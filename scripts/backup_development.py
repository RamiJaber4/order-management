"""Back up the original local database; never print credentials."""
from datetime import datetime
import os
from pathlib import Path
import subprocess
from dotenv import dotenv_values
from sqlalchemy.engine import make_url

root = Path(__file__).resolve().parents[1]
url = make_url(dotenv_values(root / '.env')['DATABASE_URL'])
if url.host not in ('localhost', '127.0.0.1'):
    raise SystemExit('Expected a local development database; review its host first.')
folder = root / 'backups'
folder.mkdir(exist_ok=True)
target = folder / f'development-{datetime.now():%Y%m%d-%H%M%S-%f}.dump'
env = os.environ.copy()
env['PGPASSWORD'] = url.password or ''
subprocess.run([
    'pg_dump', '--host', url.host, '--port', str(url.port or 5432),
    '--username', url.username, '--dbname', url.database,
    '--format=custom', '--file', str(target),
], env=env, check=True)
subprocess.run(['pg_restore', '--list', str(target)], check=True, stdout=subprocess.DEVNULL)
print(f'Backup created and archive readable: {target}')
