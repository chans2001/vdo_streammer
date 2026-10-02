import json

import pytest

from tools.prepare_frontend import PUBLIC_FILES, ROOT, prepare, public_https_origin


@pytest.mark.parametrize('url', ['http://camera.example.org', 'https://localhost', 'https://localhost:8080',
                               'https://127.0.0.1', 'https://192.168.77.2', 'https://172.17.157.129',
                               'https://[::1]', 'https://camera.local', 'https://camera.example.org/api',
                               'https://user:secret@camera.example.org', 'https://camera.example.org?token=secret',
                               'https://camera.example.org#secret', 'https://camera.example.org:invalid',
                               'https://camera.example.org\\evil', ''])
def test_reject_nonpublic_or_credential_urls(url):
    with pytest.raises(ValueError):
        public_https_origin(url)


def test_packaging_keeps_only_frontend_and_preserves_local_mode(tmp_path):
    local = (ROOT / 'frontend' / 'config.js').read_bytes()
    source = tmp_path / 'source'
    source.mkdir()
    for name in PUBLIC_FILES:
        (source / name).write_bytes((ROOT / 'frontend' / name).read_bytes())
    for name in ('.env', 'access-code.txt', 'mousecam.mp4', 'server.py'):
        (source / name).write_text('private-sentinel')
    destination = prepare('https://camera.example.org/', tmp_path / 'deploy', source)
    assert {path.name for path in destination.iterdir()} == set(PUBLIC_FILES)
    assert all(b'private-sentinel' not in path.read_bytes() for path in destination.iterdir())
    assert '"API_BASE_URL": "https://camera.example.org"' in (destination / 'config.js').read_text()
    assert (ROOT / 'frontend' / 'config.js').read_bytes() == local
    assert (source / 'config.js').read_bytes() == local
    settings = json.loads((destination / 'vercel.json').read_text())
    assert settings['framework'] is None
    assert settings['buildCommand'] == settings['installCommand'] == ''
    assert settings['outputDirectory'] == '.'
    assert not {'functions', 'rewrites', 'routes'} & settings.keys()
    csp = next(h['value'] for rule in settings['headers'] for h in rule['headers'] if h['key'] == 'Content-Security-Policy')
    assert "connect-src 'self' https://camera.example.org;" in csp
    assert '/*' in (destination / '.vercelignore').read_text()
    # Reconfiguration updates runtime config and exact CSP, preserving CLI project linkage.
    (destination / '.vercel').mkdir()
    (destination / '.vercel' / 'project.json').write_text('{}')
    prepare('https://new.example.org', destination, source)
    assert 'new.example.org' in (destination / 'config.js').read_text()
    assert (destination / '.vercel' / 'project.json').is_file()


def test_reject_dirty_deployment_folder(tmp_path):
    (tmp_path / '.env').write_text('private')
    with pytest.raises(ValueError, match='unexpected files'):
        prepare('https://camera.example.org', tmp_path)
    assert (tmp_path / '.env').read_text() == 'private'


def test_cannot_overwrite_local_frontend():
    with pytest.raises(ValueError, match='separate deployment'):
        prepare('https://camera.example.org', ROOT / 'frontend')
