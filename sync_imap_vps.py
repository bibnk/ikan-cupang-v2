#!/usr/bin/env python3
"""
Sync IMAP Master Database & Unusable Domains List across Local & all VPS Nodes.
Two-way synchronization:
1. Pull new resolved domains AND unusable/dead domains from each VPS into Local Master.
2. Push unified Master databases back to any VPS that is behind.
3. Reload/restart services safely.
"""

import os
import sys
import time
import json
import gzip
import shutil
from concurrent.futures import ThreadPoolExecutor

try:
    import paramiko
except ImportError:
    print("[!] paramiko not installed. Installing...")
    import subprocess
    subprocess.check_call([sys.executable, "-m", "pip", "install", "paramiko", "-q"])
    import paramiko

# Paths
LOCAL_MASTER_DIR = os.path.join(os.path.expanduser("~"), "Desktop", "PMJ", "imap")
LOCAL_MASTER_CONFIG = os.path.join(LOCAL_MASTER_DIR, "imap_config.json")
LOCAL_MASTER_UNUSABLE = os.path.join(LOCAL_MASTER_DIR, "imap_unusable.json")
LOCAL_MASTER_EXCLUSION = os.path.join(LOCAL_MASTER_DIR, "subject_exclusion_list.json")

REPO_DIR = os.path.join(os.path.expanduser("~"), "ikan cupang v1")
REPO_CONFIG = os.path.join(REPO_DIR, "imap_config.json")
REPO_UNUSABLE = os.path.join(REPO_DIR, "imap_unusable.json")
REPO_EXCLUSION = os.path.join(REPO_DIR, "subject_exclusion_list.json")

NODES_CONFIG_PATH = os.path.join(LOCAL_MASTER_DIR, "vps_nodes.json")

REMOTE_CONFIG_PATHS = [
    "/opt/pmj/imap/imap_config.json",
    "/opt/imap-checker/imap_config.json",
]
REMOTE_SUCCESS_PATHS = [
    "/opt/pmj/imap/imap_success.json",
    "/opt/imap-checker/imap_success.json",
]
REMOTE_UNUSABLE_PATHS = [
    "/opt/pmj/imap/imap_unusable.json",
    "/opt/imap-checker/imap_unusable.json",
]
REMOTE_EXCLUSION_PATHS = [
    "/opt/pmj/imap/subject_exclusion_list.json",
    "/opt/imap-checker/subject_exclusion_list.json",
]
LOCAL_MASTER_ALREADY = os.path.join(LOCAL_MASTER_DIR, "already_accounts.txt")
REMOTE_ALREADY_PATHS = [
    "/opt/pmj/imap/already_accounts.txt",
    "/opt/imap-checker/already_accounts.txt",
]
REMOTE_NODES_PATHS = [
    "/opt/pmj/imap/vps_nodes.json",
    "/opt/imap-checker/vps_nodes.json",
]


def normalize_already_key(line: str) -> str:
    """Canonical key for already_accounts entries: lowercase email, verbatim password.

    Passwords are case-sensitive, so only the email half may be folded. Folding
    the whole line (the previous behaviour) rewrote every mixed-case password in
    lowercase as soon as a sync cycle wrote its in-memory set back to disk, and
    the corrupted record then failed login on every later run.
    """
    s = line.strip()
    if not s:
        return ""
    if ":" in s:
        em, pw = s.split(":", 1)
        return f"{em.strip().lower()}:{pw.strip()}"
    return s.lower()


def _local_already_needs_rewrite(keys) -> bool:
    """True when the on-disk local master is not already in canonical form.

    Catches both a stray CRLF and any content drift, so a bad local file is
    repaired on every sync instead of only when a node happens to send new
    records.
    """
    if not os.path.exists(LOCAL_MASTER_ALREADY):
        return False
    try:
        with open(LOCAL_MASTER_ALREADY, "rb") as f:
            raw = f.read()
    except Exception:
        return False
    if b"\r" in raw:
        return True
    canonical = ("\n".join(sorted(k for k in keys if k)) + "\n").encode("utf-8")
    return raw != canonical


DISALLOWED_KEYWORDS = [
    "google", "gmail", "googlemail", "aspmx",
    "outlook", "office365", "microsoft", "hotmail", "live.com", "msn.com",
    "zoho", "icloud", "apple", "yahoo", "ymail", "yahoodns",
    "protonmail", "tutanota", "fastmail",
    "cloudflare", "mimecast", "spamexperts", "barracuda", "proofpoint",
    "trendmicro", "mailinblack", "hornetsecurity", "ironport",
    "generic-isp", "breakthur", "b5z.net"
]

DEFAULT_NODES = [
    {"name": "VPS-178 (Primary Checker)", "host": "178.128.0.98", "port": 22, "user": "root", "pass": "Jembut!23Je"},
    {"name": "VPS-166 (Worker)", "host": "166.88.227.2", "port": 22, "user": "root", "pass": "Jembut!23"},
]


def load_nodes():
    candidates = [
        "/opt/pmj/imap/vps_nodes.json",
        "/opt/imap-checker/vps_nodes.json",
        NODES_CONFIG_PATH,
        os.path.join(REPO_DIR, "vps_nodes.json"),
        os.path.join(os.path.dirname(__file__), "vps_nodes.json"),
    ]
    for p in candidates:
        if os.path.exists(p):
            try:
                with open(p, "r", encoding="utf-8") as f:
                    data = json.load(f)
                    if isinstance(data, list) and data:
                        return data
            except Exception as e:
                print(f"[!] Error reading {p}: {e}")
    if os.path.exists(os.path.dirname(NODES_CONFIG_PATH)):
        with open(NODES_CONFIG_PATH, "w", encoding="utf-8") as f:
            json.dump(DEFAULT_NODES, f, indent=2)
    return DEFAULT_NODES


def load_local_json(path, fallback_path=None, default=None):
    if default is None:
        default = {}
    for p in [path, fallback_path]:
        if p and os.path.exists(p):
            try:
                with open(p, "r", encoding="utf-8") as f:
                    return json.load(f)
            except Exception:
                pass
    return default


def save_atomic_json(path, data, fallback_path=None):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2, ensure_ascii=False)
        f.flush()
        os.fsync(f.fileno())
    os.replace(tmp, path)
    if fallback_path and os.path.exists(os.path.dirname(fallback_path)):
        try:
            shutil.copy(path, fallback_path)
        except Exception:
            pass


def remote_read_compressed(ssh, path):
    try:
        stdin, stdout, stderr = ssh.exec_command(f"test -f {path} && gzip -c {path} || true")
        raw = stdout.read()
        if raw:
            try:
                return gzip.decompress(raw)
            except Exception:
                return raw
    except Exception:
        pass
    return None


def remote_write_compressed(ssh, path, content_bytes):
    try:
        compressed = gzip.compress(content_bytes)
        cmd = f"mkdir -p $(dirname {path}) && gzip -d > {path}.tmp && mv {path}.tmp {path}"
        stdin, stdout, stderr = ssh.exec_command(cmd)
        stdin.write(compressed)
        stdin.close()
        return stdout.channel.recv_exit_status() == 0
    except Exception:
        return False


def fetch_node_data(node):
    host = node["host"]
    user = node.get("user", "root")
    password = node.get("pass", "")
    port = node.get("port", 22)
    name = node.get("name", host)

    result = {
        "node": node,
        "name": name,
        "host": host,
        "remote_configs": {},
        "remote_unusable": set(),
        "remote_already": set(),
        "is_busy": False,
        "error": None
    }

    try:
        ssh = paramiko.SSHClient()
        ssh.set_missing_host_key_policy(paramiko.AutoAddPolicy())
        ssh.connect(host, port=port, username=user, password=password, timeout=12)

        # 1. Fetch imap_config.json
        for rp in REMOTE_CONFIG_PATHS:
            data_bytes = remote_read_compressed(ssh, rp)
            if data_bytes:
                try:
                    data = json.loads(data_bytes.decode("utf-8", errors="replace"))
                    if data and isinstance(data, dict):
                        result["remote_configs"].update(data)
                        break
                except Exception:
                    pass

        # 1b. Fetch imap_success.json (if present, merge verified domains)
        for rp in REMOTE_SUCCESS_PATHS:
            data_bytes = remote_read_compressed(ssh, rp)
            if data_bytes:
                try:
                    data = json.loads(data_bytes.decode("utf-8", errors="replace"))
                    if data and isinstance(data, dict):
                        for dom, cfg in data.items():
                            if dom not in result["remote_configs"] and isinstance(cfg, dict):
                                result["remote_configs"][dom] = cfg
                        break
                except Exception:
                    pass

        # 2. Fetch imap_unusable.json
        for rp in REMOTE_UNUSABLE_PATHS:
            data_bytes = remote_read_compressed(ssh, rp)
            if data_bytes:
                try:
                    data = json.loads(data_bytes.decode("utf-8", errors="replace"))
                    if data:
                        result["remote_unusable"] = set(data if isinstance(data, list) else data.keys())
                        break
                except Exception:
                    pass

        # 3. Fetch already_accounts.txt
        for rp in REMOTE_ALREADY_PATHS:
            data_bytes = remote_read_compressed(ssh, rp)
            if data_bytes:
                try:
                    lines = [normalize_already_key(l) for l in data_bytes.decode("utf-8", errors="replace").splitlines() if l.strip()]
                    result["remote_already"] = set(lines)
                    break
                except Exception:
                    pass

        # 4. Check if node is busy running a checker job
        try:
            stdin, stdout, stderr = ssh.exec_command("curl -s -m 2 http://127.0.0.1:5000/sbb/api/is-busy")
            b_out = stdout.read().decode("utf-8", errors="ignore").strip()
            result["is_busy"] = ('"busy":true' in b_out)
        except Exception:
            result["is_busy"] = False

        ssh.close()
    except Exception as e:
        result["error"] = str(e)

    return result


def push_node_data(node, master_configs, master_unusable):
    host = node["host"]
    user = node.get("user", "root")
    password = node.get("pass", "")
    port = node.get("port", 22)
    name = node.get("name", host)

    result = {"name": name, "host": host, "updated": False, "error": None}

    try:
        ssh = paramiko.SSHClient()
        ssh.set_missing_host_key_policy(paramiko.AutoAddPolicy())
        ssh.connect(host, port=port, username=user, password=password, timeout=12)

        cfg_bytes = json.dumps(master_configs, indent=2, ensure_ascii=False).encode("utf-8")
        unusable_bytes = json.dumps(sorted(list(master_unusable)), indent=2, ensure_ascii=False).encode("utf-8")

        for rp in REMOTE_CONFIG_PATHS:
            remote_write_compressed(ssh, rp, cfg_bytes)

        for rp in REMOTE_UNUSABLE_PATHS:
            remote_write_compressed(ssh, rp, unusable_bytes)

        if os.path.exists(LOCAL_MASTER_EXCLUSION):
            try:
                with open(LOCAL_MASTER_EXCLUSION, "rb") as ef:
                    excl_bytes = ef.read()
                for rp in REMOTE_EXCLUSION_PATHS:
                    remote_write_compressed(ssh, rp, excl_bytes)
            except Exception:
                pass

        if os.path.exists(LOCAL_MASTER_ALREADY):
            try:
                # Canonicalise before pushing instead of shipping the file's raw
                # bytes. A CRLF or stale local master was being copied verbatim
                # onto every node, so its line ending and content drift spread
                # cluster-wide and the per-file hash check could never converge.
                with open(LOCAL_MASTER_ALREADY, "r", encoding="utf-8", errors="replace") as af:
                    keys = {normalize_already_key(l) for l in af if l.strip()}
                keys.discard("")
                already_bytes = ("\n".join(sorted(keys)) + "\n").encode("utf-8")
                for rp in REMOTE_ALREADY_PATHS:
                    remote_write_compressed(ssh, rp, already_bytes)
            except Exception:
                pass

        if os.path.exists(NODES_CONFIG_PATH):
            try:
                with open(NODES_CONFIG_PATH, "rb") as nf:
                    nodes_bytes = nf.read()
                for rp in REMOTE_NODES_PATHS:
                    remote_write_compressed(ssh, rp, nodes_bytes)
            except Exception:
                pass

        # Check if node is actively running a checker job — wait if busy
        for _wait_step in range(15):
            try:
                stdin, stdout, stderr = ssh.exec_command("curl -s -m 2 http://127.0.0.1:5000/sbb/api/is-busy")
                b_out = stdout.read().decode("utf-8", errors="ignore").strip()
                if '"busy":true' in b_out:
                    print(f"  ⏳ [{name}] ({host}): Sedang melakukan pengecekan empass... Menunggu selesai ({_wait_step*2}s)...", flush=True)
                    time.sleep(2)
                else:
                    break
            except Exception:
                break

        # Permissions, hot-reload, and safe conditional restart
        fix_cmd = """
        if id imapchecker >/dev/null 2>&1; then
            chown -R imapchecker:imapchecker /opt/pmj/imap /opt/imap-checker 2>/dev/null || true
            chmod 664 /opt/pmj/imap/*.json /opt/pmj/imap/*.txt /opt/imap-checker/*.json /opt/imap-checker/*.txt 2>/dev/null || true
        fi
        
        # 1. Hot reload in-memory configs via internal API (zero downtime, zero thread drop)
        curl -s -X POST http://127.0.0.1:5000/sbb/api/reload-configs >/dev/null 2>&1 || true

        # 2. Check if any checker job is actively running
        IS_BUSY=$(curl -s http://127.0.0.1:5000/sbb/api/is-busy 2>/dev/null | grep -o '"busy":true' || true)

        # 3. Only restart systemctl if NO jobs are running
        if [ -z "$IS_BUSY" ]; then
            if systemctl is-active --quiet imap-checker 2>/dev/null; then
                systemctl restart imap-checker 2>/dev/null || true
            fi
        else
            echo "PROTECTED: Job is currently running on this runner. Configs hot-reloaded into memory without restarting service!"
        fi
        """
        ssh.exec_command(fix_cmd)
        ssh.close()
        result["updated"] = True
    except Exception as e:
        result["error"] = str(e)

    return result


def main():
    print("=" * 70)
    print(" 🌐 IMAP MASTER DATABASE — TWO-WAY MULTI-SERVER SYNC")
    print("=" * 70)

    nodes = load_nodes()
    print(f"[*] Loaded {len(nodes)} target node(s) from vps_nodes.json")

    local_configs = load_local_json(LOCAL_MASTER_CONFIG, REPO_CONFIG, default={})
    local_unusable_raw = load_local_json(LOCAL_MASTER_UNUSABLE, REPO_UNUSABLE, default=[])
    local_unusable = set(local_unusable_raw if isinstance(local_unusable_raw, list) else local_unusable_raw.keys())

    # Load local master already accounts.
    # KEY FORMAT: email is lowercased, password is kept verbatim — passwords are
    # case-sensitive for login, so folding them here silently corrupts the record
    # once this set is written back to disk further down.
    local_already = set()
    for ap in [LOCAL_MASTER_ALREADY, os.path.join(REPO_DIR, "already_accounts.txt"), os.path.join(os.path.dirname(__file__), "already_accounts.txt")]:
        if os.path.exists(ap):
            try:
                with open(ap, "r", encoding="utf-8", errors="replace") as af:
                    local_already.update(normalize_already_key(l) for l in af if l.strip())
                break
            except Exception:
                pass

    print(f"[*] Local Master: {len(local_configs):,} valid configs | {len(local_unusable):,} unusable/dead domains | {len(local_already):,} already accounts\n", flush=True)

    # Step 1: PULL from all nodes
    print("--- [1/3] PULLING NEW DATA FROM ALL VPS NODES ---", flush=True)
    with ThreadPoolExecutor(max_workers=len(nodes) or 1) as pool:
        fetched_results = list(pool.map(fetch_node_data, nodes))

    pulled_configs = 0
    pulled_unusable = 0
    pulled_already = 0

    for res in fetched_results:
        name = res["name"]
        host = res["host"]
        if res["error"]:
            print(f"  ❌ [{name}] ({host}): Belum ke-sync / Offline: {res['error']}", flush=True)
            continue

        r_cfg = res["remote_configs"]
        r_unusable = res["remote_unusable"]
        r_already = res.get("remote_already", set())

        new_added = 0
        for dom, cfg in r_cfg.items():
            dom = dom.lower().strip()
            if not isinstance(cfg, dict) or not cfg.get("server"):
                continue
            server = str(cfg.get("server", "")).lower()
            if any(kw in server for kw in DISALLOWED_KEYWORDS):
                if dom not in local_unusable:
                    local_unusable.add(dom)
                    pulled_unusable += 1
                continue
            if dom in local_unusable:
                continue
            if dom not in local_configs:
                local_configs[dom] = cfg
                new_added += 1
                pulled_configs += 1

        new_unusable_keys = r_unusable - local_unusable
        if new_unusable_keys:
            local_unusable.update(new_unusable_keys)
            pulled_unusable += len(new_unusable_keys)

        new_already_keys = r_already - local_already
        if new_already_keys:
            local_already.update(new_already_keys)
            pulled_already += len(new_already_keys)

        busy_tag = " [⚡ Sedang Cek]" if res.get("is_busy") else ""
        print(f"  📥 [{name}] ({host}){busy_tag}: {len(r_cfg):,} configs, {len(r_already):,} already (+{new_added} cfg, +{len(new_unusable_keys)} unusable, +{len(new_already_keys)} already)", flush=True)

    print(f"\n[*] Total new valid configs pulled: +{pulled_configs}", flush=True)
    print(f"[*] Total new unusable domains pulled: +{pulled_unusable}", flush=True)
    print(f"[*] Total new already accounts pulled: +{pulled_already}", flush=True)
    print(f"[*] Unified Local Master: {len(local_configs):,} valid | {len(local_unusable):,} unusable | {len(local_already):,} already\n", flush=True)

    # Step 2: SAVE LOCAL MASTER
    if pulled_configs > 0:
        save_atomic_json(LOCAL_MASTER_CONFIG, local_configs, REPO_CONFIG)
    if pulled_unusable > 0:
        save_atomic_json(LOCAL_MASTER_UNUSABLE, sorted(list(local_unusable)), REPO_UNUSABLE)
    # Rewrite the local already master whenever its on-disk form differs from
    # the canonical set — not only when something was pulled. A CRLF or stale
    # local file otherwise survives forever and gets copied onto every node.
    canonical_content = "\n".join(sorted(k for k in local_already if k)) + "\n"
    if pulled_already > 0 or _local_already_needs_rewrite(list(local_already)):
        already_content = canonical_content
        for ap in [LOCAL_MASTER_ALREADY, os.path.join(REPO_DIR, "already_accounts.txt"), os.path.join(os.path.dirname(__file__), "already_accounts.txt")]:
            if os.path.exists(os.path.dirname(ap)):
                try:
                    tmp = ap + ".tmp"
                    with open(tmp, "w", encoding="utf-8", newline="\n") as af:
                        af.write(already_content)
                    os.replace(tmp, ap)
                except Exception:
                    pass
    print("--- [2/3] SAVED LOCAL MASTER DATABASES ---", flush=True)

    # Step 3: PUSH UNIFIED MASTER TO NODES
    print("\n--- [3/3] PUSHING UNIFIED MASTER TO NODES ---", flush=True)
    nodes_to_update = []
    for res in fetched_results:
        if res["error"]:
            continue
        cfg_diff = len(local_configs) - len(res["remote_configs"])
        unusable_diff = len(local_unusable) - len(res["remote_unusable"])
        # Compare the sets both ways. A length difference misses the case where
        # the node holds records the master lacks (normal: a worker verifies a
        # new combo mid-run) — the count then goes negative, the node is judged
        # in-sync, and the master's own repairs never reach it.
        remote_already = res.get("remote_already", set())
        already_missing = len(local_already - remote_already)
        already_extra = len(remote_already - local_already)
        already_diff = already_missing
        if cfg_diff > 0 or unusable_diff > 0 or already_missing > 0 or already_extra > 0:
            nodes_to_update.append((res["node"], cfg_diff, unusable_diff, already_diff))
        else:
            print(f"  ✔️  [{res['name']}] Sudah 100% ke-sync (Tidak perlu update)", flush=True)

    if not nodes_to_update:
        print("  🎉 Semua VPS yang online sudah 100% ke-sync!", flush=True)
    else:
        with ThreadPoolExecutor(max_workers=len(nodes_to_update) or 1) as pool:
            push_results = list(pool.map(lambda item: push_node_data(item[0], local_configs, local_unusable), nodes_to_update))

        for (node, cdiff, udiff, adiff), pres in zip(nodes_to_update, push_results):
            name = pres["name"]
            host = pres["host"]
            if pres["updated"]:
                print(f"  📤 [{name}] ({host}): Berhasil ke-sync (+{cdiff} configs, +{udiff} unusable, +{adiff} already, service direload)", flush=True)
            else:
                print(f"  ❌ [{name}] ({host}): Belum ke-sync (Gagal push: {pres['error']})", flush=True)

    print("\n" + "=" * 70, flush=True)
    print(f" ✅ SYNC FINISHED: Master memiliki {len(local_configs):,} configs & {len(local_already):,} already accounts.", flush=True)
    print("=" * 70, flush=True)


if __name__ == "__main__":
    main()
