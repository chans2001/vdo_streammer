# Manual remote deployment

This project is prepared for a **static Vercel frontend → public HTTPS tunnel → Windows Waitress/FFmpeg → USB Pi** connection. The browser contacts the backend directly. Vercel stores no captures and runs no camera code or proxy functions. Preparation does not deploy, open a tunnel, create accounts, change `.env`, or restart the working server.

The real backend hostname and Vercel production hostname do not exist in this project yet. Commands below prompt for the actual values issued by those services instead of inventing a URL.

## 1. Exact folders and Vercel settings

All commands start from the existing workspace:

```powershell
Set-Location -LiteralPath 'C:\Users\S250153\Workspace\vdo_streammer'
```

| Item | Exact value |
| --- | --- |
| Frontend source | `C:\Users\S250153\Workspace\vdo_streammer\frontend` |
| Prepared frontend upload folder | `C:\Users\S250153\Workspace\vdo_streammer\.deploy\frontend` |
| Vercel Framework Preset | **Other** (`framework: null`) |
| Build Command | **Empty / skipped** (`buildCommand: ""`) |
| Install Command | **Empty / skipped** (`installCommand: ""`) |
| Output Directory | **`.`** |
| Project Root Directory for this manual folder upload | **`.`** / no subdirectory override |
| Vercel environment variables | **None required** |
| Functions / rewrites / API proxy | **None** |

The upload folder is itself the frontend root: do not set its Root Directory to `frontend` again. `frontend/vercel.json` explicitly fixes the static settings and prevents caching `config.js`. The preparer adds a CSP allowing connections to only the chosen backend and the frontend itself. No Vercel Functions, rewrites, or framework build are introduced. These settings follow [Vercel's static build instructions](https://vercel.com/docs/builds/configure-a-build) and [configuration reference](https://vercel.com/docs/project-configuration/vercel-json).

The preparation helper copies an allowlist: `index.html`, `app.js`, `styles.css`, `favicon.svg`, `config.js`, `vercel.json`, and `.vercelignore`. It never reads `.env` or copies backend code, recordings, tests, logs, or virtual environments. A frontend `.vercelignore` allows only those public assets. The repository-root `.vercelignore` excludes everything to guard against an accidental root upload. Use the prepared directory, not the full repository. [Vercel documents this allowlist mechanism](https://vercel.com/docs/deployments/vercel-ignore).

## 2. Keep the Windows backend running

Use the already working server. If it is stopped, with the Pi streaming command running and desktop MouseCam/ffplay closed:

```powershell
.\start.ps1
```

Keep the existing `.env` settings `MOUSECAM_PORT=8080` and `MOUSECAM_SIMULATED=false`. Keeping `MOUSECAM_HOST=0.0.0.0` preserves current lab-LAN access; the tunnel still connects over loopback. If you later want tunnel-only access, `MOUSECAM_HOST=127.0.0.1` intentionally removes direct LAN access and requires a backend restart. Do not change it just to enable the tunnel.

No router port forwarding or new public inbound TCP 8080 rule is required. Leave the Pi's `192.168.77.2` and laptop USB `192.168.77.1` addresses private; neither goes into the frontend config. The laptop, Pi listener, backend process, and tunnel connector must all stay running and awake. A tunnel service does not start Python/FFmpeg or the Pi for you.

## 3. Install the Windows tunnel connector manually

These commands download Cloudflare's official Windows x64 executable into a new ignored tools folder. No installer path is assumed. Run only after arranging an institution-approved tunnel/outbound network route:

```powershell
Set-Location -LiteralPath 'C:\Users\S250153\Workspace\vdo_streammer'
New-Item -ItemType Directory -Force -Path '.tools' | Out-Null
Invoke-WebRequest -Uri 'https://github.com/cloudflare/cloudflared/releases/latest/download/cloudflared-windows-amd64.exe' -OutFile '.tools\cloudflared.exe'
& .\.tools\cloudflared.exe --version
```

The URL is the official [Cloudflare Windows download](https://developers.cloudflare.com/cloudflare-one/networks/connectors/cloudflare-tunnel/downloads/). Windows versions require manual updates. Do not overwrite an executable while its tunnel/service is running; schedule updates separately.

## 4. Choose temporary testing or a stable hostname

**Temporary test tunnel:** in a second PowerShell window, leaving the backend running:

```powershell
Set-Location -LiteralPath 'C:\Users\S250153\Workspace\vdo_streammer'
& .\.tools\cloudflared.exe tunnel --url http://127.0.0.1:8080
```

Copy the exact `https://...trycloudflare.com` URL printed by the command. This is your backend URL for that test session. Keep the window open. Quick Tunnels need no account/domain, change hostname on recreation, have no uptime guarantee, allow at most 200 in-flight requests, and do not support SSE. **They are for testing, not a stable five-person deployment.** A new hostname requires preparing and redeploying the frontend again. [Quick Tunnel documentation](https://developers.cloudflare.com/tunnel/get-started/quick-tunnels/).

**Stable deployment:** manually use a Cloudflare account and a domain you control on Cloudflare. In the dashboard, create a managed tunnel under **Networking → Tunnels**. Add a **Published application** route whose hostname is your chosen stable backend hostname and whose service URL is exactly **`http://127.0.0.1:8080`** (HTTP to the laptop; HTTPS from the browser to the public endpoint). Keep path matching unrestricted so `/api/login`, `/api/preview.jpg`, controls, and media all reach Flask. Use the dashboard's Windows connector installation command. The command format in an authorized administrator PowerShell is:

```powershell
Set-Location -LiteralPath 'C:\Users\S250153\Workspace\vdo_streammer'
$mousecamTunnelToken = Read-Host 'Paste the private tunnel connector token from the Cloudflare dashboard'
& .\.tools\cloudflared.exe service install $mousecamTunnelToken
Remove-Variable mousecamTunnelToken
Get-Service cloudflared
```

The token is a connector secret, not the lab access code. Keep it in the service configuration; never copy it into `.env.example`, frontend files, Vercel environment variables, screenshots, or source control. This service-install command is for a new connector; if a cloudflared service already exists, use the provider's instructions for that existing connector rather than replacing it. Source: [managed tunnel setup and Windows service command](https://developers.cloudflare.com/tunnel/get-started/).

Configure backend-host `/api/*` traffic to bypass CDN caching; retain the app's `Cache-Control: no-store`, Authorization, Origin, and CORS response headers. Do not add a Cache Everything rule. The default app does not trust forwarded IP/scheme headers; login rate limits behind the connector apply collectively to its loopback address (10 login attempts per minute), which is acceptable for the five-user MVP. Do not enable trust in arbitrary client-supplied forwarded headers.

These instructions use MouseCam's access-code authentication. An additional Cloudflare Access/email challenge is a separate authentication integration: do not add `--allowed-mail` or an interactive gateway in front of cross-site API fetches and assume it will work with blocked third-party cookies. If such a gateway is required by your institution, arrange that integration rather than bypassing it or putting service credentials in the frontend. If outbound tunnel connectivity is blocked, ask IT; do not disable firewalls or evade network policy. Cloudflare documents port 7844 as a connector connectivity requirement.

**Transport compatibility:** this app fetches finite authenticated `image/jpeg` responses sequentially, at up to 5 fps per viewer, plus ordinary JSON and file downloads. It uses neither SSE nor WebSockets nor browser MJPEG fetches. Quick Tunnel's SSE restriction therefore does not conflict with the actual transport; five viewers have at most five preview fetches in flight, plus status/control/download requests. The laptop uplink and tunnel/provider limits still need real testing. Long media downloads use a full Blob on iPhones and can exceed memory or the client's five-minute download deadline. Do not proxy any preview/video through Vercel Functions. Actual tunnel performance and home/mobile reachability are not validated until you perform section 8.

## 5. Prepare the public frontend URL without changing local mode

In another PowerShell window:

```powershell
Set-Location -LiteralPath 'C:\Users\S250153\Workspace\vdo_streammer'
$mousecamBackendUrl = Read-Host 'Paste the real public HTTPS backend origin (no /api path)'
.\.venv\Scripts\python.exe tools\prepare_frontend.py --api-base-url $mousecamBackendUrl
if ($LASTEXITCODE -ne 0) { throw 'Frontend preparation failed; do not deploy.' }
Get-ChildItem -Force -LiteralPath '.deploy\frontend'
Get-Content -LiteralPath '.deploy\frontend\config.js'
```

The helper requires HTTPS, rejects private/loopback addresses, URL credentials, query strings, and API paths, and refuses unexpected files in an existing upload folder. It writes the exact supplied origin to the project's actual browser runtime setting, **`window.MOUSECAM_CONFIG.API_BASE_URL`**, in `.deploy/frontend/config.js`. `frontend/config.js` remains `API_BASE_URL: ""`, so Flask-served laptop/phone pages continue to use relative, same-origin URLs. Vercel dashboard variables named `API_BASE_URL` or `VITE_API_BASE_URL` do **not** modify these plain static files. Regenerate and redeploy whenever the real backend URL changes.

## 6. Manually deploy only the prepared frontend

Node/npm are available on this laptop. The commands use `npx.cmd` to avoid PowerShell npm-script execution-policy issues. Run them yourself; none were run during preparation:

```powershell
Set-Location -LiteralPath 'C:\Users\S250153\Workspace\vdo_streammer'
npx.cmd --yes vercel@latest login
npx.cmd --yes vercel@latest link --cwd .deploy/frontend
```

Choose your account/team and create or select the frontend-only Vercel project. Before deployment, confirm the settings from section 1 in **Project Settings → Build and Deployment**: Other, empty build/install commands, output `.`, and no extra root subdirectory. Do not import or connect the entire backend repository for this manual-folder workflow. The prepared `vercel.json` supplies the static settings and headers. Then deploy:

```powershell
npx.cmd --yes vercel@latest deploy --prod --cwd .deploy/frontend
```

This command is the actual external deployment step. It uploads only the prepared public assets. Do not use `--prebuilt`; this directory is ordinary static content, not the Vercel Build Output API layout. Use the stable **production domain** shown under the project's Domains (or its configured custom domain), not an automatically generated preview/deployment URL. CLI references: [login](https://vercel.com/docs/cli/login), [link](https://vercel.com/docs/cli/link), [deploy](https://vercel.com/docs/cli/deploy).

## 7. Allow the actual Vercel production origin on the backend

Open the existing backend config privately:

```powershell
Set-Location -LiteralPath 'C:\Users\S250153\Workspace\vdo_streammer'
notepad .env
```

Set **`MOUSECAM_ALLOWED_ORIGINS`** to the exact `https://` production origin from Vercel: no path, trailing slash, wildcard, or spaces within the origin. The value is a comma-separated list if there are multiple intentionally approved frontend domains. Keep the existing `MOUSECAM_ACCESS_CODE`, FFmpeg, capture directory, camera URL, and port values unchanged. Neither the access code nor `.env` belongs in Vercel.

Stop any shared recording and wait for its saved state. In the backend's existing terminal press **Ctrl+C**, wait for shutdown, restart the Pi streaming command if its listener exited, and run **`.\start.ps1`** again. Keep the tunnel running. Backend settings are read at startup, and restarting invalidates sessions. Check for an existing process-level `MOUSECAM_ALLOWED_ORIGINS` override if changes to `.env` appear ignored; environment variables take precedence.

Local LAN pages still work without adding their origins because they are same-origin with Flask. A Vercel preview hostname is denied until explicitly approved. The public backend shell is not the intended remote entry point; if you also want to sign in directly on that HTTPS shell, explicitly add the backend's HTTPS origin too (TLS terminates at the tunnel while Waitress sees HTTP). This is optional and does not affect Vercel-to-backend authentication.

## 8. Validate the real path after your deployment

First check the public backend from PowerShell, using the actual origins you copy from the providers:

```powershell
$mousecamBackendUrl = (Read-Host 'Paste the public HTTPS backend origin').TrimEnd('/')
$mousecamFrontendOrigin = (Read-Host 'Paste the exact Vercel production origin').TrimEnd('/')
curl.exe -i "$mousecamBackendUrl/api/status"
curl.exe -i -X OPTIONS "$mousecamBackendUrl/api/preview.jpg" -H "Origin: $mousecamFrontendOrigin" -H 'Access-Control-Request-Method: GET' -H 'Access-Control-Request-Headers: authorization'
```

Expect **401 JSON** from the unauthenticated status call and **204** from the preflight with `Access-Control-Allow-Origin` exactly matching your Vercel origin and Authorization allowed. These commands do not expose credentials. A 403 points to an origin mismatch, a 502 to backend/tunnel reachability, and an HTML login/challenge response to a gateway in front of the API. Do not solve those by adding wildcard CORS or weakening authentication.

Then on the iPhone, leave Safari's cross-site tracking protection enabled, disconnect lab Wi-Fi, and use mobile data to open the production frontend. Repeat from home Wi-Fi and another authorized user's device:

1. Sign in with the shared code. Confirm Network requests go to the public HTTPS backend, never localhost or USB/LAN private IPs.
2. Confirm fresh preview renders and becomes visibly unavailable if the path is interrupted.
3. Save/view/download a photo; start one shared recording, see its timer on the second device, and stop it there. Verify closing/signing out of one page does not stop it.
4. View/download the saved MP4. Validate a representative long download on the actual iPhone, not just a tiny sample. Refresh/sign in again and confirm the library remains intact.
5. Confirm unauthenticated preview/media/control requests are denied and a disallowed Origin receives no permissive CORS header. Test all five users together before relying on the laptop uplink.

The code authenticates login with a JSON access code and subsequent API/preview/media requests with a short-lived bearer token in memory. Fetch uses `credentials: "omit"`; it never needs a third-party cookie or puts credentials in URLs/localStorage. The local HTTPS integration test uses an empty cookie jar, makes cookie APIs unavailable, requires `credentials: "omit"` on every fetch, and checks that no Cookie/Set-Cookie headers are exchanged. This verifies the app's cookie-independent design, **not the real Vercel/tunnel/iPhone route**. Actual public-domain and iPhone remote operation remains a post-deployment check.

## Local preparation checks

```powershell
Set-Location -LiteralPath 'C:\Users\S250153\Workspace\vdo_streammer'
.\.venv\Scripts\python.exe -m pytest -q
.\.venv\Scripts\python.exe tests\browser_remote.py --browser webkit
.\.venv\Scripts\python.exe tests\browser_remote.py --browser edge
```

The remote-mode test uses two different local HTTPS sites (`localhost` and `127.0.0.1`), a test-only self-signed certificate, a local TLS proxy in front of Waitress, the prepared frontend, and a simulated camera. OpenSSL is available here at `C:\Program Files\Git\mingw64\bin\openssl.exe`; the script discovers it on PATH. It installs no certificate trust, changes no public DNS, opens no tunnel, and deploys nothing. Public TLS validation must remain enabled in real use. Test captures/certificates are temporary and removed afterward. See `VERIFICATION.md` for actual results.
