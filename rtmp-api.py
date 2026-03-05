#!/usr/bin/env python3
"""
RTMP Control API - Companion server for the RTMP dashboard
Reads and modifies nginx.conf to toggle push destinations live.

Usage:
    pip install flask
    sudo python3 rtmp-api.py

Runs on port 8088 by default. Needs sudo to reload nginx and read /etc/nginx/nginx.conf
"""

import re
import os
import sys
import json
import ipaddress
import subprocess
import threading
import urllib.request
import urllib.parse
from flask import Flask, jsonify, request, send_from_directory
from functools import wraps

# ── Config ────────────────────────────────────────────────────────────────────
NGINX_CONF   = "/etc/nginx/nginx.conf"
NGINX_STAT   = "http://localhost/stat"   # internal stat URL
API_PORT     = 8088
API_SECRET   = ""   # optional: set a token to protect the API, e.g. "mysecret"
LAN_ONLY     = False  # restrict dashboard access to LAN/localhost only
CORS_ORIGIN  = ""   # "" = same-origin only (recommended); "*" = allow all origins
# ─────────────────────────────────────────────────────────────────────────────

# ── LAN-only settings persistence ─────────────────────────────────────────────

_SETTINGS_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "rtmp-settings.json")


def _load_settings():
    global LAN_ONLY
    if os.path.exists(_SETTINGS_FILE):
        try:
            with open(_SETTINGS_FILE) as f:
                s = json.load(f)
            LAN_ONLY = bool(s.get("lan_only", LAN_ONLY))
        except Exception:
            pass


def _save_settings():
    with open(_SETTINGS_FILE, "w") as f:
        json.dump({"lan_only": LAN_ONLY}, f)


_load_settings()


def _is_lan_ip(ip):
    """Return True if ip is a loopback or private (RFC-1918/4193) address."""
    try:
        addr = ipaddress.ip_address(ip)
        return addr.is_private or addr.is_loopback
    except ValueError:
        return False

conf_lock = threading.Lock()  # guards all nginx.conf read/write sequences

app = Flask(__name__, static_folder=".")
app.config['MAX_CONTENT_LENGTH'] = 1 * 1024 * 1024  # 1 MB request body limit


def require_secret(f):
    @wraps(f)
    def decorated(*args, **kwargs):
        if API_SECRET:
            token = request.headers.get("X-API-Token", "")
            if token != API_SECRET:
                return jsonify({"error": "Unauthorized"}), 401
        return f(*args, **kwargs)
    return decorated


def cors(response):
    if CORS_ORIGIN:
        response.headers["Access-Control-Allow-Origin"]  = CORS_ORIGIN
        response.headers["Access-Control-Allow-Headers"] = "Content-Type, X-API-Token"
        response.headers["Access-Control-Allow-Methods"] = "GET, POST, OPTIONS"
    return response


@app.after_request
def after(response):
    return cors(response)


@app.before_request
def check_lan_access():
    if LAN_ONLY and not _is_lan_ip(request.remote_addr or ""):
        return jsonify({"error": "Access restricted to local network"}), 403


@app.route("/api/options", methods=["OPTIONS"])
@app.route("/api/<path:p>", methods=["OPTIONS"])
def options(p=""):
    return cors(app.response_class(status=204))


# ── nginx.conf parser ─────────────────────────────────────────────────────────

def _safe_conf_path():
    """Return the real path of NGINX_CONF, refusing to follow symlinks."""
    real = os.path.realpath(NGINX_CONF)
    if real != os.path.abspath(NGINX_CONF):
        raise PermissionError(f"nginx.conf is a symlink pointing to {real} — refusing to open")
    return real


def read_conf():
    with open(_safe_conf_path(), "r", encoding="utf-8") as f:
        return f.readlines()


def write_conf(lines):
    with open(_safe_conf_path(), "w", encoding="utf-8") as f:
        f.writelines(lines)


def parse_rtmp_config(lines):
    """
    Parse the rtmp {} block from nginx.conf and return structured data.
    Handles commented-out push lines and inline comments as labels.
    """
    # Find rtmp block boundaries
    rtmp_start = None
    depth = 0
    rtmp_end = None

    for i, line in enumerate(lines):
        stripped = line.strip()
        if stripped.startswith("#"):
            continue
        if re.search(r'\brtmp\s*\{', stripped) and rtmp_start is None:
            rtmp_start = i
            depth = 0

        if rtmp_start is not None:
            depth += stripped.count("{") - stripped.count("}")
            if depth <= 0 and i > rtmp_start:
                rtmp_end = i
                break

    if rtmp_start is None:
        return {"error": "No rtmp {} block found in nginx.conf", "servers": []}

    servers = []
    current_server = None
    current_app = None
    pending_label = None

    i = rtmp_start
    while i <= (rtmp_end or len(lines) - 1):
        raw = lines[i]
        stripped = raw.strip()

        # Detect pending label from a comment line like "#CB Nederland"
        comment_match = re.match(r'^#([^p\s].{0,40})$', stripped)
        if comment_match:
            label_candidate = comment_match.group(1).strip()
            # Only treat as label if next non-empty line is a push
            for j in range(i + 1, min(i + 4, len(lines))):
                nxt = lines[j].strip()
                if nxt and not nxt.startswith("##"):
                    if re.search(r'#?\s*push\s+"', nxt):
                        pending_label = label_candidate
                    break
            i += 1
            continue

        # server block open
        if re.match(r'^server\s*\{', stripped):
            current_server = {"port": None, "chunk_size": 4096, "comment": "", "apps": []}
            servers.append(current_server)
            current_app = None
            i += 1
            continue

        # listen directive
        listen_m = re.match(r'^listen\s+(\d+)\s*;', stripped)
        if listen_m and current_server is not None:
            current_server["port"] = int(listen_m.group(1))
            i += 1
            continue

        # chunk_size
        chunk_m = re.match(r'^chunk_size\s+(\d+)\s*;', stripped)
        if chunk_m and current_server is not None:
            current_server["chunk_size"] = int(chunk_m.group(1))
            i += 1
            continue

        # application block open
        app_m = re.match(r'^application\s+(\S+)\s*\{', stripped)
        if app_m and current_server is not None:
            current_app = {"name": app_m.group(1), "live": False, "record": "off", "pushes": [], "line_start": i}
            current_server["apps"].append(current_app)
            i += 1
            continue

        # live on/off
        if re.match(r'^live\s+(on|off)\s*;', stripped) and current_app:
            current_app["live"] = stripped.startswith("live on")
            i += 1
            continue

        # record
        rec_m = re.match(r'^record\s+(\S+)\s*;', stripped)
        if rec_m and current_app:
            current_app["record"] = rec_m.group(1)
            i += 1
            continue

        # push line — active or commented out
        push_m = re.match(r'^(#\s*)?push\s+"([^"]+)"\s*;', stripped)
        if push_m and current_app is not None:
            active = push_m.group(1) is None
            url    = push_m.group(2)
            label  = pending_label or _guess_label(url)
            pending_label = None
            current_app["pushes"].append({
                "line":   i,
                "url":    url,
                "active": active,
                "label":  label,
            })
            i += 1
            continue

        pending_label = None
        i += 1

    return {"servers": servers}


def _find_rtmp_end(lines):
    """Return the line index of the rtmp block's closing brace."""
    rtmp_start = None
    depth = 0
    for i, line in enumerate(lines):
        stripped = line.strip()
        if stripped.startswith("#"):
            continue
        if re.search(r'\brtmp\s*\{', stripped) and rtmp_start is None:
            rtmp_start = i
        if rtmp_start is not None:
            depth += stripped.count("{") - stripped.count("}")
            if depth <= 0 and i > rtmp_start:
                return i
    return None


def _find_server_block_lines(lines, port):
    """Return (start, end) line indices for the server block listening on port."""
    rtmp_start = None
    for i, line in enumerate(lines):
        stripped = line.strip()
        if not stripped.startswith("#") and re.search(r'\brtmp\s*\{', stripped):
            rtmp_start = i
            break
    if rtmp_start is None:
        return None, None

    i = rtmp_start + 1
    while i < len(lines):
        stripped = lines[i].strip()
        if not stripped.startswith("#") and re.match(r'^server\s*\{', stripped):
            server_start = i
            depth = 0
            server_port = None
            j = i
            while j < len(lines):
                s = lines[j].strip()
                if not s.startswith("#"):
                    depth += s.count("{") - s.count("}")
                    listen_m = re.match(r'^listen\s+(\d+)\s*;', s)
                    if listen_m:
                        server_port = int(listen_m.group(1))
                if depth <= 0 and j > server_start:
                    if server_port == port:
                        return server_start, j
                    i = j
                    break
                j += 1
        i += 1
    return None, None


def _guess_label(url):
    """Derive a human-readable platform name from an RTMP URL."""
    url_lower = url.lower()
    if "highwebmedia" in url_lower or "mmcdn" in url_lower:
        return "Chaturbate"
    if "joystick" in url_lower:
        return "Joystick.tv"
    if "doppiocdn" in url_lower or "stripchat" in url_lower:
        return "Stripchat"
    if "live-video.net" in url_lower:
        return "Fansly"
    if "twitch" in url_lower:
        return "Twitch"
    if "youtube" in url_lower or "googlevideo" in url_lower:
        return "YouTube"
    if "facebook" in url_lower or "fbcdn" in url_lower:
        return "Facebook"
    if "kick" in url_lower:
        return "Kick"
    # fallback: use hostname
    m = re.search(r'rtmps?://([^/:]+)', url)
    return m.group(1) if m else "Unknown"


# ── Input validation ──────────────────────────────────────────────────────────

def validate_rtmp_url(url):
    """Reject URLs that could inject nginx config directives."""
    if not re.match(r'^rtmps?://', url):
        raise ValueError("URL must start with rtmp:// or rtmps://")
    for ch in ('"', '\n', '\r', ';', '{', '}'):
        if ch in url:
            raise ValueError("URL contains an invalid character")


def validate_stream_param(s):
    """Whitelist characters allowed in RTMP app/stream-name params."""
    if not s or not re.match(r'^[a-zA-Z0-9_./-]+$', s):
        raise ValueError(f"Invalid stream parameter: only alphanumeric, '.', '_', '/', '-' allowed")
    return urllib.parse.quote(s, safe='')


# ── API routes ────────────────────────────────────────────────────────────────

@app.route("/api/config")
@require_secret
def api_config():
    try:
        lines = read_conf()
        data  = parse_rtmp_config(lines)
        return jsonify(data)
    except PermissionError:
        return jsonify({"error": f"Cannot read {NGINX_CONF} — run with sudo"}), 403
    except FileNotFoundError:
        return jsonify({"error": f"{NGINX_CONF} not found"}), 404
    except Exception as e:
        print(f"[ERROR] /api/config: {e}", file=sys.stderr)
        return jsonify({"error": "Internal server error"}), 500


@app.route("/api/toggle", methods=["POST"])
@require_secret
def api_toggle():
    """
    Toggle a push destination active/inactive.
    Body: { "line": <int>, "active": <bool> }
    active=true  → uncomment the push line
    active=false → comment out the push line
    """
    try:
        body = request.get_json(force=True)
        line_no = int(body["line"])
        make_active = bool(body["active"])

        with conf_lock:
            lines = read_conf()
            if line_no < 0 or line_no >= len(lines):
                return jsonify({"error": "Line number out of range"}), 400

            raw = lines[line_no]
            stripped = raw.strip()

            if make_active:
                # Remove leading comment marker(s)
                new_stripped = re.sub(r'^#+\s*', '', stripped)
            else:
                # Add comment marker if not already commented
                if stripped.startswith("#"):
                    return jsonify({"ok": True, "reloaded": False, "message": "Already inactive"})
                new_stripped = "#" + stripped

            # Preserve original indentation
            indent = re.match(r'^(\s*)', raw).group(1)
            lines[line_no] = indent + new_stripped + "\n"

            write_conf(lines)
        return jsonify({"ok": True, "reloaded": False, "message": "Config saved — reload nginx or use Drop Publisher to apply"})

    except PermissionError:
        return jsonify({"error": f"Cannot write {NGINX_CONF} — run with sudo"}), 403
    except (KeyError, ValueError, TypeError) as e:
        return jsonify({"error": str(e)}), 400
    except Exception as e:
        print(f"[ERROR] /api/toggle: {e}", file=sys.stderr)
        return jsonify({"error": "Internal server error"}), 500


@app.route("/api/add_push", methods=["POST"])
@require_secret
def api_add_push():
    """
    Add a new push line to an app.
    Body: { "server_port": 1935, "app_name": "live", "url": "rtmp://...", "label": "Twitch" }
    Inserts the new push after the last existing push in that app block.
    """
    try:
        body     = request.get_json(force=True)
        port     = int(body["server_port"])
        app_name = body["app_name"]
        url      = body["url"].strip()
        label    = body.get("label", _guess_label(url))

        validate_rtmp_url(url)
        # Strip newlines from label so it can't escape the comment line
        label = label.replace('\n', '').replace('\r', '').strip()

        with conf_lock:
            lines = read_conf()
            data  = parse_rtmp_config(lines)

            # Find the target app
            insert_after = None
            for srv in data["servers"]:
                if srv["port"] == port:
                    for ap in srv["apps"]:
                        if ap["name"] == app_name:
                            if ap["pushes"]:
                                insert_after = ap["pushes"][-1]["line"]
                            else:
                                # Scan forward from the app block open to find its closing }
                                depth = 0
                                for k in range(ap["line_start"], len(lines)):
                                    s = lines[k].strip()
                                    if not s.startswith("#"):
                                        depth += s.count("{") - s.count("}")
                                    if depth <= 0 and k > ap["line_start"]:
                                        insert_after = k - 1
                                        break

            if insert_after is None:
                return jsonify({"error": "App not found or could not determine insert position"}), 400

            new_line = f'      #{label}\n      push "{url}";\n'
            lines.insert(insert_after + 1, new_line)

            write_conf(lines)
            reloaded, reload_msg = nginx_reload()
        return jsonify({"ok": True, "reloaded": reloaded, "message": reload_msg})

    except PermissionError:
        return jsonify({"error": "Permission denied — run with sudo"}), 403
    except (KeyError, ValueError, TypeError) as e:
        return jsonify({"error": str(e)}), 400
    except Exception as e:
        print(f"[ERROR] /api/add_push: {e}", file=sys.stderr)
        return jsonify({"error": "Internal server error"}), 500


@app.route("/api/remove_push", methods=["POST"])
@require_secret
def api_remove_push():
    """
    Permanently remove a push line (and its label comment if present).
    Body: { "line": <int> }
    """
    try:
        body    = request.get_json(force=True)
        line_no = int(body["line"])

        with conf_lock:
            lines = read_conf()
            if line_no < 0 or line_no >= len(lines):
                return jsonify({"error": "Line number out of range"}), 400

            # Remove label comment line if it's immediately above
            start = line_no
            if line_no > 0:
                prev = lines[line_no - 1].strip()
                if re.match(r'^#[^p\s]', prev):
                    start = line_no - 1

            del lines[start:line_no + 1]
            write_conf(lines)
            reloaded, reload_msg = nginx_reload()
        return jsonify({"ok": True, "reloaded": reloaded, "message": reload_msg})

    except PermissionError:
        return jsonify({"error": "Permission denied — run with sudo"}), 403
    except (KeyError, ValueError, TypeError) as e:
        return jsonify({"error": str(e)}), 400
    except Exception as e:
        print(f"[ERROR] /api/remove_push: {e}", file=sys.stderr)
        return jsonify({"error": "Internal server error"}), 500


@app.route("/api/add_server", methods=["POST"])
@require_secret
def api_add_server():
    """
    Add a new RTMP server block.
    Body: { "port": 1936, "app_name": "live" }
    """
    try:
        body     = request.get_json(force=True)
        port     = int(body["port"])
        app_name = body.get("app_name", "live").strip()

        if port < 1 or port > 65535:
            return jsonify({"error": "Port must be between 1 and 65535"}), 400
        if not re.match(r'^[a-zA-Z0-9_-]+$', app_name):
            return jsonify({"error": "App name may only contain letters, numbers, _ and -"}), 400

        with conf_lock:
            lines = read_conf()
            data  = parse_rtmp_config(lines)

            if any(srv["port"] == port for srv in data["servers"]):
                return jsonify({"error": f"Port {port} is already configured"}), 400

            rtmp_end = _find_rtmp_end(lines)
            if rtmp_end is None:
                return jsonify({"error": "Could not find rtmp {} block — is nginx.conf valid?"}), 500

            new_block = (
                f'\n    server {{\n'
                f'        listen {port};\n'
                f'        chunk_size 4096;\n'
                f'\n'
                f'        application {app_name} {{\n'
                f'            live on;\n'
                f'            record off;\n'
                f'        }}\n'
                f'    }}\n'
            )
            lines.insert(rtmp_end, new_block)
            write_conf(lines)
            reloaded, reload_msg = nginx_reload()
        return jsonify({"ok": True, "reloaded": reloaded, "message": reload_msg})

    except PermissionError:
        return jsonify({"error": "Permission denied — run with sudo"}), 403
    except (KeyError, ValueError, TypeError) as e:
        return jsonify({"error": str(e)}), 400
    except Exception as e:
        print(f"[ERROR] /api/add_server: {e}", file=sys.stderr)
        return jsonify({"error": "Internal server error"}), 500


@app.route("/api/remove_server", methods=["POST"])
@require_secret
def api_remove_server():
    """
    Remove an entire RTMP server block.
    Body: { "port": 1936 }
    """
    try:
        body = request.get_json(force=True)
        port = int(body["port"])

        with conf_lock:
            lines = read_conf()
            start, end = _find_server_block_lines(lines, port)
            if start is None:
                return jsonify({"error": f"Server block for port {port} not found"}), 404

            del lines[start:end + 1]
            write_conf(lines)
            reloaded, reload_msg = nginx_reload()
        return jsonify({"ok": True, "reloaded": reloaded, "message": reload_msg})

    except PermissionError:
        return jsonify({"error": "Permission denied — run with sudo"}), 403
    except (KeyError, ValueError, TypeError) as e:
        return jsonify({"error": str(e)}), 400
    except Exception as e:
        print(f"[ERROR] /api/remove_server: {e}", file=sys.stderr)
        return jsonify({"error": "Internal server error"}), 500


@app.route("/api/reload", methods=["POST"])
@require_secret
def api_reload():
    reloaded, msg = nginx_reload()
    return jsonify({"reloaded": reloaded, "message": msg})


def _fetch_stat_best_worker(max_attempts=10):
    """
    Try fetching NGINX_STAT up to max_attempts times and return the first
    response that contains active stream data (<stream> element).

    After an nginx reload there are briefly multiple worker processes.
    New workers have no RTMP connection state, old workers do.  By making
    several rapid loopback requests we hit different workers and surface
    the one that has live data, without waiting for OBS to reconnect.

    Falls back to the last empty XML received if no worker has stream
    data yet (i.e. OBS hasn't reconnected at all).
    """
    last_xml = None
    for _ in range(max_attempts):
        try:
            with urllib.request.urlopen(NGINX_STAT, timeout=2) as r:
                xml = r.read().decode("utf-8")
            if "<stream>" in xml:
                return xml          # found a worker with active streams
            last_xml = xml
        except Exception:
            pass
    return last_xml                 # all workers empty (or all failed)


@app.route("/api/stat")
@require_secret
def api_stat():
    """Proxy the nginx /stat XML to avoid CORS issues.

    Calls _fetch_stat_best_worker() which iterates across nginx worker
    processes to find one that has active RTMP connection data.
    """
    xml = _fetch_stat_best_worker()
    if xml is None:
        return jsonify({"error": f"Could not reach {NGINX_STAT}"}), 502
    return app.response_class(xml, mimetype="text/xml")


@app.route("/api/drop_publisher", methods=["POST"])
@require_secret
def api_drop_publisher():
    """
    Drop the publisher connection so the stream reconnects with updated config.
    Body: { "app": "live", "name": "<stream_key>" }
    Uses the nginx-rtmp control module endpoint.
    """
    try:
        body     = request.get_json(force=True)
        app_name = validate_stream_param(body.get("app", "live"))
        name     = validate_stream_param(body.get("name", ""))
        # Drop first (targets the current worker that has OBS's active connection)
        ctl_url  = f"http://localhost/control/drop/publisher?app={app_name}&name={name}"
        with urllib.request.urlopen(ctl_url, timeout=3) as r:
            result = r.read().decode("utf-8")
        # Then reload nginx so OBS reconnects to new workers with updated config
        nginx_reload()
        return jsonify({"ok": True, "result": result})
    except (KeyError, ValueError, TypeError) as e:
        return jsonify({"error": str(e)}), 400
    except Exception as e:
        print(f"[ERROR] /api/drop_publisher: {e}", file=sys.stderr)
        return jsonify({"error": "Internal server error"}), 502


@app.route("/api/test_nginx")
@require_secret
def api_test_nginx():
    ok, out = nginx_test()
    return jsonify({"ok": ok, "output": out})


# ── LAN-only access control ───────────────────────────────────────────────────

@app.route("/api/lan_only", methods=["GET"])
@require_secret
def api_get_lan_only():
    return jsonify({"lan_only": LAN_ONLY})


@app.route("/api/lan_only", methods=["POST"])
@require_secret
def api_set_lan_only():
    global LAN_ONLY
    data = request.get_json(force=True)
    LAN_ONLY = bool(data.get("enabled", False))
    _save_settings()
    return jsonify({"lan_only": LAN_ONLY})


# ── nginx helpers ─────────────────────────────────────────────────────────────

def nginx_test():
    result = subprocess.run(["nginx", "-t"], capture_output=True, text=True)
    return result.returncode == 0, (result.stdout + result.stderr).strip()


def nginx_reload():
    ok, test_out = nginx_test()
    if not ok:
        return False, f"Config test failed — NOT reloading: {test_out}"
    result = subprocess.run(["nginx", "-s", "reload"], capture_output=True, text=True)
    if result.returncode == 0:
        return True, "nginx reloaded successfully"
    return False, (result.stdout + result.stderr).strip()


# ── Serve dashboard ───────────────────────────────────────────────────────────

@app.route("/")
def index():
    return send_from_directory(".", "rtmp-dashboard.html")


# ── Main ──────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    if os.geteuid() != 0:
        print("⚠  Warning: not running as root. Reading/writing nginx.conf may fail.")
        print("   Run with: sudo python3 rtmp-api.py\n")

    print(f"RTMP Control API starting on http://0.0.0.0:{API_PORT}")
    print(f"Config file : {NGINX_CONF}")
    print(f"Stat URL    : {NGINX_STAT}")
    if API_SECRET:
        print(f"Auth        : X-API-Token required")
    else:
        print("Auth        : NONE (set API_SECRET to protect this endpoint!)")
    print(f"LAN only    : {'YES — internet access blocked' if LAN_ONLY else 'no (accessible from internet)'}")
    print()
    app.run(host="0.0.0.0", port=API_PORT, debug=False, threaded=True)
