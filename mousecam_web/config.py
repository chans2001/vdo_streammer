import os
from dataclasses import dataclass, field
from pathlib import Path
from urllib.parse import urlsplit

from dotenv import load_dotenv

from .media import find_ffmpeg

ROOT = Path(__file__).resolve().parent.parent


@dataclass
class Config:
    access_code: str
    ffmpeg: str = field(default_factory=find_ffmpeg)
    capture_dir: Path = ROOT / 'captures'
    camera_url: str = 'tcp://192.168.77.2:8888'
    simulated: bool = False
    host: str = '0.0.0.0'
    port: int = 8080
    threads: int = 24
    session_seconds: int = 28800
    min_free_mb: int = 512
    stale_seconds: float = 3
    reconnect_attempts: int = 5
    allowed_origins: tuple[str, ...] = ()
    queue_frames: int = 30

    def __post_init__(self):
        self.capture_dir = Path(self.capture_dir).resolve()
        if len(self.access_code) < 12:
            raise ValueError('Set MOUSECAM_ACCESS_CODE to at least 12 characters in .env.')
        if not self.ffmpeg or not Path(self.ffmpeg).is_file():
            raise ValueError('FFmpeg not found. Set MOUSECAM_FFMPEG in .env.')
        for origin in self.allowed_origins:
            p = urlsplit(origin)
            if p.scheme not in ('https', 'http') or not p.netloc or p.path or p.query or p.fragment or p.username or '*' in origin:
                raise ValueError('Allowed origins must be exact http(s) origins without a trailing slash.')
        if self.threads < 16 or self.session_seconds < 1 or self.stale_seconds <= 0 or self.reconnect_attempts < 1 or self.queue_frames < 1 or self.min_free_mb < 0:
            raise ValueError('Invalid limits (Waitress needs at least 16 threads).')

    @classmethod
    def from_env(cls):
        load_dotenv(ROOT / '.env')
        def env(key, default):
            return os.getenv('MOUSECAM_' + key, str(default))
        path = Path(env('CAPTURE_DIR', 'captures'))
        return cls(
            access_code=env('ACCESS_CODE', ''), ffmpeg=env('FFMPEG', '') or find_ffmpeg(),
            capture_dir=path if path.is_absolute() else ROOT / path,
            camera_url=env('CAMERA_URL', 'tcp://192.168.77.2:8888'),
            simulated=env('SIMULATED', 'false').lower() == 'true',
            host=env('HOST', '0.0.0.0'), port=int(env('PORT', 8080)),
            threads=int(env('THREADS', 24)), session_seconds=int(env('SESSION_SECONDS', 28800)),
            min_free_mb=int(env('MIN_FREE_MB', 512)), stale_seconds=float(env('STALE_SECONDS', 3)),
            reconnect_attempts=int(env('RECONNECT_ATTEMPTS', 5)),
            allowed_origins=tuple(x.strip() for x in env('ALLOWED_ORIGINS', '').split(',') if x.strip()),
        )
