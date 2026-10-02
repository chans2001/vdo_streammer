# MouseCam web

A small shared camera room for five lab members. One Windows Python process owns the Pi connection and saves captures on the laptop. Flask serves a standalone HTML/CSS/JavaScript frontend; Waitress serves HTTP with 24 threads. No database or cloud services.

## Windows setup and local startup

Use PowerShell in this directory with Python 3.13 installed:

```powershell
.\setup.ps1
notepad .env
.\start.ps1 -Simulated
```

`setup.ps1` creates `.venv`, installs dependencies, and creates a private `.env` with a random lab access code. Read `MOUSECAM_ACCESS_CODE` in that file and share it privately with lab members. It is never included in frontend files. Setup preserves an existing `.env`. If organizational policy prevents PowerShell scripts, use the equivalent commands below; do not bypass that policy:

```powershell
py -3.13 -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
Copy-Item .env.example .env
notepad .env
# Set a strong, random MOUSECAM_ACCESS_CODE of at least 12 characters first.
$env:MOUSECAM_SIMULATED = 'true'
.\.venv\Scripts\python.exe -m mousecam_web
# After stopping the server, clear this override before real-camera startup:
Remove-Item Env:MOUSECAM_SIMULATED
```

Open **http://localhost:8080** on the laptop and sign in. The simulated source shows a moving FFmpeg test pattern, with a prominent **SIMULATED CAMERA** notice. Take a photo, start and stop a recording, then view/download both in the library. Stop the server with **Ctrl+C** and wait for finalization before closing the terminal.

For the real camera, leave `MOUSECAM_SIMULATED=false` in `.env` and run `./start.ps1` without `-Simulated`. Environment variables override `.env` values. Do not run Flask's development server, debug reloader, multiple WSGI workers, or several server instances.

## Connect the Pi camera

The hardware is an **original Raspberry Pi Zero W** with an Arducam B0444 Pivariety. Connect its existing USB network to the laptop. **Close desktop MouseCam, ffplay, and any other Pi camera clients first.** The Pi listener supports the single shared upstream connection used by this server.

In your existing Pi SSH terminal, run manually:

```sh
rpicam-vid --nopreview --timeout 0 --width 1280 --height 720 --framerate 15 --codec h264 --bitrate 2000000 --inline --listen --output tcp://192.168.77.2:8888
```

Leave that terminal running, then start the web server:

```powershell
.\start.ps1
```

After a disconnect, the current Pi listener may exit and **need its streaming command restarted**. The backend makes at most five attempts per failure episode, waiting 1, 2, 4, then 8 seconds between attempts. Its budget resets only after a sustained stream of at least 30 seconds and 300 frames. When attempts are exhausted, restart the Pi command and then the web server. No Pi OS, driver, or SSH changes are made; no SSH passwords are stored.

## Open it from a phone or another laptop

Run `ipconfig` on the server laptop. Find the **IPv4 address under the active Wi-Fi or Ethernet adapter connected to the lab network**. For example, if that address is `10.20.30.40`, lab users visit `http://10.20.30.40:8080` while connected to that network.

These addresses have different roles:

| Address | Purpose |
| --- | --- |
| `192.168.77.2` | Pi's USB address; camera stream only |
| `192.168.77.1` | Laptop's Pi-facing USB adapter; not the normal lab-client address |
| Laptop Wi-Fi/Ethernet IPv4 | Address phones and lab members use |
| `localhost` / `127.0.0.1` | The device opening the URL itself |

Keep the laptop plugged in, awake, connected to both USB and the lab network, and keep its server terminal open. Follow your lab's power policy; sleep, lid closure, USB removal, or loss of power interrupts recording. Prefer pressing Stop and waiting for “saved” before maintenance. There is no automatic retention/deletion: monitor disk space and back up captures as appropriate.

Plain HTTP exposes the shared code, session token, and camera traffic to the network. Use it **only on a trusted lab network**. Internet access requires HTTPS. Institutional Wi-Fi client isolation may prevent device-to-device connections; ask IT for an approved route instead of bypassing restrictions.

### Firewall and connectivity

Start with `http://localhost:8080`. If that works but the phone cannot connect, verify the actual LAN IP, same lab network, network isolation policy, and the Windows inbound rule. Do not disable the firewall or change a managed network profile. If IT authorizes a rule, restrict it to this Python executable, TCP port 8080, the laptop's actual LAN address, the lab's actual subnet, and the approved network profile. For example, **replace all example addresses/profile** before running in an authorized administrator PowerShell:

```powershell
New-NetFirewallRule -DisplayName 'MouseCam lab TCP 8080' -Direction Inbound -Action Allow -Protocol TCP -LocalPort 8080 -Program 'C:\Users\S250153\Workspace\vdo_streammer\.venv\Scripts\python.exe' -LocalAddress '10.20.30.40' -RemoteAddress '10.20.30.0/24' -Profile Domain
# Remove that one rule when no longer needed:
Remove-NetFirewallRule -DisplayName 'MouseCam lab TCP 8080'
```

Camera troubleshooting:

- No fresh frames: close all other viewers, restart the Pi command, check the USB cable/adapter and Pi address, then restart this server. Status becomes stale after three seconds without frames, and capture actions are disabled.
- `ping 192.168.77.2` checks basic reachability if ICMP is enabled. A TCP probe such as `Test-NetConnection 192.168.77.2 -Port 8888` can consume the Pi's single listener connection: do it only with the backend stopped and restart the Pi command afterward.
- FFmpeg missing: set `MOUSECAM_FFMPEG` to the full executable path. Discovery checks PATH and `~/Downloads/mousecam-ffmpeg/**/ffmpeg.exe`. This machine's detected installation is `C:\Users\S250153\Downloads\mousecam-ffmpeg\ffmpeg-9.0.2-essentials_build\bin\ffmpeg.exe`.
- Recording overload or disk errors: inspect the status/library error and terminal. Stop other heavy laptop work, free storage, and validate with a short recording. The app stops an overloaded recording instead of silently discarding frames.
- Login rate limit: wait one minute. Sessions last eight hours by default. Refreshing a page asks for the access code again because the token lives only in page memory. Existing shared recordings keep running.
- iPhone preview: the frontend uses finite authenticated `image/jpeg` requests in both hosting modes. WebKit cancels `multipart/x-mixed-replace` fetched as a raw resource; the old local MJPEG fetch path produced “Load failed” even with HTTP 200. Reload the page to get the updated JavaScript. “Live camera” now requires a recently decoded image on this device as well as fresh camera status; a preview failure stays visible through status polls. The local `API_BASE_URL` must stay empty, not `localhost` or `127.0.0.1`.

## Configuration

Copy `.env.example` to `.env` manually only if setup has not already created it. Never commit `.env` or copy it into `frontend/`.

| Variable | Default |
| --- | --- |
| `MOUSECAM_ACCESS_CODE` | Required, at least 12 characters; setup generates a random code |
| `MOUSECAM_FFMPEG` | Auto-discovered |
| `MOUSECAM_CAMERA_URL` | `tcp://192.168.77.2:8888` |
| `MOUSECAM_CAPTURE_DIR` | `captures`, relative to this repository |
| `MOUSECAM_SIMULATED` | `false` |
| `MOUSECAM_HOST`, `MOUSECAM_PORT` | `0.0.0.0`, `8080` |
| `MOUSECAM_THREADS` | `24`, minimum 16 |
| `MOUSECAM_MIN_FREE_MB` | `512` MiB reserve, checked before capture and once per second during recording |
| `MOUSECAM_SESSION_SECONDS` | `28800` (8 hours, absolute expiry) |
| `MOUSECAM_STALE_SECONDS` | `3` |
| `MOUSECAM_RECONNECT_ATTEMPTS` | `5` |
| `MOUSECAM_ALLOWED_ORIGINS` | Empty: no cross-origin access |

Restart the server after backend configuration changes. Existing completed captures are preserved; sessions are invalidated on restart. Keep the capture directory writable only by trusted laptop users. The operating-system-held lock in that directory prevents a second owner; binding the HTTP port happens before camera acquisition.

## Manual remote deployment

See [REMOTE_DEPLOYMENT.md](REMOTE_DEPLOYMENT.md) for the exact Windows tunnel commands, Vercel settings, runtime URL configuration, origin allowlist, and remote acceptance steps.

The source frontend remains `frontend/`, with `API_BASE_URL: ""` for local same-origin use. `tools/prepare_frontend.py --api-base-url <your-real-HTTPS-origin>` creates a separate allowlisted `.deploy/frontend/` copy for your manual Vercel upload, using `window.MOUSECAM_CONFIG.API_BASE_URL`. It leaves the working source frontend and `.env` unchanged. Vercel uses Other, no build/install commands, output `.`, and no functions or API proxy. Backend configuration is still `MOUSECAM_ALLOWED_ORIGINS` in the private laptop `.env`.

Remote authentication uses an in-memory bearer token and authenticated JPEG fetches, not third-party cookies. Public-domain HTTPS/tunnel performance and actual iPhone access from mobile data still require the manual checks in that guide. No public deployment or tunnel was created during preparation.

## Recording, metadata, and timing limits

`mousecam_web/media.py` extracts the useful FFmpeg discovery, exact raw-frame reading, decoder, and encoder command patterns from the working desktop source discovered at `C:\Users\S250153\Downloads\MouseCam-Control-v1.1\MouseCam-Control\mousecam.py`. That original desktop app and its launcher remain untouched; the workspace was initially empty. The new service has no Tkinter dependency.

- One acquisition worker launches one FFmpeg decoder for the Pi stream. There is one latest full-resolution RGB frame and one 640×360 JPEG preview. Preview encoding runs separately at about 5 fps. All browsers make sequential authenticated JPEG fetches at up to 5 fps, with only one preview request in flight per viewer and a five-second request/body/decode timeout. The UI marks a displayed frame stale after three seconds. The authenticated MJPEG API remains available for compatible clients (eight slots, minute-long streams, per-frame session checks), but the website does not fetch multipart streams. The server keeps 24 Waitress threads. Slow viewers never hold the acquisition lock while sending data.
- The laptop re-encodes decoded frames as 1280×720 H.264, nominal 15 fps, yuv420p MP4, without audio. Input rawvideo `-framerate 15` sets the recording timeline. No output `-r` is combined with `-fps_mode passthrough`.
- A separate recording writer uses a 30-frame FIFO (about 79 MiB of RGB frame data). When full, it stops recording and reports the known dropped frame count. An encoder progress watchdog kills stalled encoders rather than indefinitely blocking acquisition or shutdown. Acquisition and recording survive browser refresh, close, and sign-out.
- Concurrent starts return the same active recording. Stop is idempotent and finalizes asynchronously. New starts during saving return the existing saving state. Stream loss/staleness and normal server shutdown finalize the recording with an interruption reason. Low storage rejects new captures and interrupts active recording. The reserve is not a disk quota; other processes can consume it between checks.
- Unique UTC-plus-UUID names and exclusive output creation avoid overwrites. An encoder writes `.partial.mp4` and only renames after successful finalization. The library lists completed, interrupted, and failed metadata, clearly distinguished; only successfully finalized completed/interrupted files can be viewed/downloaded. Active/unfinished files cannot be served. Failed partial files stay on disk for manual inspection; they are never advertised as playable completed recordings.
- Each capture has a small JSON sidecar. Videos store UTC start/end times, intended FPS, frame count, interruption reason, known locally dropped frames, wall time, and encoded duration. Unknown upstream loss is `null`, **not zero**. A crash/power failure can prevent MP4 finalization; on restart, unfinished sidecars become failed/interrupted with unknown end time and frame count. Completed files are retained.
- Photos save the latest fresh decoded 1280×720 frame as JPEG. They are stream snapshots, not independent full-sensor exposures.
- **Wall-clock elapsed time is not encoded video duration.** The UI timer measures wall time; encoded duration is received/written frames divided by 15. The raw stream does not provide validated experimental timestamps. Network gaps, decoder behavior, and laptop overload can change timing or lose upstream frames without a measurable upstream drop count. Scientific timing, long-session reliability, and uninterrupted recording require hardware validation.

API routes are under `/api/`: `login`, `logout`, `status`, `preview.jpg`, `preview.mjpeg`, `photos`, `recording/start`, `recording/stop`, `captures`, and `media/<generated-name>`. Everything except login requires a valid bearer token; frontend shell files alone are public. Filenames use a strict generated-name allowlist, metadata completion checks, and resolved-path/symlink checks.

## Verification

```powershell
.\.venv\Scripts\python.exe -m pip install -r requirements-dev.txt
.\.venv\Scripts\python.exe -m pytest -q
.\.venv\Scripts\python.exe tests\browser_smoke.py
```

The pytest suite and general browser smoke script use simulated FFmpeg sources and temporary capture folders; they never connect to the Pi. `ffprobe` is expected beside FFmpeg for the MP4 verification test. The browser smoke script uses an installed Microsoft Edge through Playwright by default (no browser download needed) and writes ignored screenshots under `test-results/`.

For the additional WebKit preview regression, install the test engine locally, then test against the **actual running LAN URL** (replace the address if it changes). This script reads the code privately from `.env`, signs in, checks images and failure recovery, and signs out without taking photos or changing recordings:

```powershell
$env:PLAYWRIGHT_BROWSERS_PATH = Join-Path $PWD '.browser-tools'
.\.venv\Scripts\python.exe -m playwright install webkit
.\.venv\Scripts\python.exe tests\browser_preview.py --url http://172.17.157.129:8080 --browser webkit
.\.venv\Scripts\python.exe tests\browser_preview.py --url http://172.17.157.129:8080 --browser edge
.\.venv\Scripts\python.exe tests\browser_smoke.py --browser webkit --skip-video-playback
```

The Windows WebKit test engine is not a physical iPhone or the shipped iOS Safari browser. Its MP4 playback check timed out here, so the last command explicitly skips that unrelated check while testing preview/capture/download behavior; the default Edge smoke test includes MP4 playback. The preview incompatibility is also visible in [WebKit's multipart subresource handling](https://github.com/WebKit/WebKit/blob/main/Source/WebCore/loader/SubresourceLoader.cpp), which cancels multipart non-image resources such as fetch loads.

See [VERIFICATION.md](VERIFICATION.md) for the checks actually performed and their limitations. `.gitignore` excludes `.env`, generated captures, logs, virtual environments, browser outputs, and Python caches.

Implementation references: [FFmpeg command options](https://www.ffmpeg.org/ffmpeg.html), [Waitress server settings](https://docs.pylonsproject.org/projects/waitress/en/latest/arguments.html), and [Flask security guidance](https://flask.palletsprojects.com/en/stable/web-security/).
