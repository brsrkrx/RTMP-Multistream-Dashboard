#!/usr/bin/env bash
# =============================================================================
#  RTMP Control Panel — Installer
#  Supports Ubuntu 18.04, 20.04, 22.04, 24.04
# =============================================================================

set -euo pipefail

# ── Colours ───────────────────────────────────────────────────────────────────
RED='\033[0;31m'; GREEN='\033[0;32m'; YELLOW='\033[1;33m'
CYAN='\033[0;36m'; BOLD='\033[1m'; RESET='\033[0m'

info()    { echo -e "${CYAN}${BOLD}[INFO]${RESET}  $*"; }
ok()      { echo -e "${GREEN}${BOLD}[ OK ]${RESET}  $*"; }
warn()    { echo -e "${YELLOW}${BOLD}[WARN]${RESET}  $*"; }
error()   { echo -e "${RED}${BOLD}[ERR ]${RESET}  $*"; }
die()     { error "$*"; exit 1; }
header()  { echo -e "\n${BOLD}${CYAN}━━━  $*  ━━━${RESET}"; }
ask()     { echo -e "${YELLOW}${BOLD}[?]${RESET}    $*"; }

# ── Root check ────────────────────────────────────────────────────────────────
if [[ $EUID -ne 0 ]]; then
  die "This script must be run as root. Try: sudo bash install.sh"
fi

# =============================================================================
#  Banner
# =============================================================================
clear
echo -e "${BOLD}${CYAN}"
cat <<'EOF'
  ____  _____ __  __ ____     ____            _             _
 |  _ \|_   _|  \/  |  _ \   / ___|___  _ __ | |_ _ __ ___ | |
 | |_) | | | | |\/| | |_) | | |   / _ \| '_ \| __| '__/ _ \| |
 |  _ <  | | | |  | |  __/  | |__| (_) | | | | |_| | | (_) | |
 |_| \_\ |_| |_|  |_|_|      \____\___/|_| |_|\__|_|  \___/|_|

EOF
echo -e "${RESET}${BOLD}  nginx-rtmp-module Dashboard — Installer${RESET}"
echo -e "  ${CYAN}https://github.com/you/rtmp-control${RESET}\n"
echo -e "  This script will:"
echo -e "   • Install nginx + the RTMP module (if not already installed)"
echo -e "   • Install Python 3 + Flask"
echo -e "   • Deploy the dashboard files to /opt/rtmp-control"
echo -e "   • Configure nginx with /stat and /control endpoints"
echo -e "   • Create and enable a systemd service"
echo -e "   • (Optionally) set an API secret token. HIGHLY recommended"
echo -e "   • Press Enter when asked for input to accept the standard settings\n"
echo -e "${YELLOW}  Your existing nginx.conf will be backed up before any changes.${RESET}\n"

read -rp "  Press ENTER to continue or Ctrl+C to abort..." _

# =============================================================================
#  Gather configuration
# =============================================================================
header "Configuration"

# Install directory
INSTALL_DIR="/opt/rtmp-control"
ask "Install directory [${INSTALL_DIR}] (press Enter for default):"
read -rp "  > " INPUT_DIR
INSTALL_DIR="${INPUT_DIR:-$INSTALL_DIR}"

# API port
API_PORT="8088"
ask "API port [${API_PORT}] (press Enter for default):"
read -rp "  > " INPUT_PORT
API_PORT="${INPUT_PORT:-$API_PORT}"

# API secret
API_SECRET=""
ask "Set an API secret token? (recommended if server is internet-facing) [Y/n]:"
read -rp "  > " SET_SECRET
if [[ "${SET_SECRET,,}" != "n" ]]; then
  ask "Auto-generate a secure token? [Y/n]:"
  read -rp "  > " AUTO_TOKEN
  if [[ "${AUTO_TOKEN,,}" != "n" ]]; then
    API_SECRET="$(openssl rand -hex 24)"
    ok "Generated token: ${API_SECRET}"
    echo -e "  ${YELLOW}${BOLD}Copy this token — you will need it to access the dashboard.${RESET}"
  else
    while true; do
      ask "Enter secret token (min 16 chars, no spaces):"
      read -rsp "  > " API_SECRET; echo
      if [[ ${#API_SECRET} -ge 16 && "$API_SECRET" != *" "* ]]; then
        break
      fi
      warn "Token must be at least 16 characters with no spaces. Try again."
    done
    ok "Secret token set."
  fi
fi

# LAN-only access
LAN_ONLY_VAL="False"
ask "Restrict dashboard access to local network only? (blocks internet access) [Y/n]:"
read -rp "  > " SET_LAN_ONLY
if [[ "${SET_LAN_ONLY,,}" != "n" ]]; then
  LAN_ONLY_VAL="True"
  ok "LAN-only access enabled."
fi

# RTMP listen port
RTMP_PORT="1935"
ask "RTMP listen port for the default server block [${RTMP_PORT}] (press Enter for default):"
read -rp "  > " INPUT_RTMP
RTMP_PORT="${INPUT_RTMP:-$RTMP_PORT}"

# HTTP port
HTTP_PORT="80"
ask "nginx HTTP port [${HTTP_PORT}] (press Enter for default):"
read -rp "  > " INPUT_HTTP
HTTP_PORT="${INPUT_HTTP:-$HTTP_PORT}"

# NGINX_CONF location
NGINX_CONF="/etc/nginx/nginx.conf"

echo ""
info "Summary:"
echo "   Install dir  : ${INSTALL_DIR}"
echo "   API port     : ${API_PORT}"
echo "   API secret   : ${API_SECRET:+(set)}"
echo "   LAN only     : ${LAN_ONLY_VAL}"
echo "   RTMP port    : ${RTMP_PORT}"
echo "   HTTP port    : ${HTTP_PORT}"
echo "   nginx.conf   : ${NGINX_CONF}"
echo ""
ask "Proceed? [Y/n]:"
read -rp "  > " CONFIRM
[[ "${CONFIRM,,}" == "n" ]] && die "Aborted by user."

# =============================================================================
#  Step 1 — System packages
# =============================================================================
header "Step 1/6 — System packages"

info "Updating package list…"
apt-get update -qq

# nginx
if command -v nginx &>/dev/null; then
  ok "nginx is already installed ($(nginx -v 2>&1 | grep -o '[0-9.]*' | head -1))"
else
  info "Installing nginx…"
  apt-get install -y -qq nginx
  ok "nginx installed"
fi

# libnginx-mod-rtmp
if dpkg -l libnginx-mod-rtmp 2>/dev/null | grep -q '^ii'; then
  ok "libnginx-mod-rtmp is already installed"
else
  info "Installing libnginx-mod-rtmp…"
  apt-get install -y -qq libnginx-mod-rtmp
  ok "libnginx-mod-rtmp installed"
fi

# python3 + pip
if command -v python3 &>/dev/null; then
  ok "Python 3 is already installed ($(python3 --version 2>&1))"
else
  info "Installing Python 3…"
  apt-get install -y -qq python3
  ok "Python 3 installed"
fi

if command -v pip3 &>/dev/null; then
  ok "pip3 is already available"
else
  info "Installing python3-pip…"
  apt-get install -y -qq python3-pip
  ok "pip3 installed"
fi

# Flask
if python3 -c "import flask" 2>/dev/null; then
  ok "Flask is already installed"
else
  info "Installing Flask…"
  pip3 install flask --break-system-packages -q || pip3 install flask -q
  ok "Flask installed"
fi

# =============================================================================
#  Step 2 — Create install directory & deploy files
# =============================================================================
header "Step 2/6 — Deploy files"

mkdir -p "${INSTALL_DIR}"
ok "Directory ${INSTALL_DIR} ready"

# Determine where this script lives so we can find sibling files
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

deploy_file() {
  local src="$1" dst="$2"
  if [[ -f "${SCRIPT_DIR}/${src}" ]]; then
    cp "${SCRIPT_DIR}/${src}" "${dst}"
    ok "Deployed ${src} → ${dst}"
  else
    warn "${src} not found next to install.sh — skipping (you can copy it manually to ${dst})"
  fi
}

deploy_file "rtmp-api.py"        "${INSTALL_DIR}/rtmp-api.py"
deploy_file "rtmp-dashboard.html" "${INSTALL_DIR}/rtmp-dashboard.html"
deploy_file "README.md"           "${INSTALL_DIR}/README.md"

# Patch API_PORT and API_SECRET into rtmp-api.py if it was deployed
API_FILE="${INSTALL_DIR}/rtmp-api.py"
if [[ -f "${API_FILE}" ]]; then
  sed -i "s|^API_PORT\s*=.*|API_PORT    = ${API_PORT}|" "${API_FILE}"
  # Escape single quotes in secret for sed
  ESCAPED_SECRET="${API_SECRET//\'/\'}"
  sed -i "s|^API_SECRET\s*=.*|API_SECRET  = \"${ESCAPED_SECRET}\"|" "${API_FILE}"
  sed -i "s|^LAN_ONLY\s*=.*|LAN_ONLY     = ${LAN_ONLY_VAL}|" "${API_FILE}"
  # Write rtmp-settings.json so it matches the install choice and takes precedence at runtime
  LAN_ONLY_JSON="false"; [[ "${LAN_ONLY_VAL}" == "True" ]] && LAN_ONLY_JSON="true"
  echo "{\"lan_only\": ${LAN_ONLY_JSON}}" > "${INSTALL_DIR}/rtmp-settings.json"
  ok "rtmp-api.py configured (port=${API_PORT}, secret=${API_SECRET:+set}, lan_only=${LAN_ONLY_VAL})"
fi

# =============================================================================
#  Step 3 — nginx config
# =============================================================================
header "Step 3/6 — nginx configuration"

# Backup existing config
BACKUP="${NGINX_CONF}.bak.$(date +%Y%m%d_%H%M%S)"
cp "${NGINX_CONF}" "${BACKUP}"
ok "Existing nginx.conf backed up to ${BACKUP}"

# Check if an rtmp {} block already exists
HAS_RTMP=false
grep -q 'rtmp\s*{' "${NGINX_CONF}" && HAS_RTMP=true

# Check if /stat location already exists
HAS_STAT=false
grep -q 'rtmp_stat' "${NGINX_CONF}" && HAS_STAT=true

# Check if load_module line exists
HAS_LOAD=false
grep -q 'ngx_rtmp_module' "${NGINX_CONF}" && HAS_LOAD=true
ls /etc/nginx/modules-enabled/ 2>/dev/null | grep -q rtmp && HAS_LOAD=true

if $HAS_LOAD; then
  ok "ngx_rtmp_module already referenced — skipping module setup"
else
  # Check if modules-enabled dir exists (standard Ubuntu nginx)
  if [[ -d /etc/nginx/modules-enabled ]]; then
    RTMP_CONF="/etc/nginx/modules-enabled/50-mod-rtmp.conf"
    if [[ ! -f "${RTMP_CONF}" ]]; then
      echo 'load_module modules/ngx_rtmp_module.so;' > "${RTMP_CONF}"
      ok "Created ${RTMP_CONF} to load RTMP module"
    fi
  else
    # No modules-enabled dir — prepend load_module directly to nginx.conf
    sed -i '1s/^/load_module modules\/ngx_rtmp_module.so;\n/' "${NGINX_CONF}"
    ok "Prepended load_module directive to nginx.conf"
  fi
fi

# Ensure nginx.conf actually includes the modules-enabled directory.
# A non-Ubuntu or generic nginx.conf may omit this include, causing
# "unknown directive" errors even when the .conf file exists.
if [[ -d /etc/nginx/modules-enabled ]] && \
   ! grep -qE 'include.*modules-enabled' "${NGINX_CONF}" && \
   ! grep -q 'ngx_rtmp_module' "${NGINX_CONF}"; then
  info "nginx.conf does not include modules-enabled — adding load_module directly…"
  sed -i '1s/^/load_module modules\/ngx_rtmp_module.so;\n/' "${NGINX_CONF}"
  ok "Added load_module directive to nginx.conf"
fi

# ── Ensure worker_processes 1 ─────────────────────────────────────────────────
info "Checking worker_processes setting (must be 1 for reliable RTMP stats)…"
if grep -qE '^\s*worker_processes\s' "${NGINX_CONF}"; then
  if grep -qE '^\s*worker_processes\s+1\s*;' "${NGINX_CONF}"; then
    ok "worker_processes is already 1"
  else
    sed -i -E 's/^([[:space:]]*)worker_processes[[:space:]]+[^;]+;/\1worker_processes 1;/' "${NGINX_CONF}"
    ok "Changed worker_processes to 1"
  fi
else
  sed -i '1s/^/worker_processes 1;\n/' "${NGINX_CONF}"
  ok "Added worker_processes 1 to nginx.conf"
fi

# ── Add /stat and /control locations ─────────────────────────────────────────
if $HAS_STAT; then
  ok "/stat location already present in nginx.conf — skipping"
else
  info "Adding /stat and /control locations to nginx.conf…"

  # We'll inject them into the first server {} block inside http {}
  # Strategy: find the closing brace of the first http > server block and insert before it
  python3 - "${NGINX_CONF}" "${HTTP_PORT}" <<'PYEOF'
import sys, re

conf_path = sys.argv[1]
http_port  = sys.argv[2]

with open(conf_path, 'r') as f:
    content = f.read()

# Inject a self-contained server block so no existing server {} is needed in
# nginx.conf (Ubuntu typically keeps server blocks in sites-enabled/).
inject = """
    # RTMP stats (added by rtmp-control installer)
    server {
        listen HTTPPORT;
        server_name localhost;
        access_log off;

        location /stat {
            rtmp_stat all;
            rtmp_stat_stylesheet stat.xsl;
            allow 127.0.0.1;
            deny all;
        }

        location /stat.xsl {
            root /usr/share/doc/libnginx-mod-rtmp/;
        }

        location /control {
            rtmp_control all;
            allow 127.0.0.1;
            deny all;
        }
    }
""".replace("HTTPPORT", http_port)

# Locate the http {} block using proper brace-depth tracking so we never
# accidentally walk into the rtmp {} block or mistake a comment for a block.
http_m = re.search(r'\bhttp\s*\{', content)
if not http_m:
    print("Could not find http {} block in nginx.conf — please add /stat manually")
    sys.exit(0)

depth = 0
http_end = None
for idx in range(http_m.start(), len(content)):
    if content[idx] == '{':
        depth += 1
    elif content[idx] == '}':
        depth -= 1
        if depth == 0:
            http_end = idx
            break

if http_end is None:
    print("Could not find closing } of http block — please add /stat manually")
    sys.exit(0)

# Insert the new server block just before the http block's closing }
content = content[:http_end] + inject + content[http_end:]

with open(conf_path, 'w') as f:
    f.write(content)

print("Locations injected into nginx.conf")
PYEOF
  ok "/stat and /control locations added"
fi

# ── Add rtmp {} block if missing ──────────────────────────────────────────────
if $HAS_RTMP; then
  ok "rtmp {} block already present — leaving it unchanged"
else
  warn "No rtmp {} block found. Adding a minimal default block…"
  cat >> "${NGINX_CONF}" <<RTMPBLOCK

# RTMP server block (added by rtmp-control installer)
rtmp {
    server {
        listen ${RTMP_PORT};
        chunk_size 4096;

        application live {
            live on;
            record off;
            meta copy;

            # Add your push destinations here, or use the dashboard
            #push "rtmp://example.com/live/your-stream-key";
        }
    }
}
RTMPBLOCK
  ok "Minimal rtmp {} block added on port ${RTMP_PORT}"
fi

# ── Validate and reload nginx ─────────────────────────────────────────────────
info "Testing nginx config…"
if nginx -t 2>/dev/null; then
  ok "nginx config is valid"
  info "Reloading nginx…"
  systemctl reload nginx
  ok "nginx reloaded"
else
  nginx -t || true  # print the actual error (|| true prevents set -e from firing)
  warn "nginx config test failed. Restoring backup…"
  cp "${BACKUP}" "${NGINX_CONF}"
  nginx -t && systemctl reload nginx
  die "Config was invalid and has been restored from backup. Please fix nginx.conf manually."
fi

# =============================================================================
#  Step 4 — systemd service
# =============================================================================
header "Step 4/6 — systemd service"

SERVICE_FILE="/etc/systemd/system/rtmp-control.service"

cat > "${SERVICE_FILE}" <<EOF
[Unit]
Description=RTMP Control Panel API
Documentation=file://${INSTALL_DIR}/README.md
After=network.target nginx.service
Wants=nginx.service

[Service]
Type=simple
User=root
WorkingDirectory=${INSTALL_DIR}
ExecStart=/usr/bin/python3 ${INSTALL_DIR}/rtmp-api.py
Restart=on-failure
RestartSec=5
StandardOutput=journal
StandardError=journal

[Install]
WantedBy=multi-user.target
EOF

systemctl daemon-reload
systemctl enable rtmp-control
systemctl restart rtmp-control

# Give it a moment to start
sleep 2

if systemctl is-active --quiet rtmp-control; then
  ok "rtmp-control service is running"
else
  warn "Service did not start cleanly. Check: sudo journalctl -u rtmp-control -n 30"
fi

# =============================================================================
#  Step 5 — Firewall
# =============================================================================
header "Step 5/6 — Firewall"

if command -v ufw &>/dev/null && ufw status | grep -q "Status: active"; then
  info "UFW is active. Configuring rules…"

  # nginx HTTP
  ufw allow "${HTTP_PORT}/tcp"   comment "nginx HTTP" 2>/dev/null || true
  ok "Allowed port ${HTTP_PORT}/tcp (nginx HTTP)"

  # RTMP
  ufw allow "${RTMP_PORT}/tcp"  comment "nginx RTMP" 2>/dev/null || true
  ok "Allowed port ${RTMP_PORT}/tcp (RTMP ingest)"

  # API port — ask whether to open it
  echo ""
  ask "Open API port ${API_PORT} in UFW?"
  echo "   • Yes if you want to access the dashboard from another machine"
  echo "   • No  if you will only access it from this machine (localhost)"
  read -rp "  Open port ${API_PORT}? [y/N]: " OPEN_API_PORT
  if [[ "${OPEN_API_PORT,,}" == "y" ]]; then
    ufw allow "${API_PORT}/tcp" comment "rtmp-control API" 2>/dev/null || true
    ok "Allowed port ${API_PORT}/tcp"
  else
    ok "Port ${API_PORT} left closed — accessible via localhost only"
  fi
else
  info "UFW not active — skipping firewall configuration"
  warn "Make sure port ${API_PORT} is reachable if accessing from another machine"
fi

# =============================================================================
#  Step 6 — Smoke test
# =============================================================================
header "Step 6/6 — Verification"

PASS=0; FAIL=0

check() {
  local label="$1"; shift
  if "$@" &>/dev/null; then
    ok "${label}"; (( PASS++ )) || true
  else
    error "${label} — FAILED"; (( FAIL++ )) || true
  fi
}

check "nginx is running"              systemctl is-active --quiet nginx
check "rtmp-control service running"  systemctl is-active --quiet rtmp-control
check "API responds on port ${API_PORT}" bash -c "curl -s --max-time 5 -o /dev/null -w '%{http_code}' http://localhost:${API_PORT}/api/config | grep -qE '^(200|401)$'"
check "/stat endpoint reachable"      bash -c "curl -sf http://localhost/stat | grep -q '<rtmp>'"
check "nginx.conf readable by API"    test -r "${NGINX_CONF}"
check "RTMP module file present"      test -f /usr/lib/nginx/modules/ngx_rtmp_module.so
check "worker_processes set to 1"    grep -qE '^\s*worker_processes\s+1\s*;' "${NGINX_CONF}"

echo ""
echo -e "  Results: ${GREEN}${PASS} passed${RESET}  ${RED}${FAIL} failed${RESET}"

# =============================================================================
#  Done
# =============================================================================
header "Installation complete"

SERVER_IP=$(hostname -I | awk '{print $1}')

echo ""
echo -e "  ${BOLD}Dashboard URL:${RESET}  ${CYAN}http://${SERVER_IP}:${API_PORT}${RESET}"
echo -e "  ${BOLD}Local URL:${RESET}      ${CYAN}http://localhost:${API_PORT}${RESET}"
echo ""
echo -e "  ${BOLD}Files installed to:${RESET}  ${INSTALL_DIR}/"
echo -e "  ${BOLD}nginx.conf backup:${RESET}   ${BACKUP}"
echo ""
if [[ -n "${API_SECRET}" ]]; then
  echo -e "  ${YELLOW}${BOLD}Remember:${RESET} Enter your API token in the dashboard's Token field."
fi
echo -e "  ${BOLD}Useful commands:${RESET}"
echo -e "   sudo systemctl status rtmp-control    # service status"
echo -e "   sudo journalctl -u rtmp-control -f    # live logs"
echo -e "   sudo systemctl restart rtmp-control   # restart API"
echo -e "   sudo nginx -t && sudo nginx -s reload # test & reload nginx"
echo ""
if (( FAIL > 0 )); then
  warn "${FAIL} check(s) failed — see above for details."
  warn "Review logs: sudo journalctl -u rtmp-control -n 50"
else
  echo -e "  ${GREEN}${BOLD}All checks passed. You're good to go!${RESET}"
fi
echo ""
