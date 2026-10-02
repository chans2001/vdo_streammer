import io
import json
import shutil
import subprocess
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from types import SimpleNamespace

import pytest
from PIL import Image

from mousecam_web.app import create_app
from mousecam_web.config import Config
from mousecam_web.media import FPS, FRAME_BYTES, find_ffmpeg, read_frame
from mousecam_web.service import CameraService, Recording, ServiceError, new_name, save_json

CODE = 'test-only-access-code'


def until(predicate, timeout=12):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        result = predicate()
        if result:
            return result
        time.sleep(0.05)
    raise AssertionError('Timed out waiting for condition')


@pytest.fixture
def config(tmp_path):
    return Config(access_code=CODE, ffmpeg=find_ffmpeg(), capture_dir=tmp_path, simulated=True, min_free_mb=0)


@pytest.fixture
def app(config):
    instance = create_app(config)
    instance.testing = True
    yield instance
    instance.extensions['camera'].close()


def login(client, origin=None):
    response = client.post('/api/login', json={'code': CODE}, headers={'Origin': origin} if origin else {})
    assert response.status_code == 200, response.json
    assert 'Set-Cookie' not in response.headers
    return {'Authorization': 'Bearer ' + response.json['token']}


@pytest.fixture
def live(app):
    service = app.extensions['camera']
    service.start()
    until(lambda: service.preview is not None)
    return service


@pytest.mark.parametrize('path', ['/api/status', '/api/captures', '/api/preview.jpg', '/api/preview.mjpeg', '/api/media/example.mp4', '/api/media/example.jpg?download=1'])
def test_unauthenticated_media_and_api(app, path):
    assert app.test_client().get(path).status_code == 401


@pytest.mark.parametrize('path', ['/api/photos', '/api/recording/start', '/api/recording/stop', '/api/logout'])
def test_unauthenticated_controls(app, path):
    assert app.test_client().post(path, json={}).status_code == 401


def test_login_rate_limit_and_revocation(app):
    client = app.test_client()
    headers = login(client)
    assert client.get('/api/status', headers=headers).status_code == 200
    assert client.post('/api/logout', headers=headers, json={}).status_code == 200
    assert client.get('/api/status', headers=headers).status_code == 401
    for _ in range(9):
        assert client.post('/api/login', json={'code': 'bad'}).status_code == 401
    assert client.post('/api/login', json={'code': CODE}).status_code == 429


def test_expiry_and_no_cookie_auth(app):
    client = app.test_client()
    headers = login(client)
    auth = app.extensions['auth']
    for key in auth.sessions:
        auth.sessions[key] = time.monotonic() - 1
    assert client.get('/api/status', headers=headers).status_code == 401
    client.set_cookie('session', 'pretend-session')
    assert client.post('/api/recording/start', json={}).status_code == 401
    assert client.post('/api/login', data={'code': CODE}).status_code == 415


def test_origins(config):
    config.allowed_origins = ('https://mousecam.example',)
    app = create_app(config)
    client = app.test_client()
    headers = login(client, 'https://mousecam.example')
    allowed = client.get('/api/status', headers={**headers, 'Origin': 'https://mousecam.example'})
    assert allowed.headers['Access-Control-Allow-Origin'] == 'https://mousecam.example'
    assert 'Access-Control-Allow-Credentials' not in allowed.headers
    for origin in ('https://evil.example', 'null', 'https://mousecam.example.evil', 'https://mousecam.example:444'):
        response = client.get('/api/status', headers={**headers, 'Origin': origin})
        assert response.status_code == 403
        assert 'Access-Control-Allow-Origin' not in response.headers
    response = client.options('/api/recording/start', headers={'Origin': 'https://mousecam.example', 'Access-Control-Request-Method': 'POST', 'Access-Control-Request-Headers': 'authorization,content-type'})
    assert response.status_code == 204
    assert client.options('/api/status', headers={'Origin': 'https://evil.example', 'Access-Control-Request-Method': 'GET'}).status_code == 403


def test_cross_origin_off_by_default(app):
    response = app.test_client().post('/api/login', json={'code': CODE}, headers={'Origin': 'https://mousecam.example'})
    assert response.status_code == 403
    assert 'Access-Control-Allow-Origin' not in response.headers


@pytest.mark.parametrize('name', ['../.env', '..%2F.env', '/etc/passwd', 'C:\\Windows\\win.ini', 'mousecam_fake.mp4', '.server.lock'])
def test_paths(app, name):
    client = app.test_client()
    headers = login(client)
    assert client.get('/api/media/' + name, headers=headers).status_code in (404, 308)
    with pytest.raises(ServiceError):
        app.extensions['camera'].media_path(name)


def test_partial_and_failed_media_hidden(app, config):
    name = new_name('.mp4')
    path = config.capture_dir / name
    path.write_bytes(b'incomplete')
    data = dict(name=name, type='video', started_at='2026-01-01T00:00:00+00:00', state='recording')
    save_json(path.with_suffix('.json'), data)
    service = app.extensions['camera']
    assert service.gallery() == []
    with pytest.raises(ServiceError):
        service.media_path(name)
    data['state'] = 'failed'
    save_json(path.with_suffix('.json'), data)
    assert service.gallery()[0]['available'] is False
    with pytest.raises(ServiceError):
        service.media_path(name)


def test_simulated_preview_photo_recording_download(app, live, config):
    client = app.test_client()
    headers = login(client)
    upstream = live.proc.pid
    for _ in range(5):
        response = client.get('/api/preview.jpg', headers=headers)
        assert response.status_code == 200
        assert Image.open(io.BytesIO(response.data)).size == (640, 360)
    assert live.proc.pid == upstream
    stream = client.get('/api/preview.mjpeg', headers=headers, buffered=False)
    assert next(iter(stream.response)).startswith(b'--frame\r\nContent-Type: image/jpeg')
    stream.close()
    response = client.post('/api/photos', headers=headers, json={})
    assert response.status_code == 201
    photo = response.json
    media = client.get('/api/media/' + photo['name'], headers=headers)
    assert Image.open(io.BytesIO(media.data)).size == (1280, 720)
    def start(_):
        return app.test_client().post('/api/recording/start', headers=headers, json={}).json
    with ThreadPoolExecutor(max_workers=10) as pool:
        starts = list(pool.map(start, range(10)))
    assert len({item['name'] for item in starts}) == 1
    until(lambda: live.recording.snapshot()['frame_count'] >= 20)
    with ThreadPoolExecutor(max_workers=10) as pool:
        list(pool.map(lambda _: app.test_client().post('/api/recording/stop', headers=headers, json={}), range(10)))
    assert live.recording.done.wait(15)
    record = live.recording.snapshot()
    assert record['state'] == 'completed', record
    assert record['known_dropped_frames'] == 0
    assert record['ended_at'] and record['frame_count'] >= 20
    assert record['encoded_seconds'] == record['frame_count'] / FPS
    download = client.get('/api/media/' + record['name'] + '?download=1', headers=headers)
    assert download.status_code == 200
    assert 'attachment' in download.headers['Content-Disposition']
    assert download.data[4:8] == b'ftyp'
    ranged = client.get('/api/media/' + record['name'], headers={**headers, 'Range': 'bytes=0-99'})
    assert ranged.status_code == 206 and len(ranged.data) == 100
    probe = Path(config.ffmpeg).with_name('ffprobe.exe' if Path(config.ffmpeg).suffix == '.exe' else 'ffprobe')
    assert probe.is_file(), 'ffprobe required for video verification'
    metadata = json.loads(subprocess.check_output([str(probe), '-v', 'error', '-select_streams', 'v:0', '-count_frames', '-show_streams', '-show_format', '-of', 'json', str(live.recording.path)]))
    video = metadata['streams'][0]
    assert (video['codec_name'], video['width'], video['height'], video['r_frame_rate']) == ('h264', 1280, 720, '15/1')
    assert int(video['nb_read_frames']) == record['frame_count']
    assert float(metadata['format']['duration']) == pytest.approx(record['encoded_seconds'], abs=.08)
    assert metadata['format']['nb_streams'] == 1
    # Completed captures survive a new service instance.
    assert len(CameraService(config).gallery()) == 2


def test_stream_loss_finalizes(live):
    live.start_recording()
    until(lambda: live.recording.snapshot()['frame_count'] >= 5)
    live.proc.terminate()
    assert live.recording.done.wait(15)
    record = live.recording.snapshot()
    assert record['state'] == 'interrupted'
    assert record['interrupted'] and record['error']
    assert live.media_path(record['name']).is_file()


def test_stale_rejects_capture_and_finalizes(app, live):
    live.start_recording()
    until(lambda: live.recording.snapshot()['frame_count'] >= 3)
    # Stop publishing to mimic a connected source that stops delivering frames.
    live._publish = lambda frame: None
    with live.lock:
        live.frame_at = time.monotonic() - live.config.stale_seconds - 1
    assert live.status()['camera']['state'] == 'stale'
    with pytest.raises(ServiceError):
        live.take_photo()
    with pytest.raises(ServiceError):
        live.jpeg()
    assert live.recording.done.wait(15)
    assert live.recording.snapshot()['interrupted']


def test_shutdown_finalizes_and_logout_does_not_stop(app, live):
    client = app.test_client()
    headers = login(client)
    live.start_recording()
    until(lambda: live.recording.snapshot()['frame_count'] >= 3)
    client.post('/api/logout', headers=headers, json={})
    assert live.recording.snapshot()['state'] == 'recording'
    live.close()
    assert live.recording.done.is_set()
    assert live.recording.snapshot()['state'] == 'interrupted'


def test_low_space_rejected(live, monkeypatch):
    live.config.min_free_mb = 512
    monkeypatch.setattr(shutil, 'disk_usage', lambda _: SimpleNamespace(free=1))
    with pytest.raises(ServiceError, match='Insufficient'):
        live.start_recording()
    with pytest.raises(ServiceError, match='Insufficient'):
        live.take_photo()


def test_bounded_queue_overload(config, monkeypatch):
    # Pause the writer deliberately, ensuring acquisition remains nonblocking.
    original = Recording._run
    import threading
    release = threading.Event()
    def paused(self):
        release.wait(5)
        original(self)
    monkeypatch.setattr(Recording, '_run', paused)
    config.queue_frames = 2
    recording = Recording(config)
    frame = bytes(FRAME_BYTES)
    try:
        for _ in range(3):
            recording.offer(frame)
        assert recording.queue.qsize() == 2
        assert recording.snapshot()['state'] == 'saving'
        assert recording.snapshot()['known_dropped_frames'] == 1
    finally:
        release.set()
    assert recording.done.wait(15)
    assert recording.snapshot()['state'] == 'interrupted'


def test_crash_recovery(config):
    name = new_name('.mp4')
    config.capture_dir.mkdir(exist_ok=True)
    path = config.capture_dir / name
    save_json(path.with_suffix('.json'), dict(name=name, type='video', state='recording', started_at='2026-01-01', frame_count=0))
    service = CameraService(config)
    data = service.gallery()[0]
    assert data['state'] == 'failed' and data['interrupted']
    assert data['frame_count'] is None and data['ended_at'] is None and not data['available']


def test_partial_frame_reads():
    class Partial:
        def __init__(self):
            self.remaining = FRAME_BYTES
        def read(self, n):
            count = min(n, 8192, self.remaining)
            self.remaining -= count
            return b'x' * count
    assert len(read_frame(Partial())) == FRAME_BYTES
    assert read_frame(io.BytesIO(b'broken')) is None


def test_reconnect_budget(config, monkeypatch):
    config.reconnect_attempts = 2
    config.simulated = False
    # Exercise process exit without touching a real Pi/network.
    monkeypatch.setattr('mousecam_web.service.acquisition_command', lambda _: [config.ffmpeg, '-version'])
    service = CameraService(config)
    try:
        service.start()
        until(lambda: service.status()['camera']['state'] == 'exhausted')
        assert service.status()['camera']['attempt'] == 2
        assert service.proc is None
    finally:
        service.close()


def test_encoder_failure_is_not_completed(live):
    live.start_recording()
    until(lambda: live.recording.snapshot()['frame_count'] >= 3)
    live.recording.proc.kill()
    assert live.recording.done.wait(15)
    record = live.recording.snapshot()
    assert record['state'] == 'failed' and record['interrupted']
    with pytest.raises(ServiceError):
        live.media_path(record['name'])


def test_active_recording_low_space_finalizes(live, monkeypatch):
    live.start_recording()
    until(lambda: live.recording.snapshot()['frame_count'] >= 3)
    live.config.min_free_mb = 512
    monkeypatch.setattr(shutil, 'disk_usage', lambda _: SimpleNamespace(free=1))
    assert live.recording.done.wait(15)
    assert live.recording.snapshot()['state'] == 'interrupted'
    assert 'Insufficient' in live.recording.snapshot()['error']


def test_stream_revoked_after_logout(app, live):
    client = app.test_client()
    headers = login(client)
    stream = client.get('/api/preview.mjpeg', headers=headers, buffered=False)
    iterator = iter(stream.response)
    next(iterator)
    client.post('/api/logout', headers=headers, json={})
    with pytest.raises(StopIteration):
        next(iterator)
    stream.close()


def test_raw_h264_tcp_pipeline(config):
    """Local synthetic H.264 listener exercises the actual Pi decoder command."""
    import socket
    from mousecam_web.media import CREATE_FLAGS, terminate
    with socket.socket() as listener:
        listener.bind(('127.0.0.1', 0))
        port = listener.getsockname()[1]
    config.simulated = False
    config.camera_url = f'tcp://127.0.0.1:{port}'
    source = subprocess.Popen([config.ffmpeg, '-hide_banner', '-loglevel', 'error', '-re',
                               '-f', 'lavfi', '-i', 'testsrc2=size=1280x720:rate=15',
                               '-an', '-c:v', 'libx264', '-preset', 'ultrafast', '-tune', 'zerolatency',
                               '-g', '15', '-f', 'h264', config.camera_url + '?listen=1'],
                              stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                              creationflags=CREATE_FLAGS)
    service = CameraService(config)
    try:
        service.start()
        until(lambda: service.preview is not None, timeout=20)
        assert service.status()['camera']['fresh']
        assert Image.open(io.BytesIO(service.jpeg()[0])).size == (640, 360)
        service.start_recording()
        until(lambda: service.recording.snapshot()['frame_count'] >= 5)
        terminate(source)
        assert service.recording.done.wait(15)
        assert service.recording.snapshot()['state'] == 'interrupted'
    finally:
        service.close()
        terminate(source)
