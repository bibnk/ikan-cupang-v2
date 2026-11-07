"""Rewrite of test_per_job_files_present_master_not_in_job_dir for the async engine.

ImapChecker.run() no longer drives ImapChecker._worker — it delegates to
FastImapEngine, which speaks IMAP over raw asyncio streams instead of imaplib.
That means stubbing imaplib exercises nothing, so the test has to feed a fake
asyncio stream reader/writer and assert the same per-job file contract.

Contract under test (unchanged):
  - every outcome file is written under jobs/{job_id}/
  - no imap_success.json is created in the job dir (it belongs to the master)
"""
import asyncio
import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import fast_checker


def _redirect_master(monkeypatch, tmp_path):
    """Point the master already/success paths at tmp so tests never touch live data."""
    import imap_engine
    master = tmp_path / "imap_success.json"
    monkeypatch.setattr(imap_engine, "MASTER_IMAP_SUCCESS_PATH", str(master))
    monkeypatch.setattr(imap_engine, "MASTER_IMAP_SUCCESS_LOCK_PATH", str(master) + ".lock")
    return master


class _FakeReader:
    """Scripted IMAP server: yields queued responses, one line per readline()."""

    def __init__(self, lines):
        self._lines = list(lines)

    async def readline(self):
        if not self._lines:
            return b""
        return self._lines.pop(0)


class _FakeWriter:
    def __init__(self):
        self.written = []
        self.closed = False

    def write(self, data):
        self.written.append(data)

    async def drain(self):
        return None

    def close(self):
        self.closed = True

    async def wait_closed(self):
        return None

    def get_extra_info(self, name, default=None):
        return default


def _scripted_conn(script):
    """Return an async factory mimicking asyncio.open_connection for one host."""
    reader = _FakeReader(script)
    writer = _FakeWriter()

    async def _open(host, port, **kw):
        return reader, writer

    return _open, writer


def test_per_job_files_present_master_not_in_job_dir(tmp_path, monkeypatch):
    _redirect_master(monkeypatch, tmp_path)

    # One account per outcome; domains avoid the blocklist so only the
    # explicitly-skipped account lands in domain_skipped.txt.
    accounts = [
        {"email": "b@nomail.test", "password": "p"},      # login ok, no email
        {"email": "c@bad.test", "password": "p"},         # login rejected -> die
        {"email": "d@ghost.test", "password": "p"},       # unknown domain -> unreg
        {"email": "e@hotmail.com", "password": "p"},      # blocklisted -> skip
    ]

    cfg = {
        "nomail.test": {"server": "imap.nomail.test", "port": 993},
        "bad.test": {"server": "imap.bad.test", "port": 993},
    }
    import imap_config
    monkeypatch.setattr(fast_checker, "load_json_file", lambda p: cfg if "config" in str(p) else [])
    monkeypatch.setattr(imap_config, "DEFAULT_IMAP_CONFIG", cfg)

    # Route each host to its scripted conversation.
    nomail_open, nomail_writer = _scripted_conn([
        b"* OK IMAP4rev1 ready\r\n",          # banner
        b"A1 OK LOGIN completed\r\n",         # login
        b"A2 OK SELECT done\r\n",             # select
        b"A3 OK SEARCH completed\r\n",        # search -> no ids
        b"A9 OK LOGOUT\r\n",
    ])
    bad_open, bad_writer = _scripted_conn([
        b"* OK IMAP4rev1 ready\r\n",
        b"A1 NO LOGIN failed\r\n",
        b"A9 OK LOGOUT\r\n",
    ])

    real_open_connection = asyncio.open_connection

    async def fake_open_connection(host, port, **kw):
        if "nomail" in str(host):
            return await nomail_open(host, port, **kw)
        if "bad" in str(host):
            return await bad_open(host, port, **kw)
        raise OSError(f"unexpected connection to {host}:{port}")

    monkeypatch.setattr(fast_checker.asyncio, "open_connection", fake_open_connection)
    monkeypatch.setattr(fast_checker.FastImapEngine, "_resolve_ip",
                        lambda self, h: _async_none(h))

    engine = fast_checker.FastImapEngine(
        accounts=accounts,
        imap_configs=cfg,
        output_dir=str(tmp_path / "jobs" / "perjob"),
        concurrency=4,
        timeout=7.0,
        target_senders=["@booking.com"],
        keywords=[],
        search_days=30,
        skip_domains=[],
        unusable_domains={"hotmail.com"},
        already_accounts=set(),
        proxies=[],
        on_progress=None,
        on_live=None,
    )
    asyncio.run(engine.run(silent=True))

    results_dir = tmp_path / "jobs" / "perjob"
    assert (results_dir / "noemail.txt").read_text(encoding="utf-8").strip() == "b@nomail.test:p"
    assert (results_dir / "die.txt").read_text(encoding="utf-8").strip() == "c@bad.test:p"
    assert (results_dir / "domain_skipped.txt").read_text(encoding="utf-8").strip() == "e@hotmail.com:p"

    # CRITICAL: no per-job master copy.
    assert not (results_dir / "imap_success.json").exists()


async def _async_none(_h):
    return None
