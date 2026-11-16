"""Install ikan cupang v2 on a fresh Ubuntu node, matching the existing cluster.

Brings up exactly what a running node has: the imapchecker user, /opt paths (both,
because run_sync_all and the checker read different ones), a venv with the pinned
deps, the systemd unit, nginx passthrough, and the current master data.

Idempotent: safe to re-run on a half-installed box.
"""
import paramiko, json, time, os, sys, hashlib

IPS = ["201.79.38.136", "165.245.138.100", "201.79.140.82", "201.79.43.236"]
PASSWORD = "Jembut!23Je"
NAMES = ["VPS-new-38.136", "VPS-new-138.100", "VPS-new-140.82", "VPS-new-43.236"]

REPO = r"C:/Users/Administrator/ikan cupang v2"
MASTER = r"C:/Users/Administrator/Desktop/PMJ/imap"

# Source files: everything the service needs at runtime.
PY_FILES = ["app.py", "imap_engine.py", "fast_checker.py", "imap_config.py",
            "get_email_engine.py", "loop_delete_engine.py", "ikan_cupang_v2.py",
            "vps_monitoring.py", "sync_imap_vps.py", "wsgi.py", "requirements.txt"]
DATA_FILES = ["imap_config.json", "imap_unusable.json", "subject_exclusion_list.json",
              "skip_domains.json", "already_accounts.txt"]
# The node list must exist so _master_host() has something to resolve.
NODES_SRC = os.path.join(REPO, "vps_nodes.json")

UNIT = """[Unit]
Description=IMAP Checker Web Application
After=network.target

[Service]
Type=exec
User=imapchecker
Group=imapchecker
WorkingDirectory=/opt/imap-checker
Environment="PATH=/opt/imap-checker/venv/bin"
LimitNOFILE=65536
LimitNPROC=8192
ExecStart=/opt/imap-checker/venv/bin/gunicorn \\
    --workers 1 \\
    --threads 32 \\
    --bind 127.0.0.1:5000 \\
    --timeout 600 \\
    --access-logfile /opt/imap-checker/access.log \\
    --error-logfile /opt/imap-checker/error.log \\
    wsgi:application
Restart=always
RestartSec=5

[Install]
WantedBy=multi-user.target
"""


def sh(c, cmd, timeout=600):
    _, so, se = c.exec_command(cmd, timeout=timeout)
    out = so.read().decode(errors="replace")
    err = se.read().decode(errors="replace")
    rc = so.channel.recv_exit_status()
    return rc, out, err


def connect(ip):
    c = paramiko.SSHClient()
    c.set_missing_host_key_policy(paramiko.AutoAddPolicy())
    c.connect(ip, port=22, username="root", password=PASSWORD, timeout=35,
              banner_timeout=55, auth_timeout=55, allow_agent=False, look_for_keys=False)
    return c


def install(ip, name):
    print(f"\n=== [{name}] {ip}", flush=True)
    c = connect(ip)

    # 1. user + dirs
    rc, out, err = sh(c, """
set -e
id imapchecker >/dev/null 2>&1 || useradd -r -m -d /home/imapchecker -s /usr/sbin/nologin imapchecker
mkdir -p /opt/imap-checker /opt/pmj/imap
id imapchecker >/dev/null 2>&1 && echo user-ok
python3 -V
""", 180)
    print("  bootstrap:", out.strip().replace("\n", " | "), err.strip()[:120])

    # 2. system deps + venv
    rc, out, err = sh(c, """
set -e
export DEBIAN_FRONTEND=noninteractive
apt-get update -qq 2>&1 | tail -1
apt-get install -y -qq python3-venv python3-pip nginx curl 2>&1 | tail -2
python3 -m venv /opt/imap-checker/venv 2>/dev/null || true
/opt/imap-checker/venv/bin/pip install -q --upgrade pip 2>&1 | tail -1
echo deps-ok
""", 900)
    print("  apt+venv:", out.strip().replace("\n", " | "), err.strip()[:150])

    # 3. upload code + data
    sftp = c.open_sftp()
    uploaded = []
    for f in PY_FILES:
        p = os.path.join(REPO, f)
        if os.path.exists(p):
            sftp.put(p, "/opt/imap-checker/" + f)
            uploaded.append(f)
    if os.path.exists(NODES_SRC):
        sftp.put(NODES_SRC, "/opt/imap-checker/vps_nodes.json")
        uploaded.append("vps_nodes.json")
    for f in DATA_FILES:
        p = os.path.join(MASTER, f)
        if not os.path.exists(p):
            p = os.path.join(REPO, f)
        if os.path.exists(p):
            sftp.put(p, "/opt/imap-checker/" + f)
            uploaded.append(f)
    sftp.close()
    print(f"  uploaded: {len(uploaded)} files")

    # 3b. templates + static: without these every page raises TemplateNotFound
    # and the whole UI 500s, while the service still reads as "active" -- which
    # is indistinguishable from a failed install. app.py serves them from
    # ./templates and ./static relative to its own directory.
    rc, out, err = sh(c, "mkdir -p /opt/imap-checker/templates /opt/imap-checker/static "
                         "/opt/pmj/imap/templates /opt/pmj/imap/static", 120)
    assets = 0
    for sub in ("templates", "static"):
        base = os.path.join(REPO, sub)
        if not os.path.isdir(base):
            continue
        sftp = c.open_sftp()
        for dirpath, _dirs, files in os.walk(base):
            for fn in files:
                src = os.path.join(dirpath, fn)
                rel = os.path.relpath(src, REPO).replace("\\", "/")
                for tgt in ("/opt/imap-checker/", "/opt/pmj/imap/"):
                    sftp.put(src, tgt + rel)
                assets += 1
        sftp.close()
    print(f"  assets: {assets} template/static files")

    # 4. requirements
    rc, out, err = sh(c, "cd /opt/imap-checker && ./venv/bin/pip install -q -r requirements.txt 2>&1 | tail -3; "
                         "./venv/bin/pip install -q filelock paramiko 2>&1 | tail -2; echo pip-done", 900)
    print("  pip:", out.strip().replace("\n", " | ")[-200:], err.strip()[:120])

    # 5. copy to the second /opt path (run_sync_all and the checker disagree)
    rc, out, err = sh(c, """
set -e
cp -f /opt/imap-checker/*.py /opt/pmj/imap/ 2>/dev/null || true
cp -f /opt/imap-checker/*.json /opt/pmj/imap/ 2>/dev/null || true
cp -f /opt/imap-checker/*.txt /opt/pmj/imap/ 2>/dev/null || true
echo mirror-ok
""", 180)
    print("  mirror:", out.strip())

    # 6. systemd unit + perms + start
    sftp = c.open_sftp()
    with sftp.file("/etc/systemd/system/imap-checker.service", "w") as f:
        f.write(UNIT)
    sftp.close()
    rc, out, err = sh(c, """
set -e
chown -R imapchecker:imapchecker /opt/imap-checker /opt/pmj/imap
chmod -R u+rw /opt/imap-checker /opt/pmj/imap
systemctl daemon-reload
systemctl enable imap-checker >/dev/null 2>&1 || true
systemctl restart imap-checker
sleep 6
echo "service=$(systemctl is-active imap-checker)"
curl -s -o /dev/null -w "http=%{http_code}\\n" http://127.0.0.1:5000/ || true
# The UI route, not just /: a missing template yields 500 here while / still
# answers 200, which is exactly how an install can look complete but be unusable.
curl -s -o /dev/null -w "login=%{http_code}\\n" http://127.0.0.1:5000/sbb/login || true
curl -s -o /dev/null -w "css=%{http_code}\\n" http://127.0.0.1:5000/sbb/static/style.css || true
echo "templates=$(ls -1 /opt/imap-checker/templates 2>/dev/null | wc -l)"
""", 240)
    print("  start:", out.strip().replace("\n", " | "), err.strip()[:150])

    # 7. verify
    rc, out, err = sh(c, """
python3 -c "import json;print('cfg',len(json.load(open('/opt/imap-checker/imap_config.json'))))"
python3 -c "import json;print('unu',len(json.load(open('/opt/imap-checker/imap_unusable.json'))))"
wc -l < /opt/imap-checker/already_accounts.txt | sed 's/^/alr /'
md5sum /opt/imap-checker/imap_config.json /opt/pmj/imap/imap_config.json | cut -c1-12
""", 180)
    print("  verify:", out.strip().replace("\n", " | "))
    c.close()
    return True


if __name__ == "__main__":
    only = sys.argv[1] if len(sys.argv) > 1 else None
    for ip, name in zip(IPS, NAMES):
        if only and only not in ip:
            continue
        try:
            install(ip, name)
        except Exception as e:
            print(f"  FAIL {name}: {type(e).__name__}: {str(e)[:120]}")
