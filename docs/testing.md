# Isolated backend tests

The backend tests can create, migrate, and delete database records. Importing `app.db.session` creates the SQLAlchemy engine but does not by itself connect to the database or run schema DDL; importing the repository-root `main.py` entrypoint calls `init_db()`, which connects and runs schema DDL. Some tests may also require migrations and reference data beyond what `init_db()` creates. Use only the disposable Compose target below, never the development `compose.yml` database.

## Quick path

From the backend repository root, with Docker Compose installed:

```sh
pip install -r requirements-dev.txt
docker compose -p si2-backend-test -f compose.test.yml up -d test-db
python scripts/run_tests.py
```

Pass normal pytest arguments after the launcher, for example `python scripts/run_tests.py tests/test_auth.py -q`. The launcher reads Docker metadata and sets `DATABASE_URL` in the pytest child process before pytest starts. Do not invoke pytest directly for DB-backed tests.

## Safety boundary

- This Compose project has its own project/service labels, default network, and named data volume; it does not mount `bd.sql`, use the development database, or share host resources. Its randomly assigned PostgreSQL port is bound only to loopback.
- Before test collection, pytest verifies the exact Compose project/service/container labels and name, running state, dedicated named volume and mount, dedicated network labels, and the published port. It also validates that `DATABASE_URL` has the expected test credentials/database and maps to that exact port. Missing or mismatched Docker metadata fails closed.
- The launcher and guard are read-only with respect to Docker: they inspect metadata but never start, stop, or remove resources. Start the test service explicitly with the command above. The test suite itself is not read-only: application initialization and DB-backed tests may alter this disposable database.
- This guard proves Docker resource configuration, not PostgreSQL server identity through a database connection. It cannot defend against a Docker daemon/host controlled by an attacker or tests that deliberately bypass the guard or alter their own connection settings.
- Keep the test volume between runs unless you intentionally remove the disposable test data. No cleanup command is provided here.
