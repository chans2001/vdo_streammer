# Verification performed — 2026-10-02

## iPhone preview diagnosis and LAN recheck

The report was reproduced against the existing running backend at **http://172.17.157.129:8080**, the laptop's Wi-Fi IPv4 address confirmed with `ipconfig`. The application had `API_BASE_URL: ""`; requests used that LAN origin, not loopback. Authenticated status, JPEG, and MJPEG probes returned HTTP 200. The backend reported fresh frames and `simulated: false`. JPEG returned `image/jpeg` and valid JPEG bytes; MJPEG returned `multipart/x-mixed-replace; boundary=frame` with valid parts. Authentication used bearer headers, no cookies were set, and no CORS relaxation was needed.

**Before the fix**, Playwright WebKit 26.6 on Windows reproduced repeated `Load request cancelled` failures for `/api/preview.mjpeg`, the displayed `Preview unavailable / Load failed`, a decoded image width of zero, and the misleading `Live camera` label. This matches [WebKit's explicit cancellation of multipart non-image subresources](https://github.com/WebKit/WebKit/blob/main/Source/WebCore/loader/SubresourceLoader.cpp); a `fetch()` resource is not an image resource even if its parts contain JPEG images.

The frontend now uses sequential authenticated JPEG requests in both hosting modes, checks the response MIME type, and waits for the displayed image's load/decode before marking the preview healthy. Requests include a five-second deadline across fetch/body/decode; status polling marks a frame unhealthy after three seconds without a successful render. Network, MIME, or decoding failure cannot be overwritten by fresh upstream status. Stop Recording remains available for an active recording even when preview fails. Tokens remain in memory and out of URLs, cookies, and persistent browser storage. The authenticated MJPEG endpoint is retained for compatible external clients.

**After the fix**, `tests/browser_preview.py --url http://172.17.157.129:8080` passed with both `--browser webkit` and `--browser edge` against the existing nonsimulated camera service. Each verified decoded 640×360 images, repeated same-origin JPEG requests carrying bearer authentication, no cookies or multipart fetches, and recovery after injected network errors, wrong MIME types, and corrupt JPEG bytes. Failures remained visible across at least two successful camera-status polls. No captures or recording controls were changed by this regression script. The complete backend suite passed: **35 tests in 17.35 seconds**.

The existing server logs to its owning console, not a file. No persisted project/capture `.log` files were available, and that already-open terminal's historical output was not accessible here. Diagnosis used actual LAN HTTP responses plus WebKit request-failure events rather than claiming a nonexistent server exception. The application server/Pi connection was not restarted and no firewall changes were made. Flask serves the updated static JavaScript immediately; existing browser tabs need a reload.

Additional integration checks after the frontend fix:

- `tests/browser_smoke.py --browser edge`: passed, including five JPEG viewers sharing one source, photos, shared recording, MP4 playback, downloads, and the separately served frontend with explicitly allowed CORS.
- `tests/browser_smoke.py --browser webkit --skip-video-playback`: passed, including five JPEG viewers, photo viewing/download, shared recording, separate-origin preview/authentication/controls/video download, and stale indication. No window JavaScript errors or unhandled rejections. This is a simulated-source integration test in addition to the real LAN preview tests above.
- The full Windows WebKit smoke run timed out waiting for MP4 playback, even after requesting play. That is **not** claimed as validated or fixed by this preview change; the focused run explicitly skips playback and still checks video download. Edge playback and backend ffprobe verification passed.
- During earlier WebKit trials, an explicitly cancelled fetch sometimes appeared as a Playwright `pageerror` access-control diagnostic, including on the same origin. The harness now distinguishes these browser network diagnostics from actual window errors/unhandled rejections, while still requiring successful preview and control requests. Two concurrent full-browser trials also failed recording-state checks; the final smoke runs were performed sequentially. This is not a sustained-load benchmark.

**Limits:** Windows WebKit is not the actual iPhone/iOS build. LAN-address tests ran from this laptop, so they do not independently prove the phone-to-laptop Wi-Fi path (the user reported the site loads on the phone). No Vercel deployment or public HTTPS path was tested. The earlier implementation-only checks below are historical and predate these live-server checks.

## Initial implementation checks

Executed in this Windows workspace using Python 3.13, the discovered FFmpeg/ffprobe installation, Flask 3.1.3, Waitress 3.0.2, Pillow 12.3.0, pytest 9.1.1, and Playwright 1.63.0 with the installed Microsoft Edge.

## Results

- `./setup.ps1`: passed. Created the workspace virtual environment and a private ignored `.env` with a random access code. The access code was not printed or committed.
- `.\.venv\Scripts\python.exe -m pytest -q`: **34 passed** in 13.67 seconds at the full-suite checkpoint.
- Added and ran `.\.venv\Scripts\python.exe -m pytest -q tests\test_mousecam.py::test_raw_h264_tcp_pipeline`: **1 passed** in 2.22 seconds. There are now **35 passing automated tests** total; this last test exercised a synthetic raw H.264 TCP listener on loopback using the real decoder command, without connecting to the Pi.
- `.\.venv\Scripts\python.exe tests\browser_smoke.py`: **passed** against real local Waitress and Edge after the final frontend correction, with no JavaScript page errors. Five authenticated local browser contexts used a single acquisition process. Tested live MJPEG, full-resolution photo saving/viewing/download, shared start/stop, elapsed timer, refresh/sign-out independence, MP4 playback, and stale overlay/disabled controls. A second local HTTP origin tested CORS preflight, bearer authentication, JPEG fetch preview, shared recording, and MP4 download without cookies.
- Screenshots captured and visually inspected at **1440×1000 laptop** and **390×844 phone-sized** viewports. Corrected a long-filename toast wrapping issue; the final smoke run regenerated `test-results/laptop.png` and `test-results/phone.png`. These are ignored local artifacts.
- `pip check`: no broken requirements. `git diff --check`: no whitespace errors in tracked changes; newly created project files remain uncommitted. Confirmed `.env`, `.venv/`, and `test-results/` are ignored.

Backend coverage includes unauthorized API/control/preview/media access; login throttling, token expiry/revocation and rejection of cookie-only authentication; allowed/disallowed/default-denied origins; traversal and unfinished-file rejection; ten concurrent starts creating one recording; repeated concurrent stops; source loss and stale detection with finalization; normal server-process shutdown and single-owner lock; low-storage rejection and interruption; encoder failure; bounded queue overload; crash metadata recovery; and preservation of completed captures across service reinitialization.

ffprobe verified a generated MP4 was **H.264, 1280×720, 15/1 fps, one video stream, no audio**, with decoded frame count matching the sidecar and encoded duration matching `frame_count / 15` within 0.08 seconds. HTTP byte-range delivery was checked as well. The actual server-process shutdown test sent Windows CTRL_BREAK to a separate test process and verified successful MP4 finalization with interrupted metadata.

The original desktop source in Downloads was read but never edited. Its inspected SHA-256 is `2EEB5480D64948B4473F062D15A46F86781F8EA91872376E85E53CAEB9A871EF`.

## Not performed

- No connection to the physical Pi, Arducam, or USB camera listener.
- No actual phone, Safari/iOS browser, lab Wi-Fi/Ethernet path, or firewall-rule test. Phone checks used an Edge viewport, not a physical phone.
- No Vercel deployment, HTTPS backend/tunnel setup, internet access, or real cross-site HTTPS authentication test. Two localhost origins are only a local integration test.
- No long-duration recording, power-loss recovery of playable partial MP4s, scientific timestamp calibration, upstream dropped-frame measurement, or measurement of performance on the actual camera path.
- The streaming file-picker download path was not browser-automated; the Blob download fallback was exercised. Full-file Blob viewing/downloads can exceed phone memory for long recordings.

No services were deployed, no firewall/Pi/SSH settings were changed, and test servers were shut down. Follow the hardware and network steps in README before real use.
