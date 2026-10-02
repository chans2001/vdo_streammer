"""Tk-free frame/FFmpeg primitives adapted from MouseCam Control v1.1."""
import shutil
import subprocess
from pathlib import Path

WIDTH, HEIGHT, FPS = 1280, 720, 15
FRAME_BYTES = WIDTH * HEIGHT * 3
CREATE_FLAGS = getattr(subprocess, 'CREATE_NO_WINDOW', 0)


def find_ffmpeg():
    existing = shutil.which('ffmpeg')
    if existing:
        return existing
    folder = Path.home() / 'Downloads' / 'mousecam-ffmpeg'
    return str(next(folder.rglob('ffmpeg.exe'), '')) if folder.exists() else ''


def read_frame(pipe):
    data = bytearray()
    while len(data) < FRAME_BYTES:
        chunk = pipe.read(FRAME_BYTES - len(data))
        if not chunk:
            return None
        data.extend(chunk)
    return bytes(data)


def acquisition_command(config):
    cmd = [config.ffmpeg, '-hide_banner', '-loglevel', 'error', '-nostdin']
    if config.simulated:
        cmd += ['-re', '-f', 'lavfi', '-i', f'testsrc2=size={WIDTH}x{HEIGHT}:rate={FPS}']
    else:
        cmd += ['-fflags', 'nobuffer', '-flags', 'low_delay', '-rw_timeout', '5000000',
                '-probesize', '32768', '-analyzeduration', '1000000', '-f', 'h264', '-i', config.camera_url]
    return cmd + ['-an', '-vf', f'setpts=N/({FPS}*TB),scale={WIDTH}:{HEIGHT}',
                  '-fps_mode', 'passthrough', '-pix_fmt', 'rgb24', '-f', 'rawvideo', 'pipe:1']


def recording_command(ffmpeg, path):
    # Input rate only. Never combine output -r with passthrough.
    return [ffmpeg, '-hide_banner', '-loglevel', 'error', '-n', '-f', 'rawvideo',
            '-pixel_format', 'rgb24', '-video_size', f'{WIDTH}x{HEIGHT}', '-framerate', str(FPS),
            '-i', 'pipe:0', '-an', '-c:v', 'libx264', '-preset', 'ultrafast', '-crf', '23',
            '-pix_fmt', 'yuv420p', '-movflags', '+faststart', '-f', 'mp4', str(path)]


def terminate(proc):
    if proc and proc.poll() is None:
        proc.terminate()
        try:
            proc.wait(timeout=3)
        except subprocess.TimeoutExpired:
            proc.kill()
            proc.wait(timeout=3)
