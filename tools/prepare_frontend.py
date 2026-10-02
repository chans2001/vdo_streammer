"""Copy only public assets and set runtime config for a manual static deployment."""
import argparse
import ipaddress
import json
from pathlib import Path
import re
from urllib.parse import urlsplit

ROOT = Path(__file__).resolve().parents[1]
PUBLIC_FILES = ('index.html', 'app.js', 'styles.css', 'favicon.svg', 'config.js', 'vercel.json', '.vercelignore')


def public_https_origin(value):
    value = value.strip()
    parsed = urlsplit(value)
    host = parsed.hostname or ''
    if (parsed.scheme != 'https' or not host or parsed.username is not None or parsed.password is not None
            or parsed.path not in ('', '/') or parsed.query or parsed.fragment or '\\' in value
            or any(c.isspace() for c in value) or not parsed.netloc):
        raise ValueError('Use a public HTTPS origin only, without credentials, /api, query, or fragment.')
    # Accessing port also validates malformed port numbers.
    port = parsed.port
    try:
        address = ipaddress.ip_address(host)
    except ValueError:
        if ('.' not in host or host.endswith(('.localhost', '.local', '.internal'))
                or not re.fullmatch(r'[a-zA-Z0-9.-]+', host)
                or any(not label or label.startswith('-') or label.endswith('-') for label in host.split('.'))):
            raise ValueError('Use a public backend hostname, not a local laptop or Pi address.')
    else:
        if not address.is_global:
            raise ValueError('Private, loopback, and reserved addresses are not public backend URLs.')
    authority = f'[{host}]' if ':' in host else host
    if port and port != 443:
        authority += f':{port}'
    return 'https://' + authority


def prepare(api_base_url, destination, source=ROOT / 'frontend'):
    origin = public_https_origin(api_base_url)
    source, destination = Path(source).resolve(), Path(destination).absolute()
    if destination.is_symlink() or destination.is_junction():
        raise ValueError('Deployment directory must not be a filesystem link.')
    destination = destination.resolve()
    if destination == source or source in destination.parents or destination in source.parents:
        raise ValueError('Use a separate deployment directory; do not overwrite the local frontend.')
    if destination.exists():
        extras = {path.name for path in destination.iterdir()} - set(PUBLIC_FILES) - {'.vercel'}
        if extras:
            raise ValueError('Deployment directory contains unexpected files. Review/remove them manually: ' + ', '.join(sorted(extras)))
    # Read the complete allowlist before writing anything. Never copy .env or directories.
    contents = {}
    for name in PUBLIC_FILES:
        path = source / name
        if path.is_symlink() or not path.is_file() or path.resolve().parent != source:
            raise ValueError('Missing or linked public asset: ' + name)
        contents[name] = path.read_bytes()
        target = destination / name
        if target.is_symlink() or target.is_dir():
            raise ValueError('Unsafe existing deployment asset: ' + name)
    contents['config.js'] = ('// Public runtime configuration. No credentials. Generated for manual deployment.\n'
                             'window.MOUSECAM_CONFIG = Object.freeze(' + json.dumps({'API_BASE_URL': origin}) + ');\n').encode()
    vercel = json.loads(contents['vercel.json'])
    vercel['headers'][1]['headers'].append({
        'key': 'Content-Security-Policy',
        'value': "default-src 'self'; script-src 'self'; style-src 'self'; img-src 'self' blob:; media-src 'self' blob:; connect-src 'self' " + origin + "; frame-ancestors 'none'; base-uri 'none'; form-action 'self'",
    })
    contents['vercel.json'] = (json.dumps(vercel, indent=2) + '\n').encode()
    destination.mkdir(parents=True, exist_ok=True)
    for name, data in contents.items():
        (destination / name).write_bytes(data)
    return destination


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--api-base-url', required=True, help='Real reachable public HTTPS backend origin')
    args = parser.parse_args()
    try:
        destination = prepare(args.api_base_url, ROOT / '.deploy' / 'frontend')
    except (ValueError, OSError) as error:
        parser.exit(2, f'Not prepared: {error}\n')
    print('Prepared public frontend only:', destination)
    print('Files:', ', '.join(PUBLIC_FILES))
    print('Local frontend/config.js and backend .env were not changed. Nothing was deployed.')


if __name__ == '__main__':
    main()
