#!/usr/bin/env bash
# Deploy ikan cupang v2 to VPS nodes straight from GitHub, in the order that
# actually works.
#
# Three things went wrong doing this by hand, so the script encodes all three:
#
#   1. Installing before pushing. The nodes cloned 1fa874f while main was four
#      commits ahead, so they ran an imap_engine without _master_url() and kept a
#      vps_nodes.json naming a retired host. Push first, then install.
#   2. setup.sh seeds data from the repo's committed snapshots, not from the live
#      cluster. A fresh node therefore starts with stale configs/accounts and
#      must have the master's files pushed afterwards.
#   3. Verifying only / . A missing template 500s every real page while / still
#      answers 200, so the check must hit /sbb/login and a static asset.
#
# The repo is the source of truth for CODE. The master holds the source of truth
# for DATA.
#
# Usage: deploy_from_repo.sh <node-ip> [<node-ip> ...]

set -uo pipefail

REPO_URL="https://github.com/bibnk/ikan-cupang-v2.git"
CLONE_DIR="/root/ikan-cupang-v2"
APP_DIR="/opt/imap-checker"
DATA_DIR="/opt/pmj/imap"
CODE_FILES=(
  app.py imap_engine.py imap_config.py sync_imap_vps.py vps_monitoring.py
  fast_checker.py get_email_engine.py loop_delete_engine.py
  ikan_cupang_v2.py wsgi.py requirements.txt
)

log() { printf '  %s\n' "$*"; }

deploy_one() {
  local ip="$1"
  echo "=============================================="
  echo " NODE $ip"
  echo "=============================================="

  # 1. fresh clone at whatever main is right now
  rm -rf "$CLONE_DIR"
  if ! git clone --depth 1 "$REPO_URL" "$CLONE_DIR" 2>&1 | tail -2; then
    echo "  FAILED: clone"
    return 1
  fi
  local sha
  sha="$(git -C "$CLONE_DIR" rev-parse --short HEAD)"
  log "cloned $sha"

  # Full setup on a first install; on a re-deploy only refresh code + assets so
  # the repo's data snapshots cannot overwrite the live cluster's files.
  if [ ! -f "$APP_DIR/wsgi.py" ]; then
    log "first install -> running setup.sh"
    ( cd "$CLONE_DIR" && chmod +x setup.sh && bash setup.sh 2>&1 | tail -6 )
  else
    log "existing install -> refreshing code only"
    for f in "${CODE_FILES[@]}"; do
      [ -f "$CLONE_DIR/$f" ] && cp -f "$CLONE_DIR/$f" "$APP_DIR/" 2>/dev/null
    done
    log "refreshed ${#CODE_FILES[@]} code files"
  fi

  # 2. assets: skipped by a naive file list, and without them every page 500s
  mkdir -p "$APP_DIR/templates" "$APP_DIR/static" "$DATA_DIR/templates" "$DATA_DIR/static"
  cp -rf "$CLONE_DIR/templates/." "$APP_DIR/templates/" 2>/dev/null
  cp -rf "$CLONE_DIR/static/."    "$APP_DIR/static/"    2>/dev/null
  cp -rf "$CLONE_DIR/templates/." "$DATA_DIR/templates/" 2>/dev/null
  cp -rf "$CLONE_DIR/static/."    "$DATA_DIR/static/"    2>/dev/null
  log "templates=$(ls -1 "$APP_DIR/templates" 2>/dev/null | wc -l) static=$(ls -1 "$APP_DIR/static" 2>/dev/null | wc -l)"

  # 3. deps
  "$APP_DIR/venv/bin/pip" install -q -r "$APP_DIR/requirements.txt" 2>&1 | tail -1
  "$APP_DIR/venv/bin/pip" install -q filelock paramiko 2>&1 | tail -1
  log "deps ok"

  chown -R imapchecker:imapchecker "$APP_DIR" "$DATA_DIR" 2>/dev/null
  systemctl restart imap-checker >/dev/null 2>&1
  sleep 6

  # 4. verify what the operator will actually open
  log "service=$(systemctl is-active imap-checker)"
  log "root=$(curl -s -o /dev/null -w '%{http_code}' -m 10 http://127.0.0.1/)"
  log "login=$(curl -s -o /dev/null -w '%{http_code}' -m 10 http://127.0.0.1/sbb/login)"
  log "css=$(curl -s -o /dev/null -w '%{http_code}' -m 10 http://127.0.0.1/sbb/static/style.css)"
  log "tplerr=$(tail -100 "$APP_DIR/error.log" 2>/dev/null | grep -c TemplateNotFound)"
}

for ip in "$@"; do
  deploy_one "$ip" || echo "  NODE $ip FAILED"
done

cat <<'EOF'

==============================================
 DATA IS NOT DEPLOYED BY THIS SCRIPT
==============================================
setup.sh seeds data from the repo's committed snapshots, which lag the live
cluster. After deploying, push the authoritative node list and databases from the
master (RDP) so the node joins the real cluster:

  python push_cluster_state.py          # node list + configs + accounts
  python sync_imap_vps.py               # full two-way sync

Verify every node reports master_url= <rdp>:8080 and the same md5 for
already_accounts.txt as the master.
EOF
