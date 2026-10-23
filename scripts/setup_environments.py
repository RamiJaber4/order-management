"""Create private local Docker environment files without overwriting them."""
from pathlib import Path
import secrets
from dotenv import dotenv_values
from sqlalchemy.engine import make_url

root = Path(__file__).resolve().parents[1]
config = dotenv_values(root / '.env')
url = make_url(config['DATABASE_URL'])
if url.host not in ('localhost', '127.0.0.1'):
    raise SystemExit('Expected a local development database; review its host first.')
secret = config.get('JWT_SECRET', '')
if len(secret.encode()) < 32:
    raise SystemExit('Set a valid JWT_SECRET in .env first.')
values = {
    '.env.development': {
        'DATABASE_URL': url.set(host='host.docker.internal').render_as_string(hide_password=False),
        'JWT_SECRET': secret,
    },
    '.env.test': {
        'DATABASE_URL': 'postgresql+psycopg://postgres:local_dev_password@db:5432/ecommerce_db',
        'JWT_SECRET': secrets.token_hex(32),
    },
}
for filename, settings in values.items():
    path = root / filename
    if path.exists():
        print(f'{filename}: kept existing file')
        continue
    def quote(value):
        return "'" + value.replace('\\', '\\\\').replace("'", "\\'") + "'"
    with path.open('x', encoding='utf-8') as stream:
        stream.write(''.join(f'{key}={quote(value)}\n' for key, value in settings.items()))
    print(f'{filename}: created (credentials hidden)')
