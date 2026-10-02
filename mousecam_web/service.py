import io
import json
import logging
import queue
import re
import shutil
import subprocess
import threading
import time
import uuid
from datetime import datetime, timezone

from PIL import Image

from .media import CREATE_FLAGS, FPS, HEIGHT, WIDTH, acquisition_command, read_frame, recording_command, terminate

log = logging.getLogger(__name__)
NAME = re.compile(r'^mousecam_[0-9]{8}T[0-9]{12}Z_[a-f0-9]{32}\.(?:mp4|jpg)$')


def utc_now():
    return datetime.now(timezone.utc).isoformat()


def new_name(suffix):
    return 'mousecam_' + datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S%fZ_') + uuid.uuid4().hex + suffix


def save_json(path, data):
    temporary = path.with_suffix('.json.tmp')
    with temporary.open('w', encoding='utf-8') as out:
        json.dump(data, out, indent=2)
    temporary.replace(path)


class ServiceError(Exception):
    def __init__(self, message, status=409):
        super().__init__(message)
        self.status = status


class Recording:
    """A bounded FIFO and one encoder. Acquisition never waits for this writer."""
    def __init__(self, config):
        self.config = config
        self.name = new_name('.mp4')
        self.path = config.capture_dir / self.name
        self.partial = self.path.with_suffix('.partial.mp4')
        self.sidecar = self.path.with_suffix('.json')
        self.lock = threading.RLock()
        self.queue = queue.Queue(maxsize=config.queue_frames)
        self.stop_event = threading.Event()
        self.done = threading.Event()
        self.started = time.monotonic()
        self.progress = self.started
        self.accepted = 0
        self.meta = dict(name=self.name, type='video', state='recording', started_at=utc_now(), ended_at=None,
                         intended_fps=FPS, frame_count=0, known_dropped_frames=0, upstream_dropped_frames=None,
                         interrupted=False, error=None, wall_seconds=0, encoded_seconds=0,
                         width=WIDTH, height=HEIGHT, simulated=config.simulated)
        save_json(self.sidecar, self.meta)
        try:
            self.proc = subprocess.Popen(recording_command(config.ffmpeg, self.partial), stdin=subprocess.PIPE,
                                         stdout=subprocess.DEVNULL, stderr=subprocess.PIPE, creationflags=CREATE_FLAGS)
        except Exception:
            self.meta.update(state='failed', interrupted=True, error='Could not launch encoder.', ended_at=utc_now())
            save_json(self.sidecar, self.meta)
            raise
        self.errors = bytearray()
        self.error_reader = threading.Thread(target=self._read_errors, daemon=True)
        self.error_reader.start()
        self.thread = threading.Thread(target=self._run, name='recording-writer', daemon=True)
        self.thread.start()

    def _read_errors(self):
        for chunk in iter(lambda: self.proc.stderr.read(1024), b''):
            self.errors.extend(chunk)
            del self.errors[:-4096]

    def offer(self, frame):
        with self.lock:
            if self.stop_event.is_set():
                return
            try:
                self.queue.put_nowait(frame)
                self.accepted += 1
            except queue.Full:
                self.meta['known_dropped_frames'] += 1
                self.stop('Recording overloaded: frame queue full. Recording stopped.')

    def stop(self, reason=None):
        with self.lock:
            if self.done.is_set():
                return
            if reason:
                self.meta.update(interrupted=True, error=self.meta['error'] or reason)
            if not self.stop_event.is_set():
                self.meta.update(state='saving', ended_at=utc_now(), wall_seconds=time.monotonic() - self.started)
                self.stop_event.set()

    def snapshot(self):
        with self.lock:
            data = dict(self.meta)
            if data['state'] == 'recording':
                data['wall_seconds'] = time.monotonic() - self.started
            data['encoded_seconds'] = data['frame_count'] / FPS
            return data

    def _run(self):
        failure = None
        try:
            while not self.stop_event.is_set() or not self.queue.empty():
                try:
                    frame = self.queue.get(timeout=0.1)
                except queue.Empty:
                    continue
                self.proc.stdin.write(frame)
                with self.lock:
                    self.meta['frame_count'] += 1
                    self.progress = time.monotonic()
            self.proc.stdin.close()
            code = self.proc.wait(timeout=15)
            if code or not self.meta['frame_count']:
                raise RuntimeError('Encoder failed or no frames were recorded.')
            self.partial.rename(self.path)
        except Exception as exc:
            failure = str(exc)
            terminate(self.proc)
        finally:
            self.stop(failure)
            self.error_reader.join(timeout=2)
            if self.proc.stdin and not self.proc.stdin.closed:
                try:
                    self.proc.stdin.close()
                except OSError:
                    pass
            self.proc.stderr.close()
            with self.lock:
                self.meta['known_dropped_frames'] += self.accepted - self.meta['frame_count']
                self.meta['encoded_seconds'] = self.meta['frame_count'] / FPS
                self.meta['state'] = 'failed' if failure else ('interrupted' if self.meta['interrupted'] else 'completed')
                self.meta['size_bytes'] = self.path.stat().st_size if self.path.exists() else 0
                if failure and self.errors:
                    log.error('Encoder: %s', self.errors.decode(errors='replace'))
                try:
                    save_json(self.sidecar, self.meta)
                except OSError:
                    self.meta.update(state='failed', interrupted=True, error='Could not save recording metadata.')
                    log.exception('Could not save recording metadata')
                self.done.set()

    def watchdog(self):
        with self.lock:
            stalled = not self.done.is_set() and time.monotonic() - self.progress > 8
        if stalled:
            self.stop('Encoder stalled; recording interrupted.')
            terminate(self.proc)


class CameraService:
    def __init__(self, config):
        self.config = config
        config.capture_dir.mkdir(parents=True, exist_ok=True)
        self.lock = threading.RLock()
        self.changed = threading.Condition(self.lock)
        self.stop_event = threading.Event()
        self.frame = None
        self.frame_at = 0
        self.frame_utc = None
        self.sequence = 0
        self.preview = None
        self.preview_at = 0
        self.preview_sequence = 0
        self.connection = 'starting'
        self.connection_error = None
        self.attempt = 0
        self.proc = None
        self.proc_started = 0
        self.recording = None
        self.threads = []
        self._recover()

    def _recover(self):
        for path in self.config.capture_dir.glob('mousecam_*.json'):
            if path.is_symlink():
                continue
            try:
                data = json.loads(path.read_text(encoding='utf-8'))
                if data.get('state') in ('recording', 'saving'):
                    data.update(state='failed', interrupted=True, ended_at=None,
                                frame_count=None, known_dropped_frames=None, encoded_seconds=None,
                                error='Server exited before finalization; frame count and end time unknown.')
                    save_json(path, data)
            except (OSError, ValueError):
                log.exception('Could not recover %s', path.name)

    def start(self):
        with self.lock:
            if self.threads:
                return
            for target in (self._acquire, self._preview, self._monitor):
                thread = threading.Thread(target=target, daemon=True, name=target.__name__)
                self.threads.append(thread)
                thread.start()

    def _fresh(self):
        return self.frame is not None and self.connection == 'live' and time.monotonic() - self.frame_at < self.config.stale_seconds

    def _publish(self, frame):
        with self.changed:
            self.frame, self.frame_at, self.frame_utc = frame, time.monotonic(), utc_now()
            self.sequence += 1
            self.connection = 'live'
            self.connection_error = None
            if self.recording:
                self.recording.offer(frame)
            self.changed.notify_all()

    def _lost(self, reason):
        with self.changed:
            self.connection = 'disconnected'
            self.connection_error = reason
            self.preview = None
            if self.recording:
                self.recording.stop(reason)
            self.changed.notify_all()

    def _acquire(self):
        failures = 0
        while not self.stop_event.is_set() and failures < self.config.reconnect_attempts:
            with self.lock:
                self.connection = 'connecting'
                self.attempt = failures + 1
                initial_sequence = self.sequence
            began = time.monotonic()
            proc = None
            try:
                # stderr is discarded to prevent either pipe deadlock or unbounded log growth.
                proc = subprocess.Popen(acquisition_command(self.config), stdout=subprocess.PIPE,
                                        stdin=subprocess.DEVNULL, stderr=subprocess.DEVNULL, creationflags=CREATE_FLAGS)
                with self.lock:
                    self.proc, self.proc_started = proc, time.monotonic()
                while not self.stop_event.is_set():
                    frame = read_frame(proc.stdout)
                    if frame is None:
                        break
                    self._publish(frame)
            except Exception:
                log.exception('Camera acquisition failed')
            finally:
                terminate(proc)
                if proc and proc.stdout:
                    proc.stdout.close()
                with self.lock:
                    self.proc = None
                self._lost('Stream ended. The Pi listener may need its streaming command restarted.')
            if self.stop_event.is_set():
                break
            # Reset budget only after a sustained connection, not after one stray frame.
            if time.monotonic() - began > 30 and self.sequence - initial_sequence >= FPS * 20:
                failures = 0
            failures += 1
            if failures < self.config.reconnect_attempts:
                self.stop_event.wait(min(2 ** (failures - 1), 8))
        with self.lock:
            if not self.stop_event.is_set():
                self.connection = 'exhausted'
                self.connection_error = 'Reconnect attempts exhausted. Restart the Pi stream, then restart this server.'

    def _preview(self):
        while not self.stop_event.wait(0.2):
            with self.lock:
                if not self._fresh() or self.sequence == self.preview_sequence:
                    continue
                frame, seq, stamp = self.frame, self.sequence, self.frame_at
            out = io.BytesIO()
            Image.frombytes('RGB', (WIDTH, HEIGHT), frame).resize((640, 360)).save(out, format='JPEG', quality=75)
            with self.changed:
                self.preview, self.preview_sequence, self.preview_at = out.getvalue(), seq, stamp
                self.changed.notify_all()

    def _monitor(self):
        last_disk_check = 0
        while not self.stop_event.wait(0.25):
            with self.lock:
                proc, rec = self.proc, self.recording
                now = time.monotonic()
                stale = self.connection == 'live' and now - self.frame_at >= self.config.stale_seconds
                connecting_timeout = proc and self.connection == 'connecting' and now - self.proc_started > 12
                if stale:
                    self.connection = 'stale'
                    self.connection_error = 'No fresh camera frames. Reconnecting.'
                    if rec:
                        rec.stop('Camera frames became stale.')
            if stale or connecting_timeout:
                terminate(proc)
            if rec:
                rec.watchdog()
                if now - last_disk_check > 1 and not rec.done.is_set():
                    last_disk_check = now
                    try:
                        self._check_space()
                    except ServiceError as exc:
                        rec.stop(str(exc))

    def status(self):
        with self.lock:
            fresh = self._fresh()
            state = 'stale' if self.connection == 'live' and not fresh else self.connection
            return dict(camera=dict(state=state, fresh=fresh, error=self.connection_error,
                                    frame_age_seconds=time.monotonic() - self.frame_at if self.frame_at else None,
                                    attempt=self.attempt, simulated=self.config.simulated),
                        recording=self.recording.snapshot() if self.recording else dict(state='idle', wall_seconds=0),
                        server_time=utc_now())

    def _check_space(self):
        if shutil.disk_usage(self.config.capture_dir).free < self.config.min_free_mb * 1024 * 1024:
            raise ServiceError('Insufficient free storage. Free space on the laptop before capturing.', 507)

    def start_recording(self):
        with self.lock:
            if self.recording and not self.recording.done.is_set():
                return self.recording.snapshot()
            if self.stop_event.is_set() or not self._fresh():
                raise ServiceError('Camera is disconnected or stale.')
            self._check_space()
            self.recording = Recording(self.config)
            return self.recording.snapshot()

    def stop_recording(self):
        with self.lock:
            if self.recording:
                self.recording.stop()
                return self.recording.snapshot()
            return dict(state='idle', wall_seconds=0)

    def take_photo(self):
        with self.lock:
            if self.stop_event.is_set() or not self._fresh():
                raise ServiceError('Camera is disconnected or stale.')
            if self.recording and self.recording.snapshot()['state'] == 'saving':
                raise ServiceError('Recording is saving. Please wait.')
            frame, stamp = self.frame, self.frame_utc
        self._check_space()
        path = self.config.capture_dir / new_name('.jpg')
        with path.open('xb') as out:
            Image.frombytes('RGB', (WIDTH, HEIGHT), frame).save(out, format='JPEG', quality=95)
        data = dict(name=path.name, type='photo', state='completed', started_at=stamp, ended_at=stamp,
                    size_bytes=path.stat().st_size, width=WIDTH, height=HEIGHT, simulated=self.config.simulated)
        save_json(path.with_suffix('.json'), data)
        return data

    def jpeg(self):
        with self.lock:
            if not self._fresh() or not self.preview or time.monotonic() - self.preview_at >= self.config.stale_seconds:
                raise ServiceError('No fresh preview frame.', 503)
            return self.preview, self.preview_sequence

    def gallery(self):
        items = []
        for path in self.config.capture_dir.glob('mousecam_*.json'):
            if path.is_symlink():
                continue
            try:
                data = json.loads(path.read_text(encoding='utf-8'))
                name = data.get('name', '')
                if not NAME.fullmatch(name) or path.stem != name.rsplit('.', 1)[0]:
                    continue
                if data.get('state') not in ('completed', 'interrupted', 'failed'):
                    continue
                media = self.config.capture_dir / name
                data['available'] = data['state'] in ('completed', 'interrupted') and media.is_file() and not media.is_symlink() and media.resolve().parent == self.config.capture_dir
                items.append(data)
            except (ValueError, OSError):
                continue
        return sorted(items, key=lambda item: item['started_at'], reverse=True)

    def media_path(self, name):
        if not NAME.fullmatch(name):
            raise ServiceError('Capture not found.', 404)
        path = self.config.capture_dir / name
        sidecar = path.with_suffix('.json')
        if path.is_symlink() or sidecar.is_symlink() or path.resolve().parent != self.config.capture_dir:
            raise ServiceError('Capture not found.', 404)
        try:
            data = json.loads(sidecar.read_text(encoding='utf-8'))
            if data.get('name') != name or data.get('state') not in ('completed', 'interrupted') or not path.is_file():
                raise ValueError()
        except (OSError, ValueError):
            raise ServiceError('Capture not found.', 404) from None
        return path

    def close(self):
        self.stop_event.set()
        with self.changed:
            rec = self.recording
            if rec:
                rec.stop('Server shut down during recording.')
            proc = self.proc
            self.changed.notify_all()
        terminate(proc)
        for thread in self.threads:
            thread.join(timeout=5)
        if rec and not rec.done.wait(20):
            terminate(rec.proc)
            rec.done.wait(5)
