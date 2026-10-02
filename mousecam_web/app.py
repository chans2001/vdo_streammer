import logging
import threading
import time

from flask import Flask, Response, g, jsonify, request, send_file, send_from_directory

from .auth import Auth
from .config import ROOT
from .service import CameraService, ServiceError


def create_app(config, service=None):
    app = Flask(__name__, static_folder=None)
    app.config['MAX_CONTENT_LENGTH'] = 4096
    service = service or CameraService(config)
    auth = Auth(config)
    app.extensions.update(camera=service, auth=auth)
    preview_slots = threading.BoundedSemaphore(8)

    @app.before_request
    def protect():
        if not request.path.startswith('/api/'):
            return None
        origin = request.headers.get('Origin')
        same_origin = request.host_url.rstrip('/')
        if origin and origin != same_origin and origin not in config.allowed_origins:
            raise ServiceError('Frontend origin is not allowed.', 403)
        # Opaque bearer auth is mandatory; cookies are neither set nor accepted.
        # CSRF cannot supply this Authorization header. Reject form login as well.
        if request.method == 'OPTIONS':
            if request.headers.get('Access-Control-Request-Method', '') not in ('GET', 'POST'):
                raise ServiceError('Method not allowed.', 403)
            headers = {x.strip().lower() for x in request.headers.get('Access-Control-Request-Headers', '').split(',') if x.strip()}
            if headers - {'authorization', 'content-type'}:
                raise ServiceError('Headers not allowed.', 403)
            return Response(status=204)
        if request.method == 'POST' and not request.is_json:
            raise ServiceError('Use application/json.', 415)
        if request.path == '/api/login' and request.method == 'POST':
            return None
        authorization = request.headers.get('Authorization', '')
        g.token = authorization[7:] if authorization.startswith('Bearer ') else ''
        if not auth.valid(g.token):
            raise ServiceError('Please sign in. Your session may have expired.', 401)

    @app.after_request
    def response_headers(response):
        response.headers['Cache-Control'] = 'no-store'
        response.headers['X-Content-Type-Options'] = 'nosniff'
        response.headers['Referrer-Policy'] = 'no-referrer'
        response.headers['X-Frame-Options'] = 'DENY'
        response.headers['Content-Security-Policy'] = "default-src 'self'; script-src 'self'; style-src 'self'; img-src 'self' blob:; media-src 'self' blob:; connect-src 'self'; frame-ancestors 'none'; base-uri 'none'; form-action 'self'"
        origin = request.headers.get('Origin')
        if request.path.startswith('/api/'):
            response.vary.add('Origin')
            if origin in config.allowed_origins:
                response.headers['Access-Control-Allow-Origin'] = origin
                response.headers['Access-Control-Allow-Methods'] = 'GET, POST, OPTIONS'
                response.headers['Access-Control-Allow-Headers'] = 'Authorization, Content-Type'
                response.headers['Access-Control-Max-Age'] = '600'
        return response

    @app.errorhandler(ServiceError)
    def service_error(error):
        return jsonify(error=str(error)), error.status

    @app.errorhandler(OSError)
    def io_error(error):
        logging.exception('Filesystem or subprocess error')
        return jsonify(error='Storage or encoder operation failed. Check the laptop console and free disk space.'), 500

    @app.get('/')
    def index():
        return send_from_directory(ROOT / 'frontend', 'index.html')

    @app.get('/<name>')
    def asset(name):
        if name not in ('app.js', 'styles.css', 'config.js', 'favicon.svg'):
            raise ServiceError('Not found.', 404)
        return send_from_directory(ROOT / 'frontend', name)

    @app.post('/api/login')
    def login():
        body = request.get_json(silent=True)
        if not isinstance(body, dict):
            raise ServiceError('A JSON object is required.', 400)
        return jsonify(auth.login(body.get('code'), request.remote_addr or 'unknown'))

    @app.post('/api/logout')
    def logout():
        auth.logout(g.token)
        return jsonify(ok=True)

    @app.get('/api/status')
    def status():
        return jsonify(service.status())

    @app.post('/api/photos')
    def photo():
        return jsonify(service.take_photo()), 201

    @app.post('/api/recording/start')
    def start():
        return jsonify(service.start_recording())

    @app.post('/api/recording/stop')
    def stop():
        return jsonify(service.stop_recording())

    @app.get('/api/captures')
    def captures():
        return jsonify(captures=service.gallery())

    @app.get('/api/media/<path:name>')
    def media(name):
        return send_file(service.media_path(name), conditional=True,
                         as_attachment=request.args.get('download') == '1', download_name=name)

    @app.get('/api/preview.jpg')
    def preview_jpg():
        frame, _ = service.jpeg()
        return Response(frame, mimetype='image/jpeg')

    @app.get('/api/preview.mjpeg')
    def preview_mjpeg():
        service.jpeg()
        if not preview_slots.acquire(blocking=False):
            raise ServiceError('Preview viewer limit reached.', 429)
        token = g.token
        def frames():
            last = -1
            # Rotate streams periodically so expired/revoked sessions and slow clients exit.
            deadline = time.monotonic() + 60
            try:
                while not service.stop_event.is_set() and time.monotonic() < deadline and auth.valid(token):
                    try:
                        jpeg, seq = service.jpeg()
                    except ServiceError:
                        break
                    if seq != last:
                        last = seq
                        yield b'--frame\r\nContent-Type: image/jpeg\r\nContent-Length: ' + str(len(jpeg)).encode() + b'\r\n\r\n' + jpeg + b'\r\n'
                    service.stop_event.wait(0.2)
            finally:
                preview_slots.release()
        response = Response(frames(), content_type='multipart/x-mixed-replace; boundary=frame')
        return response

    return app
