#!/usr/bin/env python3
"""
VPS Monitoring and Node Management Helper.
Probes VPS health, manages vps_nodes.json, and executes synchronization.
"""

import os
import sys
import time
import socket
import json
from concurrent.futures import ThreadPoolExecutor

try:
    import paramiko
except ImportError:
    paramiko = None


def get_nodes_file():
    candidates = [
        "/opt/pmj/imap/vps_nodes.json",
        "/opt/imap-checker/vps_nodes.json",
        os.path.join(os.path.expanduser("~"), "Desktop", "PMJ", "imap", "vps_nodes.json"),
        os.path.join(os.path.dirname(__file__), "vps_nodes.json"),
    ]
    for p in candidates:
        if os.path.exists(p):
            return p
    # Fallback to existing directory
    if os.path.exists("/opt/pmj/imap"):
        return "/opt/pmj/imap/vps_nodes.json"
    local_pmj = os.path.join(os.path.expanduser("~"), "Desktop", "PMJ", "imap")
    if os.path.exists(local_pmj):
        return os.path.join(local_pmj, "vps_nodes.json")
    return os.path.join(os.path.dirname(__file__), "vps_nodes.json")


def load_nodes():
    p = get_nodes_file()
    if os.path.exists(p):
        try:
            with open(p, "r", encoding="utf-8") as f:
                data = json.load(f)
                if isinstance(data, list):
                    return data
        except Exception:
            pass
    return []


def save_nodes(nodes_list):
    candidates = [
        "/opt/pmj/imap/vps_nodes.json",
        "/opt/imap-checker/vps_nodes.json",
        os.path.join(os.path.expanduser("~"), "Desktop", "PMJ", "imap", "vps_nodes.json"),
        os.path.join(os.path.dirname(__file__), "vps_nodes.json"),
    ]
    saved_any = False
    for p in candidates:
        pdir = os.path.dirname(p)
        if os.path.exists(pdir):
            try:
                tmp = p + ".tmp"
                with open(tmp, "w", encoding="utf-8") as f:
                    json.dump(nodes_list, f, indent=2, ensure_ascii=False)
                os.replace(tmp, p)
                saved_any = True
            except Exception:
                pass
    return saved_any


def get_master_stats():
    """Get local/master IMAP configs and unusable count."""
    config_paths = [
        "/opt/pmj/imap/imap_config.json",
        "/opt/imap-checker/imap_config.json",
        os.path.join(os.path.expanduser("~"), "Desktop", "PMJ", "imap", "imap_config.json"),
        os.path.join(os.path.dirname(__file__), "imap_config.json"),
    ]
    unusable_paths = [
        "/opt/pmj/imap/imap_unusable.json",
        "/opt/imap-checker/imap_unusable.json",
        os.path.join(os.path.expanduser("~"), "Desktop", "PMJ", "imap", "imap_unusable.json"),
        os.path.join(os.path.dirname(__file__), "imap_unusable.json"),
    ]
    exclusion_paths = [
        "/opt/pmj/imap/subject_exclusion_list.json",
        "/opt/imap-checker/subject_exclusion_list.json",
        os.path.join(os.path.expanduser("~"), "Desktop", "PMJ", "imap", "subject_exclusion_list.json"),
        os.path.join(os.path.dirname(__file__), "subject_exclusion_list.json"),
    ]
    already_paths = [
        "/opt/pmj/imap/already_accounts.txt",
        "/opt/imap-checker/already_accounts.txt",
        os.path.join(os.path.expanduser("~"), "Desktop", "PMJ", "imap", "already_accounts.txt"),
        os.path.join(os.path.dirname(__file__), "already_accounts.txt"),
    ]

    cfg_count = 0
    unusable_count = 0
    exclusion_count = 0
    already_count = 0

    for p in config_paths:
        if os.path.exists(p):
            try:
                with open(p, "r", encoding="utf-8") as f:
                    cfg_count = len(json.load(f))
                break
            except Exception:
                pass

    for p in unusable_paths:
        if os.path.exists(p):
            try:
                with open(p, "r", encoding="utf-8") as f:
                    unusable_count = len(json.load(f))
                break
            except Exception:
                pass

    for p in exclusion_paths:
        if os.path.exists(p):
            try:
                with open(p, "r", encoding="utf-8") as f:
                    exclusion_count = len(json.load(f))
                break
            except Exception:
                pass

    for p in already_paths:
        if os.path.exists(p):
            try:
                with open(p, "r", encoding="utf-8", errors="replace") as f:
                    already_count = sum(1 for l in f if l.strip())
                break
            except Exception:
                pass

    return {
        "configs_count": cfg_count,
        "unusable_count": unusable_count,
        "exclusion_count": exclusion_count,
        "already_count": already_count,
    }


def probe_single_node(node, timeout=3.5):
    """
    Probe a VPS node.
    Returns status: 'online', 'timeout', 'auth_failed', or 'error'.
    """
    host = node.get("host", "").strip()
    port = int(node.get("port", 22))
    user = node.get("user", "root").strip()
    pwd = node.get("pass", "")
    name = node.get("name") or host

    res = {
        "name": name,
        "host": host,
        "port": port,
        "user": user,
        "status": "unknown",
        "latency": None,
        "service": "unknown",
        "uptime": "-",
        "loadavg": "-",
        "mem": "-",
        "configs_count": 0,
        "unusable_count": 0,
        "error": None,
    }

    if not host:
        res["status"] = "error"
        res["error"] = "Host / IP kosong"
        return res

    t0 = time.time()
    # 1. Socket Check
    try:
        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        sock.settimeout(2.0)
        sock.connect((host, port))
        latency = round((time.time() - t0) * 1000, 1)
        sock.close()
        res["latency"] = latency
    except Exception as e:
        res["status"] = "timeout"
        res["error"] = f"Port {port} unreachable / Timeout ({str(e)})"
        return res

    if not paramiko:
        res["status"] = "online"
        res["service"] = "paramiko-missing"
        return res

    # 2. SSH Probe
    ssh = paramiko.SSHClient()
    ssh.set_missing_host_key_policy(paramiko.AutoAddPolicy())
    try:
        ssh.connect(host, port=port, username=user, password=pwd, timeout=timeout)
        cmd = """
        uptime -p 2>/dev/null || uptime;
        echo "---SEP---"
        systemctl is-active imap-checker 2>/dev/null || echo "inactive";
        echo "---SEP---"
        cat /proc/loadavg 2>/dev/null | awk '{print $1, $2, $3}';
        echo "---SEP---"
        free -m 2>/dev/null | awk '/Mem:/ {printf "%.1f/%.1f GB", $3/1024, $2/1024}';
        echo "---SEP---"
        python3 -c "import json, os; print(len(json.load(open('/opt/pmj/imap/imap_config.json'))) if os.path.exists('/opt/pmj/imap/imap_config.json') else (len(json.load(open('/opt/imap-checker/imap_config.json'))) if os.path.exists('/opt/imap-checker/imap_config.json') else 0))" 2>/dev/null || echo 0;
        echo "---SEP---"
        python3 -c "import json, os; print(len(json.load(open('/opt/pmj/imap/imap_unusable.json'))) if os.path.exists('/opt/pmj/imap/imap_unusable.json') else (len(json.load(open('/opt/imap-checker/imap_unusable.json'))) if os.path.exists('/opt/imap-checker/imap_unusable.json') else 0))" 2>/dev/null || echo 0;
        echo "---SEP---"
        python3 -c "import os; p='/opt/imap-checker/already_accounts.txt'; p2='/opt/pmj/imap/already_accounts.txt'; target = p if os.path.exists(p) else (p2 if os.path.exists(p2) else None); print(sum(1 for _ in open(target, 'r', errors='ignore')) if target else 0)" 2>/dev/null || echo 0;
        echo "---SEP---"
        curl -s -m 2 http://127.0.0.1:5000/sbb/api/is-busy 2>/dev/null | grep -o '"busy":true' && echo "busy" || echo "idle";
        """
        stdin, stdout, stderr = ssh.exec_command(cmd, timeout=timeout)
        out = stdout.read().decode("utf-8", errors="replace").strip()
        ssh.close()

        parts = [p.strip() for p in out.split("---SEP---")]
        res["uptime"] = parts[0] if len(parts) > 0 and parts[0] else "-"
        srv_raw = parts[1] if len(parts) > 1 else "unknown"
        res["service"] = "active" if "active" in srv_raw else ("inactive" if "inactive" in srv_raw else srv_raw)
        res["loadavg"] = parts[2] if len(parts) > 2 and parts[2] else "-"
        res["mem"] = parts[3] if len(parts) > 3 and parts[3] else "-"
        try:
            res["configs_count"] = int(parts[4]) if len(parts) > 4 else 0
        except Exception:
            res["configs_count"] = 0
        try:
            res["unusable_count"] = int(parts[5]) if len(parts) > 5 else 0
        except Exception:
            res["unusable_count"] = 0
        try:
            res["already_count"] = int(parts[6]) if len(parts) > 6 else 0
        except Exception:
            res["already_count"] = 0

        is_busy_str = parts[7] if len(parts) > 7 else "idle"
        res["is_busy"] = ("busy" in is_busy_str)

        res["status"] = "online"
        return res
    except paramiko.AuthenticationException:
        res["status"] = "auth_failed"
        res["error"] = "Autentikasi SSH gagal (password/user salah)"
        return res
    except socket.timeout:
        res["status"] = "timeout"
        res["error"] = "SSH session timed out"
        return res
    except Exception as e:
        res["status"] = "error"
        res["error"] = str(e)
        return res


def probe_all_nodes():
    nodes = load_nodes()
    master = get_master_stats()
    master_cfg_count = master["configs_count"]

    with ThreadPoolExecutor(max_workers=max(len(nodes), 1)) as pool:
        results = list(pool.map(probe_single_node, nodes))

    # Add sync status comparison
    master_already_count = master.get("already_count", 0)
    for r in results:
        if r["status"] == "online":
            diff = r["configs_count"] - master_cfg_count
            diff_alr = r.get("already_count", 0) - master_already_count
            r["diff_configs"] = diff
            r["diff_already"] = diff_alr
            r["in_sync"] = (diff == 0 and diff_alr == 0)
            if r.get("is_busy"):
                r["sync_status_label"] = "Sedang Cek (Busy)"
                r["sync_status_code"] = "busy"
            elif r["in_sync"]:
                r["sync_status_label"] = "Sudah Ke-sync"
                r["sync_status_code"] = "synced"
            else:
                detail = []
                if diff != 0:
                    detail.append(f"{diff:+d} cfg")
                if diff_alr != 0:
                    detail.append(f"{diff_alr:+d} already")
                r["sync_status_label"] = f"Belum Ke-sync ({', '.join(detail)})"
                r["sync_status_code"] = "pending"
        else:
            r["diff_configs"] = None
            r["diff_already"] = None
            r["in_sync"] = False
            r["sync_status_label"] = "Belum Ke-sync (Offline/Timeout)"
            r["sync_status_code"] = "error"

    return {
        "success": True,
        "master": master,
        "total_nodes": len(nodes),
        "online_count": sum(1 for r in results if r["status"] == "online"),
        "timeout_count": sum(1 for r in results if r["status"] == "timeout"),
        "error_count": sum(1 for r in results if r["status"] in ["auth_failed", "error"]),
        "nodes": results,
    }


def deploy_and_sync_node(node):
    """
    Upload essential config files and restart service on node.
    """
    if not paramiko:
        return False, "Paramiko not installed"

    host = node.get("host")
    port = int(node.get("port", 22))
    user = node.get("user", "root")
    pwd = node.get("pass", "")

    # Locate source files
    config_src = None
    unusable_src = None
    exclusion_src = None
    nodes_src = get_nodes_file()

    for p in [
        "/opt/pmj/imap/imap_config.json",
        "/opt/imap-checker/imap_config.json",
        os.path.join(os.path.expanduser("~"), "Desktop", "PMJ", "imap", "imap_config.json"),
        os.path.join(os.path.dirname(__file__), "imap_config.json"),
    ]:
        if os.path.exists(p):
            config_src = p
            break

    for p in [
        "/opt/pmj/imap/imap_unusable.json",
        "/opt/imap-checker/imap_unusable.json",
        os.path.join(os.path.expanduser("~"), "Desktop", "PMJ", "imap", "imap_unusable.json"),
        os.path.join(os.path.dirname(__file__), "imap_unusable.json"),
    ]:
        if os.path.exists(p):
            unusable_src = p
            break

    for p in [
        "/opt/pmj/imap/subject_exclusion_list.json",
        "/opt/imap-checker/subject_exclusion_list.json",
        os.path.join(os.path.expanduser("~"), "Desktop", "PMJ", "imap", "subject_exclusion_list.json"),
        os.path.join(os.path.dirname(__file__), "subject_exclusion_list.json"),
    ]:
        if os.path.exists(p):
            exclusion_src = p
            break

    already_src = None
    for p in [
        "/opt/pmj/imap/already_accounts.txt",
        "/opt/imap-checker/already_accounts.txt",
        os.path.join(os.path.expanduser("~"), "Desktop", "PMJ", "imap", "already_accounts.txt"),
        os.path.join(os.path.dirname(__file__), "already_accounts.txt"),
    ]:
        if os.path.exists(p):
            already_src = p
            break

    ssh = paramiko.SSHClient()
    ssh.set_missing_host_key_policy(paramiko.AutoAddPolicy())
    try:
        ssh.connect(host, port=port, username=user, password=pwd, timeout=10.0)
        sftp = ssh.open_sftp()

        ssh.exec_command("mkdir -p /opt/pmj/imap /opt/imap-checker")

        # Upload files
        uploads = []
        if config_src:
            uploads.extend([
                (config_src, "/opt/pmj/imap/imap_config.json"),
                (config_src, "/opt/imap-checker/imap_config.json"),
            ])
        if unusable_src:
            uploads.extend([
                (unusable_src, "/opt/pmj/imap/imap_unusable.json"),
                (unusable_src, "/opt/imap-checker/imap_unusable.json"),
            ])
        if exclusion_src:
            uploads.extend([
                (exclusion_src, "/opt/pmj/imap/subject_exclusion_list.json"),
                (exclusion_src, "/opt/imap-checker/subject_exclusion_list.json"),
            ])
        if already_src:
            uploads.extend([
                (already_src, "/opt/pmj/imap/already_accounts.txt"),
                (already_src, "/opt/imap-checker/already_accounts.txt"),
            ])
        if nodes_src and os.path.exists(nodes_src):
            uploads.extend([
                (nodes_src, "/opt/pmj/imap/vps_nodes.json"),
                (nodes_src, "/opt/imap-checker/vps_nodes.json"),
            ])

        for src, dst in uploads:
            try:
                sftp.put(src, dst)
            except Exception:
                pass

        sftp.close()

        # Check if node is busy checking — wait if busy
        for _wait_step in range(10):
            try:
                stdin, stdout, stderr = ssh.exec_command("curl -s -m 2 http://127.0.0.1:5000/sbb/api/is-busy")
                b_out = stdout.read().decode("utf-8", errors="ignore").strip()
                if '"busy":true' in b_out:
                    time.sleep(2)
                else:
                    break
            except Exception:
                break

        # Fix ownership and restart service if installed
        cmd = """
        if id imapchecker >/dev/null 2>&1; then
            chown -R imapchecker:imapchecker /opt/pmj/imap /opt/imap-checker 2>/dev/null || true
            chmod 664 /opt/pmj/imap/*.json /opt/pmj/imap/*.txt /opt/imap-checker/*.json /opt/imap-checker/*.txt 2>/dev/null || true
        fi
        curl -s -X POST http://127.0.0.1:5000/sbb/api/reload-configs >/dev/null 2>&1 || true
        IS_BUSY=$(curl -s http://127.0.0.1:5000/sbb/api/is-busy 2>/dev/null | grep -o '"busy":true' || true)
        if [ -z "$IS_BUSY" ]; then
            if systemctl is-active --quiet imap-checker 2>/dev/null; then
                systemctl restart imap-checker
            fi
            echo "RESTARTED"
        else
            echo "HOT_RELOAD_ONLY"
        fi
        """
        stdin, stdout, stderr = ssh.exec_command(cmd)
        cmd_out = stdout.read().decode("utf-8", errors="ignore").strip()
        ssh.close()
        if "HOT_RELOAD_ONLY" in cmd_out:
            return True, "Node disinkronkan & di-hot reload aman (job checking tetap berjalan tanpa restart)"
        return True, "Node berhasil disinkronkan & service direfresh"
    except Exception as e:
        return False, str(e)


def normalize_ingest_key(line):
    """Canonical already key for the master ingest path: lowercase email, verbatim password.

    The password must survive untouched — it is what LOGIN sends, and folding it
    here silently rewrote every mixed-case record arriving from a worker.
    """
    s = (line or "").strip()
    if not s or s.startswith("#"):
        return ""
    if ":" in s:
        em, pw = s.split(":", 1)
        return f"{em.strip().lower()}:{pw.strip()}"
    return s.lower()


def record_already_combos_master(combos):
    """
    Append unique combos to Master already_accounts.txt across known locations.
    Thread-safe and deduplicated.
    """
    if not combos:
        return 0
    clean = set()
    for c in combos:
        if isinstance(c, str):
            key = normalize_ingest_key(c)
            if key and ":" in key:
                clean.add(key)
    if not clean:
        return 0

    # Serialize writers: concurrent pushes (gunicorn threads) each read the file,
    # find the same combo "new", and both append -> duplicates. A file lock makes
    # read-check-append atomic across processes. And compare against existing
    # keys normalised the SAME way as incoming keys (normalize_ingest_key folds
    # only the email, keeping the password verbatim); .lower() on whole lines
    # made mixed-case passwords never match, so a combo could be appended twice.
    from filelock import FileLock

    targets = [
        "/opt/pmj/imap/already_accounts.txt",
        "/opt/imap-checker/already_accounts.txt",
        os.path.join(os.path.expanduser("~"), "Desktop", "PMJ", "imap", "already_accounts.txt"),
        os.path.join(os.path.dirname(__file__), "already_accounts.txt"),
    ]
    added = 0
    for t in targets:
        try:
            os.makedirs(os.path.dirname(t), exist_ok=True)
            with FileLock(t + ".ingest.lock", timeout=10):
                existing = set()
                if os.path.exists(t):
                    try:
                        with open(t, "r", encoding="utf-8", errors="replace") as f:
                            existing = set(normalize_ingest_key(l) for l in f if l.strip())
                    except Exception:
                        existing = set()
                new_items = clean - existing
                if not new_items:
                    continue
                # newline="\n": identical content must hash identically on every
                # node, otherwise sync's in-sync check compares unequal files.
                with open(t, "a", encoding="utf-8", newline="\n") as f:
                    for item in new_items:
                        f.write(item + "\n")
                added = max(added, len(new_items))
        except Exception:
            pass
    return added


def stream_sync_all():
    """
    Generator yielding real-time step-by-step progress lines for SSE web streaming.
    """
    import subprocess
    candidates = [
        os.path.join(os.path.dirname(__file__), "sync_imap_vps.py"),
        os.path.join(os.path.expanduser("~"), "Desktop", "sync_imap_vps.py"),
        "/opt/imap-checker/sync_imap_vps.py",
    ]
    script = None
    for p in candidates:
        if os.path.exists(p):
            script = p
            break
    if not script:
        yield "data: " + json.dumps({"type": "error", "message": "sync_imap_vps.py tidak ditemukan"}) + "\n\n"
        return

    proc = subprocess.Popen(
        [sys.executable, "-u", script],
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        encoding="utf-8",
        errors="replace",
        bufsize=1
    )
    if proc.stdout:
        for line in iter(proc.stdout.readline, ""):
            line_str = line.strip()
            if not line_str:
                continue

            ev_type = "log"
            if "Berhasil ke-sync" in line_str or "Sudah 100% ke-sync" in line_str or "already 100% in sync" in line_str:
                ev_type = "synced"
            elif "Belum ke-sync" in line_str or "Offline" in line_str or "Gagal" in line_str or "❌" in line_str:
                ev_type = "pending"
            elif "Sedang melakukan pengecekan" in line_str or "Sedang Cek" in line_str or "⏳" in line_str:
                ev_type = "busy"
            elif "PULLING" in line_str or "PUSHING" in line_str or "SAVED" in line_str:
                ev_type = "step"
            elif "SYNC FINISHED" in line_str:
                ev_type = "done"

            yield "data: " + json.dumps({"type": ev_type, "message": line_str}) + "\n\n"
        proc.stdout.close()
    proc.wait()
    yield "data: " + json.dumps({"type": "complete", "message": "Proses sinkronisasi klaster selesai!"}) + "\n\n"


def restart_node_service(node):
    if not paramiko:
        return False, "Paramiko not installed"
    host = node.get("host")
    port = int(node.get("port", 22))
    user = node.get("user", "root")
    pwd = node.get("pass", "")

    ssh = paramiko.SSHClient()
    ssh.set_missing_host_key_policy(paramiko.AutoAddPolicy())
    try:
        ssh.connect(host, port=port, username=user, password=pwd, timeout=8.0)
        stdin, stdout, stderr = ssh.exec_command("""
        systemctl restart imap-checker
        sleep 1
        systemctl is-active imap-checker
        """)
        status = stdout.read().decode("utf-8").strip()
        ssh.close()
        return True, status
    except Exception as e:
        return False, str(e)


def run_sync_all():
    import subprocess
    candidates = [
        os.path.join(os.path.dirname(__file__), "sync_imap_vps.py"),
        os.path.join(os.path.expanduser("~"), "Desktop", "sync_imap_vps.py"),
        "/opt/imap-checker/sync_imap_vps.py",
    ]
    script = None
    for p in candidates:
        if os.path.exists(p):
            script = p
            break
    if not script:
        return False, "sync_imap_vps.py tidak ditemukan"

    try:
        proc = subprocess.run(
            [sys.executable, script],
            capture_output=True,
            text=True,
            timeout=300
        )
        output = proc.stdout
        if proc.stderr:
            output += "\n" + proc.stderr
        return True, output.strip()
    except subprocess.TimeoutExpired:
        return False, "Sync process timed out (> 300 detik)"
    except Exception as e:
        return False, str(e)

