"""Anthropic client wrapper: prompt caching, retries, batch mode, cost tracking.

Per the brief:
- Sonnet 4.6 for role parsing, scoring, and outputs
- Haiku 4.5 available for any filter pass we add later
- Adaptive thinking is the only on-mode on 4.6 (budget_tokens is deprecated)
- The SDK auto-retries 429s with exponential backoff honoring `retry-after`;
  we bump `max_retries` to 4 per the brief's error-handling section
"""

from __future__ import annotations

import json
import time
from dataclasses import dataclass
from typing import Any, Optional

import anthropic

# Models per the brief
MODEL_SONNET = "claude-sonnet-4-6"
MODEL_HAIKU = "claude-haiku-4-5-20251001"

# Pricing per 1M tokens — from shared/models.md
_PRICING_PER_1M = {
    MODEL_SONNET: {"input": 3.00, "output": 15.00},
    MODEL_HAIKU: {"input": 1.00, "output": 5.00},
}

# Cache multipliers (5-min ephemeral TTL)
_CACHE_WRITE_MULT = 1.25
_CACHE_READ_MULT = 0.10

# Batch API discount
_BATCH_DISCOUNT = 0.50


@dataclass
class TokenUsage:
    """Cumulative token usage; tracks cached vs uncached separately."""

    input_tokens: int = 0
    output_tokens: int = 0
    cache_creation_input_tokens: int = 0
    cache_read_input_tokens: int = 0

    def add(self, usage: Any) -> None:
        """Add an Anthropic SDK usage object's counts."""
        self.input_tokens += getattr(usage, "input_tokens", 0) or 0
        self.output_tokens += getattr(usage, "output_tokens", 0) or 0
        self.cache_creation_input_tokens += getattr(usage, "cache_creation_input_tokens", 0) or 0
        self.cache_read_input_tokens += getattr(usage, "cache_read_input_tokens", 0) or 0

    def dollar_cost(self, model: str, batch: bool = False) -> float:
        pricing = _PRICING_PER_1M[model]
        cost = (
            self.input_tokens * pricing["input"]
            + self.output_tokens * pricing["output"]
            + self.cache_creation_input_tokens * pricing["input"] * _CACHE_WRITE_MULT
            + self.cache_read_input_tokens * pricing["input"] * _CACHE_READ_MULT
        ) / 1_000_000
        if batch:
            cost *= _BATCH_DISCOUNT
        return cost


class LLMClient:
    """Anthropic wrapper with caching helpers and a usage accumulator."""

    def __init__(self, api_key: Optional[str] = None, max_retries: int = 4) -> None:
        self.client = anthropic.Anthropic(api_key=api_key, max_retries=max_retries)
        self._usage: dict[tuple[str, bool], TokenUsage] = {}

    def _accumulate(self, model: str, usage: Any, *, batch: bool = False) -> None:
        key = (model, batch)
        if key not in self._usage:
            self._usage[key] = TokenUsage()
        self._usage[key].add(usage)

    @staticmethod
    def _system_param(system: str | list[dict], cache: bool) -> list[dict] | str:
        """Wrap a string system prompt in the list-of-blocks form so we can attach cache_control."""
        if isinstance(system, list) or not cache:
            return system
        return [{"type": "text", "text": system, "cache_control": {"type": "ephemeral"}}]

    def call_json(
        self,
        *,
        model: str = MODEL_SONNET,
        system: str | list[dict],
        messages: list[dict],
        schema: dict,
        max_tokens: int = 4096,
        cache_system: bool = True,
    ) -> dict:
        """Single call constrained to a JSON schema; returns the parsed dict."""
        response = self.client.messages.create(
            model=model,
            max_tokens=max_tokens,
            system=self._system_param(system, cache_system),
            messages=messages,
            output_config={"format": {"type": "json_schema", "schema": schema}},
        )
        self._accumulate(model, response.usage)
        for block in response.content:
            if block.type == "text":
                return json.loads(block.text)
        raise RuntimeError("No text block in JSON response")

    def call_text(
        self,
        *,
        model: str = MODEL_SONNET,
        system: str | list[dict],
        messages: list[dict],
        max_tokens: int = 4096,
        cache_system: bool = True,
    ) -> str:
        """Single call returning the first text block (no schema)."""
        response = self.client.messages.create(
            model=model,
            max_tokens=max_tokens,
            system=self._system_param(system, cache_system),
            messages=messages,
        )
        self._accumulate(model, response.usage)
        for block in response.content:
            if block.type == "text":
                return block.text
        return ""

    def batch_json(
        self,
        *,
        model: str,
        system: str | list[dict],
        schema: dict,
        items: list[tuple[str, list[dict]]],
        max_tokens: int = 1024,
        poll_interval: float = 15.0,
        cache_system: bool = True,
    ) -> dict[str, dict]:
        """Submit a JSON-schema-constrained batch, wait, return {custom_id: parsed_dict}.

        `items` is a list of (custom_id, messages) pairs. System + schema are shared.
        Failures are skipped — the caller checks for missing keys.
        """
        system_param = self._system_param(system, cache_system)

        requests = [
            {
                "custom_id": cid,
                "params": {
                    "model": model,
                    "max_tokens": max_tokens,
                    "system": system_param,
                    "messages": messages,
                    "output_config": {"format": {"type": "json_schema", "schema": schema}},
                },
            }
            for cid, messages in items
        ]

        batch = self.client.messages.batches.create(requests=requests)
        while True:
            batch = self.client.messages.batches.retrieve(batch.id)
            if batch.processing_status == "ended":
                break
            time.sleep(poll_interval)

        results: dict[str, dict] = {}
        for r in self.client.messages.batches.results(batch.id):
            if r.result.type != "succeeded":
                continue
            msg = r.result.message
            self._accumulate(model, msg.usage, batch=True)
            text = next((b.text for b in msg.content if b.type == "text"), None)
            if text is None:
                continue
            try:
                results[r.custom_id] = json.loads(text)
            except json.JSONDecodeError:
                continue
        return results

    def total_cost(self) -> float:
        return sum(
            usage.dollar_cost(model, batch=batch)
            for (model, batch), usage in self._usage.items()
        )

    def cost_summary(self) -> str:
        lines = ["Claude API usage:"]
        total = 0.0
        for (model, batch), usage in sorted(self._usage.items()):
            cost = usage.dollar_cost(model, batch=batch)
            total += cost
            mode = "batch" if batch else "live "
            lines.append(
                f"  {model} [{mode}] "
                f"in={usage.input_tokens:,} "
                f"out={usage.output_tokens:,} "
                f"cache_write={usage.cache_creation_input_tokens:,} "
                f"cache_read={usage.cache_read_input_tokens:,} "
                f"-> ${cost:.4f}"
            )
        lines.append(f"  total: ${total:.4f}")
        return "\n".join(lines)
