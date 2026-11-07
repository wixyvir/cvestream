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
DEV_COMPOSE = ROOT / "compose.yml"
VALKEY_STATEFULSET = (
    ROOT / "deploy" / "k8s" / "overlays" / "example" / "valkey-statefulset.yaml"
)

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


def test_example_valkey_image_matches_the_dev_stack() -> None:
    # Dependabot does not bump these pins: keep the copies equal by hand.
    expected = _load(DEV_COMPOSE)["services"]["valkey"]["image"]
    documents = yaml.safe_load_all(VALKEY_STATEFULSET.read_text(encoding="utf-8"))
    statefulset = next(d for d in documents if d["kind"] == "StatefulSet")
    containers = statefulset["spec"]["template"]["spec"]["containers"]
    assert [c["image"] for c in containers] == [expected], (
        "the example Valkey StatefulSet must use the Valkey image of compose.yml"
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


# --- story 3.4: the opt-in valkey-ui component and the dev compose service ---

UI_COMPONENT = ROOT / "deploy" / "k8s" / "components" / "valkey-ui"
UI_TEST_OVERLAY = ROOT / "tests" / "k8s" / "example-with-valkey-ui"
EXAMPLE_OVERLAY = ROOT / "deploy" / "k8s" / "overlays" / "example"
UI_IMAGE = "redis/redisinsight:3.8.0"


def _load_all(path: Path) -> list[Manifest]:
    return [
        cast(Manifest, doc)
        for doc in yaml.safe_load_all(path.read_text(encoding="utf-8"))
        if doc
    ]


@pytest.fixture(scope="module")
def ui_component_files() -> dict[str, Manifest]:
    return {path.name: _load(path) for path in sorted(UI_COMPONENT.glob("*.yaml"))}


@pytest.fixture(scope="module")
def ui_deployment(ui_component_files: dict[str, Manifest]) -> Manifest:
    return ui_component_files["deployment.yaml"]


@pytest.fixture(scope="module")
def ui_pod(ui_deployment: Manifest) -> Manifest:
    return cast(Manifest, ui_deployment["spec"]["template"]["spec"])


@pytest.fixture(scope="module")
def ui_container(ui_pod: Manifest) -> Manifest:
    containers = cast(list[Manifest], ui_pod["containers"])
    assert len(containers) == 1
    return containers[0]


def test_the_ui_component_is_a_component_listing_its_four_files() -> None:
    kustomization = _load(UI_COMPONENT / "kustomization.yaml")
    assert kustomization["apiVersion"] == "kustomize.config.k8s.io/v1alpha1"
    assert kustomization["kind"] == "Component"
    assert kustomization["resources"] == [
        "deployment.yaml",
        "service.yaml",
        "networkpolicy.yaml",
        "secret.example.yaml",
    ]


def test_the_ui_pod_security_context_is_restricted(ui_pod: Manifest) -> None:
    context = ui_pod["securityContext"]
    assert context["runAsNonRoot"] is True
    run_as_user = context["runAsUser"]
    assert isinstance(run_as_user, int) and run_as_user > 0
    assert context["seccompProfile"]["type"] == "RuntimeDefault"
    assert ui_pod["automountServiceAccountToken"] is False
    assert ui_pod["enableServiceLinks"] is False
    for volume in cast(list[Manifest], ui_pod["volumes"]):
        assert "hostPath" not in volume
    assert ui_pod.get("hostNetwork") is not True


def test_the_ui_container_security_context_is_restricted(
    ui_container: Manifest,
) -> None:
    context = ui_container["securityContext"]
    assert context["allowPrivilegeEscalation"] is False
    assert context["capabilities"]["drop"] == ["ALL"]
    assert context["readOnlyRootFilesystem"] is True
    assert "privileged" not in context or context["privileged"] is False
    for port in cast(list[Manifest], ui_container["ports"]):
        assert "hostPort" not in port


def test_the_ui_image_is_an_exact_pin_equal_to_the_compose_one(
    ui_container: Manifest,
) -> None:
    image = cast(str, ui_container["image"])
    assert image == UI_IMAGE  # exact tag, never latest
    compose = _load(DEV_COMPOSE)["services"]["valkey-ui"]
    assert compose["image"] == image, (
        "the component and compose.yml must pin the same UI image"
    )


def test_the_ui_service_is_cluster_ip_and_the_component_has_no_ingress(
    ui_component_files: dict[str, Manifest],
) -> None:
    service = ui_component_files["service.yaml"]
    assert service["kind"] == "Service"
    assert service["spec"].get("type", "ClusterIP") == "ClusterIP"
    for name, doc in ui_component_files.items():
        assert doc["kind"] != "Ingress", name


def test_the_ui_networkpolicy_denies_ingress_and_allows_only_valkey_and_dns(
    ui_component_files: dict[str, Manifest],
) -> None:
    policy = ui_component_files["networkpolicy.yaml"]
    spec = policy["spec"]
    assert spec["podSelector"]["matchLabels"]["app.kubernetes.io/name"] == "valkey-ui"
    assert spec["policyTypes"] == ["Ingress", "Egress"]
    assert spec["ingress"] == []
    egress = cast(list[Manifest], spec["egress"])
    assert len(egress) == 2
    dns, valkey = egress
    assert dns["ports"] == [
        {"port": 53, "protocol": "UDP"},
        {"port": 53, "protocol": "TCP"},
    ]
    assert dns["to"][0]["podSelector"]["matchLabels"]["k8s-app"] == "kube-dns"
    assert (
        valkey["to"][0]["podSelector"]["matchLabels"]["app.kubernetes.io/name"]
        == "valkey"
    )
    assert valkey["ports"] == [{"port": 6379, "protocol": "TCP"}]


def test_the_ui_pod_opts_into_the_valkey_networkpolicy(ui_deployment: Manifest) -> None:
    labels = ui_deployment["spec"]["template"]["metadata"]["labels"]
    assert labels["cvestream.io/valkey-access"] == "true"
    valkey_policy = _load(EXAMPLE_OVERLAY / "networkpolicy-valkey.yaml")
    peers = cast(list[Manifest], valkey_policy["spec"]["ingress"][0]["from"])
    assert any(
        peer.get("podSelector", {})
        .get("matchLabels", {})
        .get("cvestream.io/valkey-access")
        == "true"
        for peer in peers
    ), "the example Valkey NetworkPolicy must still admit the opt-in label"


def test_only_the_test_overlay_enables_the_ui_component() -> None:
    example = _load(EXAMPLE_OVERLAY / "kustomization.yaml")
    assert not any(
        "valkey-ui" in str(component) for component in example.get("components", [])
    )
    test_overlay = _load(UI_TEST_OVERLAY / "kustomization.yaml")
    assert any(
        "components/valkey-ui" in str(component)
        for component in test_overlay.get("components", [])
    )


def test_the_dev_compose_ui_is_loopback_only_and_keeps_the_password_in_a_secret() -> (
    None
):
    compose = _load(DEV_COMPOSE)
    services = compose["services"]
    ui = services["valkey-ui"]
    for port in cast(list[str], ui["ports"]):
        assert port.startswith("127.0.0.1:"), port
    assert "ports" not in services["valkey"]
    assert "RI_REDIS_PASSWORD" not in cast(dict[str, str], ui["environment"])
    secret_name = ui["secrets"][0]
    assert compose["secrets"][secret_name]["file"].endswith(
        "cvestream-ui.redisinsight.json"
    )


def test_the_ui_component_secret_holds_only_a_placeholder(
    ui_component_files: dict[str, Manifest],
) -> None:
    secret = ui_component_files["secret.example.yaml"]
    assert secret["kind"] == "Secret"
    value = secret["stringData"]["databases.json"]
    assert "REPLACE-ME" in value
    assert "password" in value
