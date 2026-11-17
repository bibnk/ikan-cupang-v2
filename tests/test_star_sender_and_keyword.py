"""The three bugs that made sender='*' + keyword return nothing.

Reproduced against a mock IMAP connection so the assertions describe behaviour,
not whatever a real provider happens to return.

1. FROM "*" sent literally. IMAP has no wildcard in the FROM key, so the server
   matches nothing, email_ids stays empty and a healthy login is filed as
   noemail. "*" must mean "no sender constraint".
2. `sorted_ids = sorted(...)[-20:]` over the merged set. Subject hits were
   discarded whenever 20 newer messages existed from other senders.
3. The keep-gate OR'd sender_matches(), which is always True for "*", so a
   keyword job kept every fetched message and the keyword filter did nothing.
"""
import email
import sys
from unittest.mock import MagicMock, patch

sys.path.insert(0, "C:/Users/Administrator/ikan cupang v2")
import imap_engine  # noqa: E402
from imap_engine import ImapChecker  # noqa: E402


def build_message(uid, from_addr, subject):
    return email.message_from_string(
        f"From: {from_addr}\r\nSubject: {subject}\r\nDate: Sat, 04 Jan 2025 10:00:00 +0000\r\n\r\nbody"
    ).as_bytes()


def make_conn(uid_map, from_search, subject_search):
    """uid_map: {uid_int: (from_addr, subject)}. Search responses are byte UIDs."""
    conn = MagicMock()

    def search(charset, criteria):
        if "SUBJECT" in criteria:
            return ("OK", [b" ".join(str(u).encode() for u in subject_search(criteria))])
        if "FROM" in criteria:
            return ("OK", [b" ".join(str(u).encode() for u in from_search(criteria))])
        if criteria.strip() == "ALL":
            return ("OK", [b" ".join(str(u).encode() for u in sorted(uid_map))])
        return ("OK", [b""])

    conn.search.side_effect = search

    def fetch(eid, spec):
        uid = int(eid)
        from_addr, subject = uid_map[uid]
        return ("OK", [(b"1 (BODY[] {0}", build_message(uid, from_addr, subject)), b")"])

    conn.fetch.side_effect = fetch
    return conn


def run_case(name, uid_map, target_senders, keywords, from_search, subject_search, expect_subjects):
    checker = ImapChecker(
        job_id="t", results_dir="C:/Users/Administrator/AppData/Local/hermes/cache/scratch/_t",
        accounts=[], target_senders=target_senders, keywords=keywords,
    )
    conn = make_conn(uid_map, from_search, subject_search)
    with patch.object(imap_engine, "imap_via_proxy", return_value=conn), \
         patch.object(ImapChecker, "_try_imap_variants", return_value=None), \
         patch.object(ImapChecker, "_update_imap_config", lambda *a, **k: None), \
         patch.object(imap_engine, "lookup_imap_config",
                      return_value={"server": "imap.x", "port": 993}), \
         patch.object(checker, "_write_live") as wl, \
         patch.object(checker, "_write_noemail") as wn:
        checker.unreg_domains = set()
        checker.unusable_domains = set()
        checker.skip_domain_keywords = []
        import queue as _q
        q = _q.Queue()
        q.put({"email": "u@x.com", "password": "p"})
        checker._worker(q)

    got = []
    if wl.called:
        got = [e["subject"] for e in wl.call_args.args[2]]
    status = "live" if wl.called else ("noemail" if wn.called else "other")
    ok = (status == "live" and sorted(got) == sorted(expect_subjects))
    print(f"  [{'PASS' if ok else 'FAIL'}] {name}")
    if not ok:
        print(f"        status={status} got={got} want={expect_subjects}")
    return ok


def test_star_sender_and_keyword():
    results = []


# 1. "*" with no keyword: mailbox should be enumerated, message kept.
assert run_case(
    'sender="*", no keyword -> ALL is enumerated, message is live',
    {1: ("a@b.com", "Hello")}, ["*"], [],
    from_search=lambda c: [], subject_search=lambda c: [],
    expect_subjects=["Hello"],
)

# 2. "*" + keyword: only the subject match survives (gate must not be bypassed).
assert run_case(
    'sender="*", keyword="hotels.com" -> only the matching subject is kept',
    {1: ("spam@x.com", "Buy now"), 2: ("no@reply.com", "hotels.com booking")},
    ["*"], ["hotels.com"],
    from_search=lambda c: [], subject_search=lambda c: [2],
    expect_subjects=["hotels.com booking"],
)

# 3. keyword hit must survive 30 newer unrelated messages (the [-20:] bug).
many = {u: ("other@x.com", f"unrelated {u}") for u in range(100, 130)}
many[1] = ("no@reply.com", "hotels.com booking")
assert run_case(
    "keyword hit survives 30 newer unrelated messages",
    many, ["*"], ["hotels.com"],
    from_search=lambda c: [], subject_search=lambda c: [1],
    expect_subjects=["hotels.com booking"],
)

# 4. explicit sender still filters by sender (regression guard).
assert run_case(
    "explicit sender still filters by FROM",
    {1: ("noreply@booking.com", "Confirmation"), 2: ("spam@x.com", "Junk")},
    ["@booking.com"], [],
    from_search=lambda c: [1], subject_search=lambda c: [],
    expect_subjects=["Confirmation"],
)

