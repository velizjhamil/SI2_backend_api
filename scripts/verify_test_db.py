"""Read-only proof that the guarded endpoint reaches the dedicated test cluster."""

import os
import ipaddress
import re
import subprocess
import sys
from urllib.parse import urlsplit

from sqlalchemy import create_engine, text

from pytest_guard import (
    DATABASE,
    DATA_PATH,
    NETWORK,
    PROJECT,
    SERVICE,
    USERNAME,
    VOLUME,
    UnsafeTestEnvironment,
    _inspect,
    database_url_from_docker,
    validate_environment,
)


class TestDatabaseVerificationError(RuntimeError):
    """Raised without exposing connection strings or environment values."""


def _same_host_ip(actual: str, expected: str) -> bool:
    """Compare host IPs while accepting PostgreSQL's explicit full-width prefix."""
    try:
        actual_interface = ipaddress.ip_interface(actual)
        expected_interface = ipaddress.ip_interface(expected)
    except ValueError:
        return False
    return (
        actual_interface.network.prefixlen == actual_interface.max_prefixlen
        and expected_interface.network.prefixlen == expected_interface.max_prefixlen
        and actual_interface.ip == expected_interface.ip
    )


def verify_test_database() -> str:
    """Validate Docker identity and prove the endpoint's PostgreSQL cluster ID."""
    stage = "guard/url"
    evidence = ""
    try:
        url = database_url_from_docker()
        os.environ["DATABASE_URL"] = url
        validate_environment()

        # Re-inspect only after guard validation, then execute against the exact
        # immutable container ID returned by Docker rather than its mutable name.
        stage = "container metadata"
        container = _inspect("container", "si2-backend-test-test-db-1")
        container_id = container.get("Id")
        labels = container.get("Config", {}).get("Labels") or {}
        networks = container.get("NetworkSettings", {}).get("Networks") or {}
        network = networks.get(NETWORK) or {}
        container_address = network.get("IPAddress")
        mounts = [mount for mount in container.get("Mounts", []) if mount.get("Destination") == DATA_PATH]
        ports = container.get("NetworkSettings", {}).get("Ports", {}).get("5432/tcp") or []
        if not (
            container_id
            and container_address
            and container.get("Name") == "/si2-backend-test-test-db-1"
            and labels.get("com.docker.compose.project") == PROJECT
            and labels.get("com.docker.compose.service") == SERVICE
            and container.get("Config", {}).get("Image") == "postgres:16-alpine"
            and container.get("State", {}).get("Running") is True
            and len(mounts) == 1
            and mounts[0].get("Type") == "volume"
            and mounts[0].get("Name") == VOLUME
            and len(ports) == 1
            and ports[0].get("HostIp") == "127.0.0.1"
            and ports[0].get("HostPort") == str(urlsplit(url).port)
        ):
            raise TestDatabaseVerificationError("Container metadata mismatch.")

        stage = "control data"
        control = subprocess.run(
            ["docker", "exec", container_id, "pg_controldata", DATA_PATH],
            check=True,
            capture_output=True,
            text=True,
        )
        match = re.search(r"^Database system identifier:\s*(\d+)\s*$", control.stdout, re.MULTILINE)
        if match is None:
            raise TestDatabaseVerificationError("System identifier unavailable.")
        expected_system_id = match.group(1)

        stage = "read-only connection"
        engine = create_engine(url, pool_pre_ping=True)
        try:
            with engine.connect() as connection:
                connection.exec_driver_sql("SET TRANSACTION READ ONLY")
                row = connection.execute(text("""
                    SELECT current_database(), current_user,
                           inet_server_addr()::text, inet_server_port(),
                           (SELECT system_identifier::text FROM pg_control_system())
                """)).one()
            stage = "identity comparison"
            expected_identity = (DATABASE, USERNAME, container_address, 5432, expected_system_id)
            fields = ("database", "user", "address", "port", "system_id")
            matches = (
                row[0] == expected_identity[0],
                row[1] == expected_identity[1],
                _same_host_ip(row[2], expected_identity[2]),
                row[3] == expected_identity[3],
                row[4] == expected_identity[4],
            )
            if not all(matches):
                evidence = "; mismatch-fields=" + ",".join(
                    field for field, matches_field in zip(fields, matches) if not matches_field
                )
                raise TestDatabaseVerificationError("Endpoint identity mismatch.")
        finally:
            engine.dispose()
        return url
    except Exception:
        # Never include driver, Docker, URL, or environment exception text.
        raise TestDatabaseVerificationError(f"stage={stage}{evidence}") from None


def main() -> int:
    try:
        verify_test_database()
    except (TestDatabaseVerificationError, UnsafeTestEnvironment) as exc:
        stage = str(exc) if isinstance(exc, TestDatabaseVerificationError) else "guard/url"
        print(f"Refusing: dedicated PostgreSQL test-cluster verification failed ({stage}).", file=sys.stderr)
        return 2
    print("Verified the guarded endpoint against the dedicated PostgreSQL test cluster.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
