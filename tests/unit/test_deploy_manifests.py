"""Deployment manifests: release pins and the Deployment invariants.

Reads the raw files (no kubectl, no network). The release workflow itself is
checked by actionlint and zizmor, not here.
"""

import tomllib
from pathlib import Path
from typing import Any, cast

import pytest
import yaml

ROOT = Path(__file__).resolve().parents[2]
IMAGE = "ghcr.io/wixyvir/cvestream"
DEPLOYMENT = ROOT / "deploy" / "k8s" / "base" / "deployment.yaml"
COMPOSE = ROOT / "deploy" / "compose" / "compose.yml"

Manifest = dict[str, Any]


def _load(path: Path) -> Manifest:
    return cast(Manifest, yaml.safe_load(path.read_text(encoding="utf-8")))


@pytest.fixture(scope="module")
def version() -> str:
    pyproject = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    return cast(str, pyproject["project"]["version"])


@pytest.fixture(scope="module")
def deployment() -> Manifest:
    return _load(DEPLOYMENT)


@pytest.fixture(scope="module")
def pod(deployment: Manifest) -> Manifest:
    return cast(Manifest, deployment["spec"]["template"]["spec"])


@pytest.fixture(scope="module")
def container(pod: Manifest) -> Manifest:
    containers = cast(list[Manifest], pod["containers"])
    assert len(containers) == 1
    return containers[0]


def test_kubernetes_image_is_pinned_to_the_release(
    container: Manifest, version: str
) -> None:
    assert container["image"] == f"{IMAGE}:{version}", (
        "deploy/k8s/base/deployment.yaml must use the pyproject.toml version"
    )


def test_compose_image_is_pinned_to_the_release(version: str) -> None:
    image = _load(COMPOSE)["services"]["cvestream"]["image"]
    assert image == f"{IMAGE}:{version}", (
        "deploy/compose/compose.yml must use the pyproject.toml version"
    )


def test_single_replica_recreate_and_grace_period(deployment: Manifest) -> None:
    spec = deployment["spec"]
    assert spec["replicas"] == 1
    assert spec["strategy"]["type"] == "Recreate"
    assert spec["template"]["spec"]["terminationGracePeriodSeconds"] == 60


def test_pod_environment_is_isolated(pod: Manifest) -> None:
    assert pod["enableServiceLinks"] is False
    assert pod["automountServiceAccountToken"] is False


def test_pod_security_context_is_restricted(pod: Manifest) -> None:
    context = pod["securityContext"]
    assert context["runAsNonRoot"] is True
    assert context["runAsUser"] == 999
    assert context["runAsGroup"] == 999
    assert context["fsGroup"] == 999
    assert context["seccompProfile"]["type"] == "RuntimeDefault"


def test_container_security_context_is_restricted(container: Manifest) -> None:
    context = container["securityContext"]
    assert context["readOnlyRootFilesystem"] is True
    assert context["allowPrivilegeEscalation"] is False
    assert context["capabilities"]["drop"] == ["ALL"]


@pytest.mark.parametrize(
    ("probe", "command"),
    [
        ("startupProbe", ["cvestream", "healthcheck", "--ready"]),
        ("readinessProbe", ["cvestream", "healthcheck", "--ready"]),
        ("livenessProbe", ["cvestream", "healthcheck"]),
    ],
)
def test_probes_exec_the_healthcheck(
    container: Manifest, probe: str, command: list[str]
) -> None:
    assert container[probe]["exec"]["command"] == command
    assert container[probe]["timeoutSeconds"] >= 2, (
        "each probe starts Python and waits up to 2 s"
    )


def test_no_sub_path_mounts(container: Manifest) -> None:
    for mount in cast(list[Manifest], container["volumeMounts"]):
        assert "subPath" not in mount, (
            f"{mount['mountPath']}: a subPath mount does not follow Secret updates"
        )
