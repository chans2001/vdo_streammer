import logging
import signal

from waitress import create_server

from .app import create_app
from .config import Config


def main():
    logging.basicConfig(level=logging.INFO, format='%(asctime)s %(levelname)s %(message)s')
    config = Config.from_env()
    config.capture_dir.mkdir(parents=True, exist_ok=True)
    # OS-held lock is released even after a crash. Prevent multiple capture owners.
    lock = (config.capture_dir / '.server.lock').open('a+b')
    try:
        if __import__('os').name == 'nt':
            import msvcrt
            lock.seek(0)
            if not lock.read(1):
                lock.write(b'0')
                lock.flush()
            lock.seek(0)
            msvcrt.locking(lock.fileno(), msvcrt.LK_NBLCK, 1)
        else:
            import fcntl
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError:
        lock.close()
        raise SystemExit('Another MouseCam server owns this capture directory.')
    app = create_app(config)
    service = app.extensions['camera']
    server = None
    def shutdown(*_):
        raise KeyboardInterrupt
    signal.signal(signal.SIGTERM, shutdown)
    if hasattr(signal, 'SIGBREAK'):
        signal.signal(signal.SIGBREAK, shutdown)
    try:
        # Bind before opening the Pi so a second start cannot steal its connection.
        server = create_server(app, host=config.host, port=config.port, threads=config.threads,
                               connection_limit=64, channel_timeout=20, outbuf_high_watermark=262144,
                               outbuf_overflow=262144, max_request_body_size=4096,
                               clear_untrusted_proxy_headers=True)
        service.start()
        logging.info('MouseCam %s: http://localhost:%s (LAN clients use laptop Wi-Fi/Ethernet IP)',
                     'SIMULATED CAMERA' if config.simulated else 'Pi camera', config.port)
        server.run()
    except KeyboardInterrupt:
        logging.info('Finalizing recording and shutting down...')
    finally:
        service.close()
        if server:
            server.close()
            server.task_dispatcher.shutdown()
        lock.close()


if __name__ == '__main__':
    main()
