"""Live search (the 📨 button) must return only matching subjects.

This is the path behind /api/get-email -> fetch_emails(), which is separate from
the bulk worker. It had its own copy of the bug:

  * `is_all_search = (senders == ["*"])` meant a "-" sentinel fell through to the
    FROM branch and searched for a literal hyphen, and with "*" the keyword loop
    was inside the `else` branch so keywords were skipped entirely.
  * the post-fetch gate `not is_all_search and senders and not sender_matches(...)`
    was skipped for "*", so every message in the box came back -- the operator
    saw "all email" instead of the subject they asked for.

The assertions check the returned subjects, which is exactly what the operator
sees in the modal.
"""
import sys

import pytest

sys.path.insert(0, "C:/Users/Administrator/ikan cupang v2")
import get_email_engine as G  # noqa: E402
from get_email_engine import fetch_emails  # noqa: E402


class FakeIMAP:
    """Enough of imaplib.IMAP4_SSL for fetch_emails' read path."""

    def __init__(self, messages):
        # messages: list of (uid:int, from:str, subject:str), oldest first
        self.messages = messages
        self.searches = []

    def select(self, folder, readonly=False):
        return ("OK", [b"1"])

    def logout(self):
        return ("BYE", [])

    def uid(self, command, *args):
        if command.upper() == "SEARCH":
            criteria = args[-1].decode() if isinstance(args[-1], bytes) else args[-1]
            self.searches.append(criteria)
            hits = []
            for uid, sender, subject in self.messages:
                ok = True
                if "SUBJECT" in criteria:
                    needle = criteria.split('SUBJECT "')[1].split('"')[0].lower()
                    ok = needle in subject.lower()
                elif "FROM" in criteria:
                    needle = criteria.split('FROM "')[1].split('"')[0].lower()
                    ok = needle in sender.lower()
                if ok:
                    hits.append(str(uid).encode())
            return ("OK", [b" ".join(hits)])
        if command.upper() == "FETCH":
            uid = int(args[0])
            sender, subject = next((s, sub) for u, s, sub in self.messages if u == uid)
            raw = (f"From: {sender}\r\nSubject: {subject}\r\n"
                   f"Date: Sat, 04 Jan 2025 10:00:00 +0000\r\n\r\nbody").encode()
            return ("OK", [(f"{uid} (RFC822 {{{len(raw)}}}".encode(), raw), b")"])
        return ("OK", [b""])


MESSAGES = [
    (1, "spam@junk.test", "Buy cheap pills"),
    (2, "noreply@hotels.com", "hotels.com booking confirmed"),
    (3, "news@shop.test", "Weekly deals"),
    (4, "no-reply@hotels.com", "Your hotels.com receipt"),
]


def _run(senders, keywords, monkeypatch):
    fake = FakeIMAP(MESSAGES)
    monkeypatch.setattr(G, "get_imap_config",
                        lambda d: {"server": "imap.x", "port": 993})
    monkeypatch.setattr(G, "connect_imap", lambda e, p: (fake, None))
    out = fetch_emails("u@x.com", "p", senders=senders, keywords=keywords,
                       search_days=365, limit=20)
    return out, fake


@pytest.mark.parametrize("sentinel", ["-", "*"])
def test_subject_only_returns_matching_subjects_only(sentinel, monkeypatch):
    out, fake = _run([sentinel], ["hotels.com"], monkeypatch)
    assert out["success"], out
    subjects = [e["subject"] for e in out["emails"]]
    assert subjects, "expected the two hotels.com subjects"
    assert all("hotels.com" in s.lower() for s in subjects), subjects
    assert "Buy cheap pills" not in subjects
    assert "Weekly deals" not in subjects
    # The search must not have asked for a literal sentinel sender.
    assert not any(f'FROM "{sentinel}"' in c for c in fake.searches), fake.searches


@pytest.mark.parametrize("sentinel", ["-", "*"])
def test_subject_only_search_has_no_since(sentinel, monkeypatch):
    _, fake = _run([sentinel], ["hotels.com"], monkeypatch)
    subj = [c for c in fake.searches if "SUBJECT" in c]
    assert subj, fake.searches
    assert not any("SINCE" in c for c in subj), subj


def test_explicit_sender_still_works(monkeypatch):
    out, _ = _run(["@hotels.com"], [], monkeypatch)
    subjects = [e["subject"] for e in out["emails"]]
    assert len(subjects) == 2, subjects
    assert all("hotels" in s.lower() for s in subjects), subjects


def test_sender_and_keyword_still_union(monkeypatch):
    out, _ = _run(["@junk.test"], ["receipt"], monkeypatch)
    subjects = [e["subject"] for e in out["emails"]]
    assert "Buy cheap pills" in subjects
    assert "Your hotels.com receipt" in subjects
    assert "Weekly deals" not in subjects
