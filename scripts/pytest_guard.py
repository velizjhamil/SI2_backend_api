"""Fail-closed identity checks for the disposable PostgreSQL test target."""

import json
import os
import subprocess
from urllib.parse import quote, urlsplit


PROJECT = "si2-backend-test"
SERVICE = "test-db"
CONTAINER = f"{PROJECT}-{SERVICE}-1"
VOLUME = f"{PROJECT}_test_data"
NETWORK = f"{PROJECT}_default"
DATABASE = "si2_test_db"
USERNAME = "si2_test_user"
PASSWORD = "si2_test_password"
DATA_PATH = "/var/lib/postgresql/data"


class UnsafeTestEnvironment(RuntimeError):
    """Raised when test database identity cannot be proven from Docker metadata."""


def _inspect(resource_type: str, resource_name: str) -> dict:
    try:
        result = subprocess.run(
            ["docker", "inspect", "--type", resource_type, resource_name],
            check=True,
            capture_output=True,
            text=True,
        )
        items = json.loads(result.stdout)
        if len(items) != 1:
            raise ValueError("unexpected inspect result count")
        return items[0]
    except Exception as exc:
        # Avoid echoing Docker output, which can contain environment values.
        raise UnsafeTestEnvironment(
            f"Cannot verify the dedicated test {resource_type}; start it using the documented Compose command."
        ) from exc


def _expected_label(labels: dict, key: str, expected: str) -> bool:
    return labels.get(key) == expected


def database_url_from_docker() -> str:
    """Verify Compose container/network/volume identity and return its local URL."""
    container = _inspect("container", CONTAINER)
    labels = container.get("Config", {}).get("Labels") or {}
    if not (
        _expected_label(labels, "com.docker.compose.project", PROJECT)
        and _expected_label(labels, "com.docker.compose.service", SERVICE)
        and container.get("Name") == f"/{CONTAINER}"
        and container.get("Config", {}).get("Image") == "postgres:16-alpine"
        and container.get("State", {}).get("Running") is True
    ):
        raise UnsafeTestEnvironment("The PostgreSQL container identity does not match the test Compose project.")
    container_environment = dict(
        entry.split("=", 1)
        for entry in container.get("Config", {}).get("Env", [])
        if "=" in entry
    )
    if not all(
        container_environment.get(key) == value
        for key, value in (
            ("POSTGRES_DB", DATABASE),
            ("POSTGRES_USER", USERNAME),
            ("POSTGRES_PASSWORD", PASSWORD),
        )
    ):
        raise UnsafeTestEnvironment("The test container database configuration does not match the isolated test target.")

    mounts = [m for m in container.get("Mounts", []) if m.get("Destination") == DATA_PATH]
    if len(mounts) != 1 or mounts[0].get("Type") != "volume" or mounts[0].get("Name") != VOLUME:
        raise UnsafeTestEnvironment("The test container is not using its dedicated Compose volume.")
    volume = _inspect("volume", VOLUME)
    volume_labels = volume.get("Labels") or {}
    if not (
        volume.get("Name") == VOLUME
        and _expected_label(volume_labels, "com.docker.compose.project", PROJECT)
        and _expected_label(volume_labels, "com.docker.compose.volume", "test_data")
    ):
        raise UnsafeTestEnvironment("The PostgreSQL data volume is not owned by the test Compose project.")

    networks = container.get("NetworkSettings", {}).get("Networks") or {}
    network = networks.get(NETWORK)
    if not network or set(networks) != {NETWORK}:
        raise UnsafeTestEnvironment("The test container is not attached to its dedicated Compose network.")
    network_metadata = _inspect("network", NETWORK)
    network_labels = network_metadata.get("Labels") or {}
    if not (
        _expected_label(network_labels, "com.docker.compose.project", PROJECT)
        and _expected_label(network_labels, "com.docker.compose.network", "default")
    ):
        raise UnsafeTestEnvironment("The Docker network is not owned by the test Compose project.")

    bindings = container.get("NetworkSettings", {}).get("Ports", {}).get("5432/tcp")
    if not isinstance(bindings, list) or len(bindings) != 1:
        raise UnsafeTestEnvironment("The test PostgreSQL endpoint must have exactly one published port.")
    binding = bindings[0]
    if binding.get("HostIp") != "127.0.0.1" or not binding.get("HostPort", "").isdigit():
        raise UnsafeTestEnvironment("The test PostgreSQL port is not published exclusively on loopback.")

    return (
        f"postgresql://{quote(USERNAME)}:{quote(PASSWORD)}@127.0.0.1:"
        f"{binding['HostPort']}/{DATABASE}"
    )


def validate_environment() -> None:
    expected_url = database_url_from_docker()
    configured_url = os.environ.get("DATABASE_URL")
    if not configured_url:
        raise UnsafeTestEnvironment("DATABASE_URL must be set by scripts/run_tests.py.")

    expected = urlsplit(expected_url)
    configured = urlsplit(configured_url)
    if (
        configured.scheme != "postgresql"
        or configured.username != USERNAME
        or configured.password != PASSWORD
        or configured.hostname != "127.0.0.1"
        or configured.port != expected.port
        or configured.path != f"/{DATABASE}"
        or configured.query
        or configured.fragment
    ):
        raise UnsafeTestEnvironment(
            "DATABASE_URL does not point to the verified, dedicated test container endpoint."
        )
