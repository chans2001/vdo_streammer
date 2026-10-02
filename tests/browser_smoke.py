r"""Real local Waitress + Edge smoke test; no Pi or external services.

Run: .\.venv\Scripts\python.exe tests\browser_smoke.py
Screenshots are written to ignored test-results/.
"""
import functools
import argparse
import http.server
import json
import os
from pathlib import Path
import sys
import tempfile
import threading
import time

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from playwright.sync_api import sync_playwright
from waitress import create_server

from mousecam_web.app import create_app
from mousecam_web.config import Config, ROOT
from mousecam_web.media import find_ffmpeg


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--browser', choices=['edge', 'webkit'], default='edge')
    parser.add_argument('--skip-video-playback', action='store_true', help='Run preview/capture/download checks without testing MP4 decoding')
    args = parser.parse_args()
    os.environ.setdefault('PLAYWRIGHT_BROWSERS_PATH', str(ROOT / '.browser-tools'))
    output = ROOT / 'test-results'
    output.mkdir(exist_ok=True)
    with tempfile.TemporaryDirectory(prefix='browser-', dir=output) as captures:
        config = Config(access_code='browser-test-access-code', ffmpeg=find_ffmpeg(), capture_dir=Path(captures), simulated=True, min_free_mb=0)
        app = create_app(config)
        camera = app.extensions['camera']
        server = create_server(app, host='127.0.0.1', port=0, threads=24, outbuf_high_watermark=262144, outbuf_overflow=262144)
        origin = f'http://127.0.0.1:{server.effective_port}'
        class Static(http.server.SimpleHTTPRequestHandler):
            def log_message(self, *_):
                pass
            def do_GET(self):
                if self.path == '/config.js':
                    data = ('window.MOUSECAM_CONFIG = ' + json.dumps({'API_BASE_URL': origin}) + ';').encode()
                    self.send_response(200)
                    self.send_header('Content-Type', 'application/javascript')
                    self.send_header('Content-Length', str(len(data)))
                    self.end_headers()
                    self.wfile.write(data)
                else:
                    super().do_GET()
        static = http.server.ThreadingHTTPServer(('127.0.0.1', 0), functools.partial(Static, directory=str(ROOT / 'frontend')))
        static_origin = f'http://127.0.0.1:{static.server_port}'
        config.allowed_origins = (static_origin,)
        server_thread = threading.Thread(target=server.run, daemon=True)
        static_thread = threading.Thread(target=static.serve_forever, daemon=True)
        server_thread.start()
        static_thread.start()
        camera.start()
        try:
            with sync_playwright() as playwright:
                browser = playwright.webkit.launch(headless=True) if args.browser == 'webkit' else playwright.chromium.launch(channel='msedge', headless=True)
                errors = []
                app_errors = []
                contexts = []
                def page_for(url, viewport):
                    context = browser.new_context(viewport=viewport, accept_downloads=True)
                    contexts.append(context)
                    context.add_init_script('window.showSaveFilePicker = undefined;')
                    context.expose_binding('reportAppError', lambda source, message: app_errors.append(message))
                    context.add_init_script("""
                        addEventListener('error', event => {
                            if (event.error) window.reportAppError(String(event.error));
                        });
                        addEventListener('unhandledrejection', event => window.reportAppError(String(event.reason)));
                    """)
                    page = context.new_page()
                    page.on('pageerror', lambda error: errors.append({'page': page.url, 'message': str(error), 'stack': error.stack}))
                    page.goto(url)
                    page.locator('#access-code').fill(config.access_code)
                    page.locator('#login-button').click()
                    page.locator('#workspace').wait_for(state='visible')
                    page.wait_for_function("() => document.querySelector('#preview').naturalWidth === 640 && document.querySelector('#offline').hidden")
                    assert page.evaluate('document.documentElement.scrollWidth <= innerWidth')
                    assert context.cookies() == []
                    return page

                laptop = page_for(origin, {'width': 1440, 'height': 1000})
                upstream = camera.proc.pid
                viewers = [page_for(origin, {'width': 1024, 'height': 768}) for _ in range(4)]
                assert camera.proc.pid == upstream
                laptop.locator('#photo').click()
                laptop.get_by_role('status').filter(has_text='Photo saved:').wait_for()
                laptop.locator('.capture').first.wait_for()
                laptop.locator('.capture').first.get_by_role('button', name='View', exact=True).click()
                laptop.wait_for_function("() => document.querySelector('#viewer-content img')?.naturalWidth === 1280")
                laptop.locator('#close-viewer').click()
                with laptop.expect_download() as download:
                    laptop.locator('.capture').first.get_by_role('button', name='Download').click()
                assert download.value.suggested_filename.endswith('.jpg')
                laptop.locator('#start').click()
                viewers[0].wait_for_function("() => !document.querySelector('#stop').disabled")
                viewers[0].reload()
                viewers[0].locator('#login-panel').wait_for(state='visible')
                viewers[1].locator('#logout').click()
                assert camera.recording.snapshot()['state'] == 'recording', camera.recording.snapshot()
                viewers[2].wait_for_function("() => document.querySelector('#timer').textContent !== '00:00:00'")
                laptop.screenshot(path=str(output / 'laptop.png'), full_page=True)
                assert camera.recording.snapshot()['state'] == 'recording', camera.recording.snapshot()
                viewers[2].locator('#stop').click()
                laptop.wait_for_function("() => document.querySelector('#record-status').textContent.includes('Recording saved')")
                if not args.skip_video_playback:
                    laptop.locator('.capture').filter(has_text='VIDEO').get_by_role('button', name='View', exact=True).click()
                    # WebKit can defer media loading until playback is requested.
                    laptop.locator('#viewer-content video').evaluate('(video) => { video.muted = true; video.play().catch(() => {}); }')
                    laptop.wait_for_function("() => document.querySelector('#viewer-content video')?.readyState >= 2")
                    laptop.locator('#close-viewer').click()
                # Different origins exercise preflight + Authorization with no third-party cookies.
                phone = page_for(static_origin, {'width': 390, 'height': 844})
                phone.locator('#photo').click()
                phone.get_by_role('status').filter(has_text='Photo saved:').wait_for()
                phone.screenshot(path=str(output / 'phone.png'), full_page=True)
                assert camera.proc.pid == upstream
                phone.locator('#start').click()
                laptop.wait_for_function("() => !document.querySelector('#stop').disabled")
                phone.locator('#stop').click()
                laptop.wait_for_function("() => document.querySelector('#record-status').textContent.includes('Recording saved')")
                with phone.expect_download() as download:
                    phone.locator('.capture').filter(has_text='VIDEO').first.get_by_role('button', name='Download').click()
                assert download.value.suggested_filename.endswith('.mp4')
                camera._publish = lambda _: None
                with camera.lock:
                    camera.frame_at = time.monotonic() - config.stale_seconds - 1
                laptop.locator('#offline').wait_for(state='visible')
                laptop.wait_for_function("() => document.querySelector('#photo').disabled && document.querySelector('#start').disabled")
                assert app_errors == [], app_errors
                # Windows WebKit also emits pageerror for explicitly aborted fetches
                # at logout, even when JS catches them; these are not window errors.
                network_diagnostics = [error for error in errors if args.browser == 'webkit' and error['stack'].startswith('Fetch API cannot load ') and 'due to access control checks.' in error['stack']]
                assert len(network_diagnostics) == len(errors), errors
                if network_diagnostics:
                    print(f'WebKit network diagnostics during cancellation: {len(network_diagnostics)}; no window error or unhandled rejection.')
                for context in contexts:
                    context.close()
                browser.close()
                playback = 'MP4 playback SKIPPED' if args.skip_video_playback else 'MP4 playback verified'
                print(f'PASS ({args.browser}): real Waitress; five authenticated JPEG viewers share one source; photo/view/download; shared recording/stop; refresh/logout independence; {playback}; 1440px laptop and 390px phone layout; separate-origin JPEG/auth/record/download; stale overlay; no JavaScript errors.')
        finally:
            camera.close()
            static.shutdown()
            static.server_close()
            server.close()
            server.task_dispatcher.shutdown()
            server_thread.join(timeout=5)
            static_thread.join(timeout=5)


if __name__ == '__main__':
    main()
