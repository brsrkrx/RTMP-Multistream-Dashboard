# RTMP Control Panel

A web-based dashboard for managing an **nginx-rtmp-module** server on Ubuntu. Toggle push destinations live, monitor stream stats, and manage your nginx RTMP config — all from a browser.

---

## What's Included

| File | Purpose |
|---|---|
| `rtmp-api.py` | Python companion API — reads/writes `nginx.conf` and proxies nginx stats |
| `rtmp-dashboard.html` | Browser dashboard — served by the API, no separate web server needed |
| `install.sh` | Automated installer for Ubuntu (sets up nginx, the RTMP module, and the API service) |
| `rtmp-settings.json` | Persists runtime settings (e.g. LAN-only mode) — created automatically, survives reinstalls |
| `README.md` | This file |

---

## Quick Install (Ubuntu)

Run the installer as root. It will install nginx, the RTMP module, configure the stat endpoint, install the API as a systemd service, and start everything automatically:

```bash
sudo bash install.sh
```

The installer will ask a few questions before it begins:

| Prompt | Default | Notes |
|---|---|---|
| Install directory | `/opt/rtmp-control` | Where the files are deployed |
| API port | `8088` | Port the dashboard is served on |
| API secret token | **Yes — auto-generated** | A cryptographically secure token is generated automatically. Copy it when shown — you will need it to log in. See [Token Authentication](#option-1--token-authentication-recommended) |
| LAN-only access | **Yes** | Restricts the dashboard to your local network by default. See [LAN-Only Mode](#option-2--lan-only-mode) |
| RTMP port | `1935` | Port OBS connects to |
| nginx HTTP port | `80` | Port for the nginx stat endpoint |

After it completes, open a browser and go to `http://your-server-ip:8088`.

---

## Manual Setup

### 1. Install Python and Flask

The API requires Python 3 and Flask. Python 3 is pre-installed on Ubuntu. Install Flask with:

```bash
pip3 install flask --break-system-packages
```

> **Note:** The `--break-system-packages` flag is required on Ubuntu 23.04+ due to PEP 668. On older Ubuntu versions you can omit it.

---

### 2. Place the Files

```bash
sudo mkdir -p /opt/rtmp-control
sudo cp rtmp-api.py rtmp-dashboard.html /opt/rtmp-control/
```

---

### 3. Configure nginx to Expose the Stat Endpoint

The dashboard reads live stream statistics from nginx's built-in `/stat` endpoint. Add a dedicated server block inside your `http { }` section in `/etc/nginx/nginx.conf`:

```nginx
http {
    server {
        listen 80;
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
}
```

After editing the config, test and reload nginx:

```bash
sudo nginx -t
sudo nginx -s reload
```

---

### 4. (Optional) Configure the API

Open `rtmp-api.py` in a text editor and review the settings near the top of the file:

```python
NGINX_CONF  = "/etc/nginx/nginx.conf"   # Path to your nginx config
NGINX_STAT  = "http://localhost/stat"    # Internal URL for the stat endpoint
API_PORT    = 8088                       # Port the API listens on
API_SECRET  = ""                         # Leave empty for no auth (see Security below)
LAN_ONLY    = False                      # Set True to block internet access (see Security below)
CORS_ORIGIN = ""                         # "" = same-origin only (recommended); "*" = allow all
```

> **Note:** `LAN_ONLY` is also controlled at runtime via the **Access Control** toggle in the dashboard sidebar. Changes take effect immediately without restarting the service, and are saved to `rtmp-settings.json` so they persist across restarts.

---

### 5. Start the API

The API needs `sudo` to read and write `/etc/nginx/nginx.conf` and to run `nginx -s reload`.

```bash
cd /opt/rtmp-control
sudo python3 rtmp-api.py
```

---

### 6. Open the Dashboard

Open a browser and navigate to:

```
http://your-server-ip:8088
```

The **API** field is automatically pre-filled with the URL you opened the dashboard from, so you can click **Load** straight away without typing anything.

The **Token** field remembers the last successfully used token across browser sessions (stored in `localStorage`). You can also press **Enter** in the Token field to trigger Load.

---

## Using the Dashboard

### Toggling Push Destinations

Each push destination in your nginx config appears as a row with three controls: a **toggle switch**, an **edit button (✎)**, and a **remove button (✕)**. Flipping the toggle will:

1. Comment out (disable) or uncomment (enable) that `push` line in `/etc/nginx/nginx.conf`
2. Run `nginx -s reload` automatically

> **Important:** Due to how nginx's graceful reload works, the config change does **not** take effect immediately for an active stream. The existing OBS connection keeps running in the old nginx worker and continues pushing to all destinations that were active when the stream started. The new config only applies when OBS reconnects.
>
> To apply the change immediately, use the **Force Reconnect** button (see below).

### Editing a Push Destination

Click the **✎** button on any push row to edit it. The row's current URL and label are loaded into the add form at the bottom of that application block. The **+ Add** button changes to **✓ Update** (highlighted in amber) and a **✕ Cancel** button appears.

Make your changes and click **✓ Update** to save, or **✕ Cancel** to discard. Editing is implemented as a remove-then-add, so nginx reloads as part of the update — stats will temporarily go blank and auto-recover (see [Stats after a config change](#stats-after-a-config-change) below).

### Force Reconnect

The **⏹ Force reconnect** button appears on each active stream card in the sidebar. Clicking it:

1. Terminates the OBS publisher connection via the nginx-rtmp control module
2. OBS auto-reconnects (if auto-reconnect is enabled in OBS)
3. The new connection uses the updated config — with the toggled destination now active or inactive

**Recommended OBS settings** for seamless reconnect: go to **Settings → Advanced → Network** and set Reconnect Delay to 2s with Maximum Retries set to 10 or higher.

**Typical workflow to stop a push mid-stream:**
1. Toggle the destination OFF in the dashboard
2. Click **⏹ Force reconnect** on the active stream card
3. OBS reconnects within a few seconds — the disabled destination is no longer pushed to

### Adding a Push Destination

At the bottom of each application block there is an **Add** form. Enter:
- The full RTMP URL (e.g. `rtmp://live.twitch.tv/app/your-stream-key`)
- An optional label (e.g. `Twitch`) — if left blank, the dashboard will guess the platform name from the URL

Click **+ Add**. The new push line is inserted into your nginx config and nginx reloads automatically.

### Removing a Push Destination

Click the **✕** button on any push row. A confirmation prompt will appear before anything is changed. Removing a destination deletes the line from your nginx config permanently.

### Managing Port Groups

A **port group** is an nginx `server {}` block inside the `rtmp {}` context — it defines a port that OBS (or any RTMP source) connects to. Each port group contains one or more applications with their own push destinations.

#### Adding a Port Group

Click **＋ Add Port Group** below the server list. A panel opens with two fields:

| Field | Description |
|---|---|
| **Port** | The TCP port nginx will listen on for RTMP connections (1–65535, must be unique) |
| **App name** | The application name OBS uses in the stream URL (default: `live`) |

As you type, a live preview shows the full OBS stream URL:

```
rtmp://your-server:PORT/APP/stream-key
```

Click **＋ Create** to add the port group. A minimal server block with one application is written to `nginx.conf` and nginx reloads automatically.

#### Deleting a Port Group

Click the **✕ Delete** button in the header of a port group. A confirmation modal appears listing all applications and push destinations that will be permanently removed. Click **Delete** to confirm. This deletes the entire server block from `nginx.conf` and reloads nginx.

> **Warning:** Deleting a port group is permanent and removes every application and push destination inside it. There is no undo.

### Stats after a config change

Any operation that writes to `nginx.conf` — adding, editing, or removing a push destination, or adding/deleting a port group — triggers an nginx reload. nginx starts fresh worker processes, and the RTMP stat module runs per-worker: new workers have no connection state yet, so the Live Stats sidebar and Active Streams panel temporarily go blank.

The dashboard handles this automatically:

1. An amber notice appears in the Active Streams panel explaining what happened.
2. The dashboard retries fetching stats at 3 s, 8 s, 15 s, and 25 s intervals.
3. Once your publisher reconnects to the new workers, stats restore on their own.

For instant recovery, click **⏹ Force reconnect** on the active stream card — OBS reconnects within a few seconds and stats return on the next poll.

> **Note:** Toggling a push destination does **not** cause this, because toggle does not restart nginx workers.

### Reloading nginx Manually

Click the **⟳ Reload nginx now** button in the sidebar to trigger a graceful reload at any time, without making any config changes.

---

## Understanding the Live Stats

The sidebar displays four numbers:

| Stat | What it means |
|---|---|
| **Streams** | Number of active publishers — i.e. how many sources (e.g. OBS) are currently connected and sending video to nginx |
| **Destinations** | Number of active outgoing push connections. Derived from nginx's `nclients` minus the publisher itself, so OBS streaming to 3 platforms shows 3 |
| **Bw In** | Bandwidth coming INTO nginx from OBS — this is the bitrate you set in OBS (e.g. 7.5 Mbit/s) |
| **Bw Out** | Total bandwidth going OUT of nginx to all push destinations combined. With 3 active destinations this is approximately 3 × Bw In |

As a quick sanity check: **Bw Out ≈ Bw In × (number of active push destinations)**.

The **Active Streams** section shows a card for each live stream with:
- Stream name (application/key)
- Per-stream inbound and outbound bitrate
- Active destination count
- Uptime

Stats refresh automatically on the interval selected in the **Auto-refresh** dropdown (default: every 5 seconds).

---

## Security

By default the API has **no authentication** and is accessible to anyone who can reach port 8088. This is fine if the server is on a private network, but you should secure it if it is internet-facing.

### Option 1 — Token Authentication (Recommended)

Token authentication is **enabled by default** during install. A cryptographically secure token is auto-generated and shown once:

```
Set an API secret token? [Y/n]: (press Enter)
Auto-generate a secure token? [Y/n]: (press Enter)
→ Generated: a3f8c2e1b7d94f2a6e3c8b1d5f7a2e4c9f1b3d5e7a2c4f6b8d0e2a4c6f8b0d2
  Copy this token — you will need it to access the dashboard.
```

Copy and save this token somewhere safe — you will need to enter it in the dashboard's **Token** field the first time you open it. The token is saved in your browser's `localStorage` after a successful use, so you only need to enter it once per browser.

If you prefer to set a token manually (or to change it later), edit `rtmp-api.py`:

```python
API_SECRET = "your-token-here"   # minimum 16 characters
```

Restart the service after changing the file:

```bash
sudo systemctl restart rtmp-control
```

### Option 2 — LAN-Only Mode

LAN-only mode restricts the dashboard to connections from your local network (private IP ranges: `10.x.x.x`, `172.16–31.x.x`, `192.168.x.x`) and the server itself. All connections from public internet IPs are rejected with a `403`.

**During install:** the installer asks *"Restrict dashboard access to local network only?"* — it is enabled by default. Press Enter to accept, or type `n` to disable it.

**From the dashboard:** open the **Access Control** widget in the sidebar. The toggle takes effect immediately — no restart required. A confirmation dialog appears when enabling, since enabling LAN-only from a remote (internet) connection will lock you out on the next request.

> **Warning:** If you enable LAN-only while connected from the internet, you will immediately lose access to the dashboard. To recover, SSH into the server and either:
> - Edit `rtmp-settings.json` in the install directory and set `"lan_only": false`, then restart the service, or
> - Toggle it back off by accessing the dashboard from the local network

### Option 3 — Firewall Rule

Block external access to port 8088 with UFW:

```bash
sudo ufw deny 8088
sudo ufw allow from 192.168.1.0/24 to any port 8088  # allow your local network only
```

### Option 4 — Reverse Proxy with nginx + HTTPS

For the most secure setup, proxy the API through nginx itself with HTTPS and HTTP Basic Auth. Add to your nginx config:

```nginx
location /rtmp-panel/ {
    auth_basic "RTMP Control";
    auth_basic_user_file /etc/nginx/.htpasswd;
    proxy_pass http://127.0.0.1:8088/;
}
```

Create a password file:

```bash
sudo apt install apache2-utils
sudo htpasswd -c /etc/nginx/.htpasswd yourusername
```

---

## Running as a Service (Auto-start on Boot)

To keep the API running in the background and have it start automatically on boot, create a systemd service:

```bash
sudo nano /etc/systemd/system/rtmp-control.service
```

Paste the following:

```ini
[Unit]
Description=RTMP Control Panel API
After=network.target nginx.service

[Service]
Type=simple
User=root
WorkingDirectory=/opt/rtmp-control
ExecStart=/usr/bin/python3 /opt/rtmp-control/rtmp-api.py
Restart=on-failure
RestartSec=5

[Install]
WantedBy=multi-user.target
```

Enable and start the service:

```bash
sudo systemctl daemon-reload
sudo systemctl enable rtmp-control
sudo systemctl start rtmp-control
```

Check it is running:

```bash
sudo systemctl status rtmp-control
```

View logs:

```bash
sudo journalctl -u rtmp-control -f
```

To update the files after making changes, copy them to the install directory and restart the service:

```bash
sudo cp rtmp-dashboard.html rtmp-api.py /opt/rtmp-control/
sudo systemctl restart rtmp-control
```

---

## nginx.conf Structure Reference

The API expects a standard nginx-rtmp config structure. It parses all `server` blocks inside the `rtmp { }` block, and all `application` blocks and `push` directives within each server. Both active push lines and commented-out push lines are detected and displayed in the dashboard.

A minimal working example:

```nginx
# ── Global context ────────────────────────────────────────────────────────────
worker_processes 1;   # Must be 1 — see note below
load_module modules/ngx_rtmp_module.so;

rtmp {
    server {
        listen 1935;
        chunk_size 4096;

        application live {
            live on;
            record off;

            # Twitch
            push "rtmp://live.twitch.tv/app/your-stream-key";

            # YouTube (disabled)
            #push "rtmp://a.rtmp.youtube.com/live2/your-stream-key";
        }
    }
}

http {
    server {
        listen 80;
        server_name localhost;
        location /stat    { rtmp_stat all; allow 127.0.0.1; deny all; }
        location /control { rtmp_control all; allow 127.0.0.1; deny all; }
    }
}
```

> **`worker_processes` must be set to `1`.**
> nginx-rtmp's stat module tracks connections inside each worker process's memory. With the default `worker_processes auto` (which spawns one worker per CPU core), HTTP requests for `/stat` may be routed to a *different* worker than the one handling the RTMP ingest connection — causing the dashboard to show zero streams and empty bandwidth figures even while OBS is actively streaming. Setting `worker_processes 1` ensures all connections share the same process, so stats are always accurate.

Label comments directly above a push line (lines starting with `#` that are not themselves a push directive) are used as the display label in the dashboard. For example:

```nginx
#Twitch
push "rtmp://live.twitch.tv/app/key";
```

…will appear in the dashboard as **Twitch**.

---

## Verification

### Check nginx is installed and running

```bash
sudo systemctl status nginx
```

### Check the RTMP module is installed

```bash
dpkg -l | grep libnginx-mod-rtmp
```

You should see a line beginning with `ii` (installed).

### Check your nginx config is valid

```bash
sudo nginx -t
```

Should output: `syntax is ok` and `test is successful`.

### Check the stat endpoint is working

```bash
curl http://localhost/stat
```

Should return an XML document containing your RTMP server info.

---

## Troubleshooting

**"Cannot read nginx.conf — run with sudo"**
The API must be started with `sudo`. Stop it and restart with `sudo python3 rtmp-api.py`.

**"Config load failed: Connection refused"**
The dashboard can't reach the API. Make sure `rtmp-api.py` is running and that port 8088 is not blocked by a firewall.

**Dashboard returns "Access restricted to local network" (403)**
LAN-only mode is enabled and you are connecting from a public IP. Access the dashboard from your local network, or SSH into the server and edit `rtmp-settings.json` in the install directory — set `"lan_only": false` and restart the service with `sudo systemctl restart rtmp-control`.

**"Stat unavailable"**
The API cannot reach `http://localhost/stat`. Make sure you have added the `/stat` location block to your nginx config with `server_name localhost;` and reloaded nginx. Confirm it works with `curl http://localhost/stat`.

**`nginx: [emerg] unknown directive "rtmp_stat"` after install**
The RTMP module is not being loaded by nginx. This can happen if you are using a generic or non-Ubuntu nginx.conf that does not include `/etc/nginx/modules-enabled/*.conf`. The installer detects this and adds a `load_module modules/ngx_rtmp_module.so;` line directly to nginx.conf. If you replaced nginx.conf after running the installer, re-run the installer or add the line manually at the very top of your nginx.conf.

**Toggle switches revert after clicking**
This usually means the API returned an error. Check the **Activity Log** in the dashboard sidebar for the error message.

**nginx config test fails after a toggle**
The API runs `nginx -t` before every reload and will refuse to reload if the test fails. Open `/etc/nginx/nginx.conf` in a text editor and look for any syntax errors near the line that was toggled.

**Disabling a push destination doesn't stop the stream immediately**
This is expected. nginx's graceful reload lets the existing stream connection keep running. Toggle the destination off, then click **⏹ Force reconnect** on the active stream card to force OBS to reconnect with the updated config. Make sure OBS has auto-reconnect enabled (Settings → Advanced → Network).

**Force Reconnect stops the stream but OBS doesn't reconnect**
OBS auto-reconnect is not enabled. Go to **Settings → Advanced → Network** in OBS and set Reconnect Delay to 2s and Maximum Retries to 10 or higher. After enabling, manually stop and restart the stream once in OBS to activate the new setting.

**Push destinations show as "Unknown"**
Labels are detected from a comment line immediately above the push directive in your config. Add a comment like `#Twitch` on the line above any push you want labelled, or set the label when adding a new destination through the dashboard.

---

## How Toggle Works (Technical Detail)

nginx-rtmp has no runtime API to add or remove push destinations. The toggle mechanism works around this by:

1. Finding the relevant `push "..."` line in `nginx.conf` by line number
2. Adding or removing a `#` comment character at the start of the line
3. Writing the modified config back to disk
4. Running `nginx -s reload`

`nginx -s reload` sends a `SIGHUP` signal to the master process. nginx starts new worker processes with the updated config and signals the old workers to gracefully finish their existing connections.

For RTMP, this means the existing OBS connection remains alive in the old worker process, which continues pushing to all destinations that were configured when the stream started — regardless of the config change. The new config only applies when OBS disconnects and establishes a fresh connection to a new worker.

This is why the **⏹ Force Reconnect** button exists: it uses the nginx-rtmp control module (`/control/drop/publisher`) to actively terminate the OBS connection, triggering a reconnect that picks up the new worker and updated config.

---

## Known Limitations

**RTMPS (`rtmps://`) is not supported.**
`nginx-rtmp-module` only supports plain RTMP. Push destinations using `rtmps://` URLs (required by Facebook Live, Instagram, and some other platforms) will not work. RTMPS support is planned for a future release.

---

## License

MIT — free to use, modify, and distribute.

---

Built with the help of [Claude](https://claude.ai) by Anthropic.
