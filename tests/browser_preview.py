"""Read-only preview regression against a running backend, including a real LAN URL.

The access code is read privately from .env. No captures or recording state are changed.
Playwright WebKit on Windows is not an actual iPhone/Safari installation.
"""
import argparse
import os
from pathlib import Path
import sys
from urllib.parse import urlsplit

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
os.environ.setdefault('PLAYWRIGHT_BROWSERS_PATH', str(ROOT / '.browser-tools'))

from dotenv import dotenv_values
from playwright.sync_api import sync_playwright


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--url', required=True, help='Running server origin, e.g. http://172.17.157.129:8080')
    parser.add_argument('--browser', choices=['webkit', 'edge'], default='webkit')
    args = parser.parse_args()
    origin = args.url.rstrip('/')
    code = os.environ.get('MOUSECAM_ACCESS_CODE') or dotenv_values(ROOT / '.env')['MOUSECAM_ACCESS_CODE']
    with sync_playwright() as playwright:
        browser = playwright.webkit.launch() if args.browser == 'webkit' else playwright.chromium.launch(channel='msedge')
        context = browser.new_context(viewport={'width': 390, 'height': 844}, is_mobile=True, has_touch=True)
        # JPEG preview no longer needs this relatively new AbortSignal API.
        context.add_init_script('AbortSignal.any = undefined;')
        page = context.new_page()
        errors, requests, responses = [], [], []
        page.on('pageerror', lambda error: errors.append(str(error)))
        page.on('request', lambda req: requests.append(req.url) if '/api/' in req.url else None)
        page.on('response', lambda response: responses.append(response) if response.url.endswith('/api/preview.jpg') else None)
        page.goto(origin)
        page.locator('#access-code').fill(code)
        page.locator('#login-button').click()
        try:
            page.wait_for_function("() => document.querySelector('#preview').naturalWidth === 640 && document.querySelector('#offline').hidden && document.querySelector('#camera-status').textContent === 'Live camera'")
            assert context.cookies() == []
            first = responses[-1]
            assert first.status == 200 and first.headers['content-type'] == 'image/jpeg'
            assert first.request.header_value('authorization').startswith('Bearer ')
            assert 'set-cookie' not in first.headers
            status = page.evaluate("async () => (await (await api('/api/status')).json()).camera")
            print(f'{args.browser}: {origin}, fresh={status["fresh"]}, simulated={status["simulated"]}, JPEG=200 image/jpeg, decoded=640x360, bearer=True, cookies=False')

            def failed_preview(detail):
                page.wait_for_function("() => document.querySelector('#camera-status').textContent === 'Preview unavailable'")
                page.wait_for_timeout(2200)  # Two successful status polls must not erase failure.
                assert page.locator('#offline').is_visible()
                assert detail in page.locator('#offline-detail').inner_text()
                assert 'live' not in page.locator('#status-dot').get_attribute('class').split()
                assert page.locator('#photo').is_disabled() and page.locator('#start').is_disabled()

            def recover():
                page.unroute('**/api/preview.jpg')
                page.wait_for_function("() => document.querySelector('#camera-status').textContent === 'Live camera' && document.querySelector('#offline').hidden")

            page.route('**/api/preview.jpg', lambda route: route.abort('failed'))
            failed_preview('')
            recover()
            page.route('**/api/preview.jpg', lambda route: route.fulfill(status=200, content_type='text/html', body='<html>proxy error</html>'))
            failed_preview('did not return a JPEG')
            recover()
            page.route('**/api/preview.jpg', lambda route: route.fulfill(status=200, content_type='image/jpeg', body=b'not an image'))
            failed_preview('Could not decode')
            recover()
            assert all(urlsplit(url).netloc == urlsplit(origin).netloc for url in requests)
            assert not any('preview.mjpeg' in url for url in requests)
            assert not errors, errors
            print('PASS: relative same-origin API URLs; repeated JPEG rendering; failed request, wrong MIME, corrupt image remain visibly unhealthy despite live camera status; automatic recovery; no multipart fetch; no JS errors.')
        finally:
            if page.locator('#logout').is_visible():
                page.locator('#logout').click()
                page.locator('#login-panel').wait_for(state='visible')
            context.close()
            browser.close()


if __name__ == '__main__':
    main()
