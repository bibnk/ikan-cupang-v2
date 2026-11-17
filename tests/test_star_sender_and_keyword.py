"""Sender sentinels and subject-only search.

Each case drives the real worker against a mock IMAP connection, so the
assertions describe behaviour rather than whatever a provider happens to return.

The bugs being pinned down:
  1. FROM "*" sent literally -- IMAP has no wildcard in the FROM key, so the
     server matched a literal asterisk, email_ids stayed empty, and a healthy
     login was filed as noemail.
  2. The newest-20 slice over the merged id set discarded a subject hit whenever
     20 newer messages existed from other senders.
  3. The keep-gate OR'd sender_matches(), which is always True for "*", so a
     keyword job kept every fetched message and the SUBJECT filter did nothing.
  4. SUBJECT searches were narrowed by SINCE even in subject-only mode, so an old
     match looked exactly like "this domain cannot search subject".
"""
import email
import queue
import sys
from unittest.mock import MagicMock, patch

import pytest

sys.path.insert(0, "C:/Users/Administrator/ikan cupang v2")
import imap_engine  # noqa: E402
from imap_engine import ImapChecker  # noqa: E402

RESULTS_DIR = "C:/Users/Administrator/AppData/Local/hermes/cache/scratch/_star_tests"


def _build_message(uid, from_addr, subject):
    return email.message_from_string(
        f"From: {from_addr}\r\n"
        f"Subject: {subject}\r\n"
        f"Date: Sat, 04 Jan 2025 10:00:00 +0000\r\n\r\nbody"
    ).as_bytes()


def _make_conn(uid_map, from_uids, subject_uids):
    conn = MagicMock()

    def search(charset, criteria):
        if "SUBJECT" in criteria:
            uids = subject_uids
        elif "FROM" in criteria:
            uids = from_uids
        else:
            uids = sorted(uid_map)
        return ("OK", [b" ".join(str(u).encode() for u in uids)])

    conn.search.side_effect = search

    def fetch(eid, spec):
        uid = int(eid)
        from_addr, subject = uid_map[uid]
        return ("OK", [(b"1 (BODY[] {0}", _build_message(uid, from_addr, subject)), b")"])

    conn.fetch.side_effect = fetch
    return conn


def _run(uid_map, target_senders, keywords, from_uids, subject_uids):
    """Run the worker and report (status, subjects_out, search_criteria)."""
    checker = ImapChecker(
        job_id="t", results_dir=RESULTS_DIR, accounts=[],
        target_senders=list(target_senders), keywords=list(keywords),
    )
    conn = _make_conn(uid_map, from_uids, subject_uids)
    with patch.object(imap_engine, "imap_via_proxy", return_value=conn), \
         patch.object(ImapChecker, "_try_imap_variants", return_value=None), \
         patch.object(ImapChecker, "_update_imap_config", lambda *a, **k: None), \
         patch.object(imap_engine, "lookup_imap_config",
                      return_value={"server": "imap.x", "port": 993}), \
         patch.object(checker, "_write_live") as live, \
         patch.object(checker, "_write_noemail") as noemail:
        checker.unreg_domains = set()
        checker.unusable_domains = set()
        checker.skip_domain_keywords = []
        q = queue.Queue()
        q.put({"email": "u@x.com", "password": "p"})
        checker._worker(q)

    criteria = [c.args[1] for c in conn.search.call_args_list]
    if live.called:
        return "live", [e["subject"] for e in live.call_args.args[2]], criteria
    if noemail.called:
        return "noemail", [], criteria
    return "other", [], criteria


def test_star_alone_enumerates_mailbox():
    """'*' with no keyword: no FROM search at all, whole box is live."""
    status, subs, criteria = _run({1: ("a@b.com", "Hello")}, ["*"], [], [], [])
    assert status == "live"
    assert subs == ["Hello"]
    assert not any("FROM" in c for c in criteria), criteria
    assert any("ALL" in c for c in criteria), criteria


def test_star_with_keyword_keeps_only_subject_match():
    """Keyword job must not keep unrelated messages just because '*' matches."""
    status, subs, _ = _run(
        {1: ("spam@x.com", "Buy now"), 2: ("no@reply.com", "hotels.com booking")},
        ["*"], ["hotels.com"], [], [2],
    )
    assert status == "live"
    assert subs == ["hotels.com booking"]


def test_dash_is_subject_only_too():
    """'-' is the documented alternative to '*'."""
    status, subs, criteria = _run(
        {1: ("spam@x.com", "Buy now"), 2: ("no@reply.com", "hotels.com booking")},
        ["-"], ["hotels.com"], [], [2],
    )
    assert status == "live"
    assert subs == ["hotels.com booking"]
    assert not any("FROM" in c for c in criteria), criteria


def test_subject_hit_survives_newer_unrelated_mail():
    """The [-20:] slice used to discard the match."""
    uid_map = {u: ("other@x.com", f"unrelated {u}") for u in range(100, 130)}
    uid_map[1] = ("no@reply.com", "hotels.com booking")
    status, subs, _ = _run(uid_map, ["*"], ["hotels.com"], [], [1])
    assert status == "live"
    assert "hotels.com booking" in subs


@pytest.mark.parametrize("sentinel", ["*", "-"])
def test_subject_only_search_is_not_narrowed_by_since(sentinel):
    """An old subject match must still be found in subject-only mode."""
    status, subs, criteria = _run(
        {7: ("old@x.com", "hotels.com invoice")}, [sentinel], ["hotels.com"], [], [7],
    )
    assert status == "live"
    assert subs == ["hotels.com invoice"]
    subj_criteria = [c for c in criteria if "SUBJECT" in c]
    assert subj_criteria, criteria
    assert not any("SINCE" in c for c in subj_criteria), subj_criteria


def test_explicit_sender_still_filters_by_from():
    """Regression guard: a real sender list keeps working."""
    status, subs, criteria = _run(
        {1: ("noreply@booking.com", "Confirmation"), 2: ("spam@x.com", "Junk")},
        ["@booking.com"], [], [1], [],
    )
    assert status == "live"
    assert subs == ["Confirmation"]
    assert any("FROM" in c for c in criteria), criteria
