"""Exercise full tests, real outages and restart persistence in disposable Docker.

Creates a unique Compose project and deletes only its temporary resources.
Never reads private environment files or uses existing database volumes.
"""
import json
import argparse
from pathlib import Path
import subprocess
import time
from uuid import uuid4

ROOT = Path(__file__).resolve().parents[1]


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--skip-pytest", action="store_true",
                        help="Run only outage, restart and persistence checks")
    args = parser.parse_args()
    compose = ["docker", "compose", "-f", str(ROOT / "tests/compose.reliability.yaml"),
               "-p", "reliability-" + uuid4().hex[:12]]

    def run(*args, capture=False):
        result = subprocess.run([*compose, *args], cwd=ROOT, check=True,
                                text=True, capture_output=capture, timeout=600)
        return result.stdout.strip() if capture else None

    def python(code):
        return run("exec", "-T", "api", "python", "-c", code, capture=True)

    def probe(path, expected):
        # urllib raises HTTPError for intentional 503 responses.
        result = json.loads(python(
            "import json, urllib.request, urllib.error\n"
            "try:\n"
            f"    response = urllib.request.urlopen('http://127.0.0.1:8000{path}', timeout=12)\n"
            "except urllib.error.HTTPError as exc:\n"
            "    response = exc\n"
            "print(json.dumps({'status': response.code, 'request_id': response.headers.get('X-Request-ID'), "
            "'body': response.read().decode()}))"
        ))
        assert result["status"] == expected, result
        assert result["request_id"], result
        assert "reliability_test_password" not in result["body"], result
        return result

    def await_ready():
        deadline = time.monotonic() + 60
        while True:
            try:
                probe("/health/ready", 200)
                return
            except (AssertionError, subprocess.CalledProcessError):
                if time.monotonic() >= deadline:
                    raise
                time.sleep(1)

    try:
        run("up", "--build", "-d", "--wait", "api")
        if not args.skip_pytest:
            run("run", "--rm", "--no-deps", "tests")
        probe("/health/live", 200)
        probe("/health/ready", 200)
        print("Initial health checks passed.", flush=True)

        # A unique customer in the disposable database proves persistence.
        customer_id = python(
            "from app.db.session import SessionLocal; from app.models import Customer\n"
            "with SessionLocal() as session:\n"
            "    customer = Customer(name='Restart probe', email='restart@example.com')\n"
            "    session.add(customer); session.commit(); print(customer.id)"
        )
        run("stop", "db")
        probe("/health/live", 200)
        probe("/health/ready", 503)
        probe("/products", 503)
        run("start", "db")
        await_ready()
        print("Real PostgreSQL outage: live=200, ready/products=503, recovery=200.", flush=True)

        # Terminate only this API process (not a Docker stop, which disables restart).
        container = run("ps", "-q", "api", capture=True)

        def restart_count():
            return int(subprocess.run(
                ["docker", "inspect", "--format", "{{.RestartCount}}", container],
                check=True, capture_output=True, text=True, timeout=15,
            ).stdout.strip())

        before = restart_count()
        try:
            python("import os, signal; os.kill(1, signal.SIGTERM)")
        except subprocess.CalledProcessError:
            pass  # docker exec can exit with the terminated container.
        deadline = time.monotonic() + 60
        while restart_count() <= before:
            if time.monotonic() >= deadline:
                raise AssertionError("API process exited but Docker did not restart it")
            time.sleep(1)
        await_ready()
        print("API process exit recovered automatically; Docker restart count increased.", flush=True)

        run("restart", "db", "api")
        await_ready()
        persisted = python(
            "from app.db.session import SessionLocal; from app.models import Customer\n"
            "with SessionLocal() as session:\n"
            f"    print(session.get(Customer, {int(customer_id)}).email)"
        )
        assert persisted == "restart@example.com"
        # Recreate the DB container; the project's own volume must retain the row.
        run("up", "-d", "--force-recreate", "--no-deps", "db")
        await_ready()
        persisted = python(
            "from app.db.session import SessionLocal; from app.models import Customer\n"
            "with SessionLocal() as session:\n"
            f"    print(session.get(Customer, {int(customer_id)}).email)"
        )
        assert persisted == "restart@example.com"
        print("Container restart and database recreation preserved data.", flush=True)
    finally:
        run("down", "--volumes", "--remove-orphans", "--rmi", "local")


if __name__ == "__main__":
    main()
