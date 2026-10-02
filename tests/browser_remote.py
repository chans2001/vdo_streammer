"""Local HTTPS/cross-site remote-mode integration. No tunnel, deployment, or real Pi.

Requires OpenSSL on PATH. Test-only certificates are never installed as trusted roots.
"""
import argparse
import functools
import http.client
import http.server
import json
import os
from pathlib import Path
import shutil
import ssl
import subprocess
import sys
import tempfile
import threading
import time

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
os.environ.setdefault('PLAYWRIGHT_BROWSERS_PATH', str(ROOT / '.browser-tools'))

from playwright.sync_api import sync_playwright
from waitress import create_server

from mousecam_web.app import create_app
from mousecam_web.config import Config
from tools.prepare_frontend import prepare


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--browser', choices=['edge', 'webkit'], default='webkit')
    args = parser.parse_args()
    openssl = shutil.which('openssl')
    if not openssl:
        raise SystemExit('OpenSSL is required on PATH for this local HTTPS test.')
    with tempfile.TemporaryDirectory(prefix='mousecam-https-') as temporary:
        folder = Path(temporary)
        subprocess.run([openssl, 'req', '-x509', '-newkey', 'rsa:2048', '-nodes', '-days', '1',
                        '-keyout', str(folder / 'test.key'), '-out', str(folder / 'test.pem'),
                        '-subj', '/CN=localhost', '-addext', 'subjectAltName=DNS:localhost,IP:127.0.0.1'],
                       check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        tls = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
        tls.load_cert_chain(folder / 'test.pem', folder / 'test.key')
        config = Config(access_code='https-test-code-only', capture_dir=folder / 'captures', simulated=True, min_free_mb=0)
        app = create_app(config)
        camera = app.extensions['camera']
        waitress = create_server(app, host='127.0.0.1', port=0, threads=24)
        observed = []

        class TLSProxy(http.server.BaseHTTPRequestHandler):
            def log_message(self, *_):
                pass
            def forward(self):
                connection = http.client.HTTPConnection('127.0.0.1', waitress.effective_port, timeout=10)
                try:
                    body = self.rfile.read(int(self.headers.get('Content-Length', 0)))
                    headers = dict(self.headers)
                    headers['Connection'] = 'close'
                    connection.request(self.command, self.path, body=body, headers=headers)
                    response = connection.getresponse()
                    data = response.read()
                    observed.append(dict(method=self.command, path=self.path, status=response.status,
                                         bearer=self.headers.get('Authorization', '').startswith('Bearer '),
                                         cookie=bool(self.headers.get('Cookie')), origin=self.headers.get('Origin'),
                                         allowed=response.getheader('Access-Control-Allow-Origin'),
                                         set_cookie=bool(response.getheader('Set-Cookie'))))
                    self.send_response(response.status)
                    for key, value in response.getheaders():
                        if key.lower() not in ('transfer-encoding', 'connection', 'content-length'):
                            self.send_header(key, value)
                    self.send_header('Content-Length', str(len(data)))
                    self.end_headers()
                    self.wfile.write(data)
                except (BrokenPipeError, ConnectionResetError):
                    pass
                finally:
                    connection.close()
            do_GET = do_POST = do_OPTIONS = forward

        backend = http.server.ThreadingHTTPServer(('127.0.0.1', 0), TLSProxy)
        backend.socket = tls.wrap_socket(backend.socket, server_side=True)
        backend_origin = f'https://127.0.0.1:{backend.server_port}'
        exported = prepare('https://camera.example.org', folder / 'frontend')
        # Test fixture only: production preparation intentionally rejects loopback URLs.
        (exported / 'config.js').write_text('window.MOUSECAM_CONFIG = ' + json.dumps({'API_BASE_URL': backend_origin}) + ';')
        settings = json.loads((exported / 'vercel.json').read_text())
        csp = next(h['value'] for rule in settings['headers'] for h in rule['headers'] if h['key'] == 'Content-Security-Policy').replace('https://camera.example.org', backend_origin)

        class Static(http.server.SimpleHTTPRequestHandler):
            def log_message(self, *_):
                pass
            def end_headers(self):
                self.send_header('Content-Security-Policy', csp)
                self.send_header('Cache-Control', 'no-store')
                super().end_headers()

        frontend = http.server.ThreadingHTTPServer(('127.0.0.1', 0), functools.partial(Static, directory=str(exported)))
        frontend.socket = tls.wrap_socket(frontend.socket, server_side=True)
        # localhost and 127.0.0.1 are different sites, not merely different ports.
        frontend_origin = f'https://localhost:{frontend.server_port}'
        config.allowed_origins = (frontend_origin,)
        threads = [threading.Thread(target=waitress.run, daemon=True), threading.Thread(target=backend.serve_forever, daemon=True), threading.Thread(target=frontend.serve_forever, daemon=True)]
        for thread in threads:
            thread.start()
        camera.start()
        try:
            with sync_playwright() as playwright:
                browser = playwright.webkit.launch() if args.browser == 'webkit' else playwright.chromium.launch(channel='msedge')
                context = browser.new_context(ignore_https_errors=True, viewport={'width': 390, 'height': 844}, is_mobile=True, has_touch=True, accept_downloads=True)
                context.add_init_script("""
                    window.showSaveFilePicker = undefined;
                    Object.defineProperty(Document.prototype, 'cookie', {
                        get() { throw new Error('Cookies unavailable in this test'); },
                        set() { throw new Error('Cookies unavailable in this test'); }
                    });
                """)
                # Even a pre-existing backend cookie must not be sent by credentials: omit.
                context.add_cookies([{'name': 'should-not-be-sent', 'value': 'test-only', 'domain': '127.0.0.1', 'path': '/', 'secure': True, 'sameSite': 'None'}])
                app_errors = []
                context.expose_binding('reportAppError', lambda source, message: app_errors.append(message))
                context.add_init_script("addEventListener('unhandledrejection', e => window.reportAppError(String(e.reason))); addEventListener('error', e => { if (e.error) window.reportAppError(String(e.error)); });")
                def signed_in_page():
                    page = context.new_page()
                    page.goto(frontend_origin)
                    page.locator('#access-code').fill(config.access_code)
                    page.locator('#login-button').click()
                    page.wait_for_function("() => document.querySelector('#camera-status').textContent === 'Live camera' && document.querySelector('#preview').naturalWidth === 640")
                    return page

                page = signed_in_page()
                second = signed_in_page()
                page.locator('#photo').click()
                page.locator('.capture').first.wait_for()
                page.locator('.capture').first.get_by_role('button', name='View', exact=True).click()
                page.wait_for_function("() => document.querySelector('#viewer-content img')?.naturalWidth === 1280")
                page.locator('#close-viewer').click()
                with page.expect_download() as photo:
                    page.locator('.capture').first.get_by_role('button', name='Download').click()
                assert photo.value.suggested_filename.endswith('.jpg')
                page.locator('#start').click()
                second.wait_for_function("() => !document.querySelector('#stop').disabled")
                page.locator('#logout').click()
                assert camera.recording.snapshot()['state'] == 'recording'
                second.wait_for_function("() => document.querySelector('#timer').textContent !== '00:00:00'")
                second.locator('#stop').click()
                second.wait_for_function("() => document.querySelector('#record-status').textContent.includes('Recording saved')")
                with second.expect_download() as video:
                    second.locator('.capture').filter(has_text='VIDEO').first.get_by_role('button', name='Download').click()
                assert video.value.suggested_filename.endswith('.mp4')
                assert second.evaluate('localStorage.length === 0 && sessionStorage.length === 0')
                # Actual cross-site fetches must reject a missing bearer and disallowed Origin.
                assert second.evaluate("async () => (await fetch(window.MOUSECAM_CONFIG.API_BASE_URL + '/api/preview.jpg', {credentials:'omit'})).status") == 401
                denied = context.request.get(backend_origin + '/api/status', headers={'Origin': 'https://not-allowed.example'})
                assert denied.status == 403 and 'access-control-allow-origin' not in denied.headers
                with app.extensions['auth'].lock:
                    for key in app.extensions['auth'].sessions:
                        app.extensions['auth'].sessions[key] = time.monotonic() - 1
                second.locator('#login-panel').wait_for(state='visible')
                assert not app_errors, app_errors
                assert not any(item['cookie'] or item['set_cookie'] for item in observed)
                assert any(item['method'] == 'OPTIONS' and item['status'] == 204 and item['allowed'] == frontend_origin for item in observed)
                assert any(item['path'] == '/api/preview.jpg' and item['status'] == 200 and item['bearer'] for item in observed)
                assert all(item['bearer'] for item in observed if item['status'] == 200 and item['method'] != 'OPTIONS' and item['path'].startswith('/api/') and item['path'] != '/api/login')
                context.close()
                browser.close()
                print(f'PASS ({args.browser}): cross-site HTTPS; cookie APIs unavailable; existing cookies never sent; real CORS preflight; authenticated JPEG, photo/view/download, shared record/stop, gallery/video download; logout and expiry; denied origin and unauthenticated preview; no persistent credentials. Local test certificates only, no public deployment.')
        finally:
            camera.close()
            for server in (frontend, backend):
                server.shutdown()
                server.server_close()
            waitress.close()
            waitress.task_dispatcher.shutdown()
            for thread in threads:
                thread.join(timeout=5)


if __name__ == '__main__':
    main()
