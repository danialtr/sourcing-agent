from nurse_sourcer.extractors.contact import extract_contacts
from nurse_sourcer.extractors.identity import make_identity_key
from nurse_sourcer.extractors.signals import extract_signals


def test_extract_contacts_finds_email_and_handles():
    text = "Reach me at maria.santos@example.com or twitter.com/maria. linkedin.com/in/msantos"
    contacts = extract_contacts(text, "https://reddit.com/u/maria_s")
    assert "maria.santos@example.com" in contacts.emails
    assert "msantos" in contacts.linkedin_handles
    assert "maria_s" in contacts.reddit_usernames


def test_signals_detect_keywords():
    text = "I am a Filipino ICU nurse with 5 years of experience, currently studying B1 German for the Triple Win program."
    s = extract_signals(text)
    assert s.is_nurse
    assert s.is_filipino
    assert s.mentions_icu
    assert s.mentions_language
    assert s.mentions_program
    assert s.years_experience == 5


def test_identity_key_prefers_email():
    k = make_identity_key(email="A@B.com", handle="abc", name="A B")
    assert k == "email:a@b.com"


def test_identity_key_falls_back_to_name():
    k = make_identity_key(name="  Maria   Cristina  ")
    assert k == "name:maria cristina"
