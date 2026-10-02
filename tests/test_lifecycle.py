import json
import os
from pathlib import Path
import signal
import socket
import subprocess
import sys
import time
import urllib.request

from mousecam_web.config import ROOT


def test_actual_server_shutdown_and_single_owner(tmp_path):
    with socket.socket() as listener:
        listener.bind(('127.0.0.1', 0))
        port = listener.getsockname()[1]
    code = 'process-test-access-code'
    env = {**os.environ, 'MOUSECAM_ACCESS_CODE': code, 'MOUSECAM_SIMULATED': 'true',
           'MOUSECAM_HOST': '127.0.0.1', 'MOUSECAM_PORT': str(port), 'MOUSECAM_CAPTURE_DIR': str(tmp_path),
           'MOUSECAM_MIN_FREE_MB': '0'}
    proc = subprocess.Popen([sys.executable, '-m', 'mousecam_web'], cwd=ROOT, env=env,
                            stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                            creationflags=getattr(subprocess, 'CREATE_NEW_PROCESS_GROUP', 0))
    token = ''
    def request(path, body=None):
        data = json.dumps(body).encode() if body is not None else None
        req = urllib.request.Request(f'http://127.0.0.1:{port}/api/{path}', data=data,
                                     headers={'Content-Type': 'application/json', 'Authorization': 'Bearer ' + token})
        with urllib.request.urlopen(req, timeout=2) as response:
            return json.load(response)
    try:
        deadline = time.monotonic() + 15
        while time.monotonic() < deadline:
            try:
                token = request('login', {'code': code})['token']
                break
            except OSError:
                time.sleep(.1)
        assert token, 'Server failed to start'
        while time.monotonic() < deadline and not request('status')['camera']['fresh']:
            time.sleep(.1)
        record = request('recording/start', {})
        while time.monotonic() < deadline and request('status')['recording']['frame_count'] < 5:
            time.sleep(.1)
        duplicate = subprocess.run([sys.executable, '-m', 'mousecam_web'], cwd=ROOT, env=env, capture_output=True, timeout=10)
        assert duplicate.returncode != 0
        assert b'Another MouseCam server owns' in duplicate.stderr
        proc.send_signal(signal.CTRL_BREAK_EVENT if os.name == 'nt' else signal.SIGTERM)
        output, _ = proc.communicate(timeout=30)
        assert proc.returncode == 0, output.decode(errors='replace')
        sidecar = json.loads((tmp_path / record['name']).with_suffix('.json').read_text())
        assert sidecar['state'] == 'interrupted'
        assert sidecar['frame_count'] >= 5
        assert (tmp_path / record['name']).is_file()
    finally:
        if proc.poll() is None:
            proc.kill()
            proc.communicate(timeout=5)
