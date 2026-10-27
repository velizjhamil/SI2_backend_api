"""Run pytest only after deriving DATABASE_URL from verified Docker resources."""

import os
import subprocess
import sys

from pytest_guard import UnsafeTestEnvironment, database_url_from_docker


def main() -> int:
    try:
        test_database_url = database_url_from_docker()
    except UnsafeTestEnvironment as exc:
        print(f"Refusing to run tests: {exc}", file=sys.stderr)
        return 2

    environment = os.environ.copy()
    environment["DATABASE_URL"] = test_database_url
    return subprocess.call([sys.executable, "-m", "pytest", *sys.argv[1:]], env=environment)


if __name__ == "__main__":
    raise SystemExit(main())
