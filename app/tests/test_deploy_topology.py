"""ADR-0011: invariants of the compose fragments and the three stacks.

Parses the YAML directly (no docker) and merges ``include.path`` the way
Compose does for the keys that matter here: mappings merge recursively,
sequences (``ports``, ``networks``) are unioned.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

yaml = pytest.importorskip("yaml")

_DEPLOY = Path(__file__).resolve().parents[2] / "deploy"

_STACK_INCLUDES: dict[str, list[str]] = {
    "single": ["../compose/control.yml", "../compose/media.yml", "./single.override.yml"],
    "nl1": ["../compose/control.yml", "./nl1.overlay.yml"],
    "nl2": ["../compose/media.yml"],
}

_APP_IMAGE_VARS: dict[str, str] = {
    "migrate": "IMAGE_BOT",
    "bot": "IMAGE_BOT",
    "backup": "IMAGE_BACKUP",
    "api": "IMAGE_API",
    "worker": "IMAGE_WORKER",
    "cleanup": "IMAGE_WORKER",
}

_CONTROL_SERVICES = {"postgres", "redis", "migrate", "bot", "backup"}
_MEDIA_SERVICES = {"api", "worker", "cleanup", "nginx", "certbot"}


def _load(path: Path) -> dict[str, Any]:
    data = yaml.safe_load(path.read_text(encoding="utf-8"))
    assert isinstance(data, dict), path
    return data


def _merge(base: Any, extra: Any) -> Any:
    if isinstance(base, dict) and isinstance(extra, dict):
        merged = dict(base)
        for key, value in extra.items():
            merged[key] = _merge(base[key], value) if key in base else value
        return merged
    if isinstance(base, list) and isinstance(extra, list):
        return base + [item for item in extra if item not in base]
    return extra


def _include_entry(stack: str) -> dict[str, Any]:
    compose = _load(_DEPLOY / stack / "docker-compose.yml")
    assert set(compose) == {"include"}, f"{stack}: only include is allowed at top level"
    (entry,) = compose["include"]
    return entry


def _render(stack: str) -> dict[str, Any]:
    entry = _include_entry(stack)
    merged: dict[str, Any] = {}
    for rel in entry["path"]:
        fragment = _load(_DEPLOY / stack / rel)
        merged = _merge(merged, {k: v for k, v in fragment.items() if not k.startswith("x-")})
    return merged


def _services(stack: str) -> dict[str, dict[str, Any]]:
    return _render(stack)["services"]


def _published(services: dict[str, dict[str, Any]]) -> dict[str, list[str]]:
    return {name: svc["ports"] for name, svc in services.items() if svc.get("ports")}


def _all_compose_files() -> list[Path]:
    return sorted(
        {
            (_DEPLOY / stack / rel).resolve()
            for stack, rels in _STACK_INCLUDES.items()
            for rel in rels
        }
    )


@pytest.mark.parametrize("stack", sorted(_STACK_INCLUDES))
def test_stack_includes_expected_fragments(stack: str) -> None:
    entry = _include_entry(stack)
    assert entry["path"] == _STACK_INCLUDES[stack]
    assert entry["project_directory"] == "."
    assert entry["env_file"] == ".env"


@pytest.mark.parametrize(
    ("stack", "expected"),
    [
        ("single", _CONTROL_SERVICES | _MEDIA_SERVICES),
        ("nl1", _CONTROL_SERVICES | {"api"}),
        ("nl2", _MEDIA_SERVICES),
    ],
)
def test_stack_service_set(stack: str, expected: set[str]) -> None:
    assert set(_services(stack)) == expected


def test_fragments_publish_ports_only_on_nginx() -> None:
    assert _published(_load(_DEPLOY / "compose" / "control.yml")["services"]) == {}
    assert set(_published(_load(_DEPLOY / "compose" / "media.yml")["services"])) == {"nginx"}


def test_single_publishes_only_nginx_ports() -> None:
    published = _published(_services("single"))
    assert set(published) == {"nginx"}
    assert sorted(published["nginx"]) == ["443:443", "80:80"]


def test_nl1_binds_data_ports_to_private_ip_only() -> None:
    published = _published(_services("nl1"))
    assert set(published) == {"postgres", "redis"}
    for ports in published.values():
        for port in ports:
            assert port.startswith("${NL1_PRIVATE_IP"), port


def test_nl2_publishes_only_nginx_ports() -> None:
    assert set(_published(_services("nl2"))) == {"nginx"}


@pytest.mark.parametrize("stack", ["single", "nl2"])
def test_nginx_has_no_route_to_data_plane(stack: str) -> None:
    assert _services(stack)["nginx"]["networks"] == ["dwtgbot_media"]


@pytest.mark.parametrize("stack", ["single", "nl2"])
def test_nginx_reloads_to_pick_up_renewed_certs(stack: str) -> None:
    nginx = _services(stack)["nginx"]
    (script,) = nginx["command"]
    assert nginx["entrypoint"] == ["/bin/sh", "-c"]
    assert "nginx -s reload" in script
    # The stock entrypoint renders /etc/nginx/templates; bypassing it would
    # leave the media vhost unconfigured.
    assert "exec /docker-entrypoint.sh nginx" in script


def test_single_app_services_join_both_networks() -> None:
    services = _services("single")
    for name in ("api", "worker", "cleanup"):
        assert set(services[name]["networks"]) == {"dwtgbot_internal", "dwtgbot_media"}, name
    for name in ("postgres", "redis", "bot", "backup", "migrate"):
        assert services[name]["networks"] == ["dwtgbot_internal"], name


def test_single_media_services_wait_for_migrate() -> None:
    services = _services("single")
    for name in ("api", "worker", "cleanup"):
        dep = services[name]["depends_on"]["migrate"]
        assert dep["condition"] == "service_completed_successfully", name


def test_single_worker_yields_to_control_plane() -> None:
    services = _services("single")
    worker = services["worker"]
    assert worker["cpu_shares"] == "${WORKER_CPU_SHARES:-512}"
    assert worker["blkio_config"]["weight"] == 300
    assert worker["oom_score_adj"] > 0
    assert services["postgres"]["oom_score_adj"] < services["redis"]["oom_score_adj"] < 0
    assert worker["environment"]["DB_POOL_SIZE"].startswith("${WORKER_DB_POOL_SIZE")


@pytest.mark.parametrize("stack", sorted(_STACK_INCLUDES))
def test_app_images_require_immutable_tag(stack: str) -> None:
    for name, svc in _services(stack).items():
        var = _APP_IMAGE_VARS.get(name)
        if var is not None:
            assert svc["image"].startswith(f"${{{var}:?"), f"{stack}/{name}: {svc['image']}"


@pytest.mark.parametrize("stack", sorted(_STACK_INCLUDES))
def test_container_names_are_unique_and_prefixed(stack: str) -> None:
    names = [svc["container_name"] for svc in _services(stack).values()]
    assert len(names) == len(set(names))
    assert all(name.startswith("dwtgbot_") for name in names)


def test_single_reuses_split_container_and_volume_names() -> None:
    single = _render("single")
    split = _merge(_render("nl1"), _render("nl2"))
    single_names = {n: s["container_name"] for n, s in single["services"].items()}
    split_names = {n: s["container_name"] for n, s in split["services"].items()}
    # api is the only service present on both split hosts; NL-2's name wins.
    assert single_names == split_names
    assert single["volumes"] == split["volumes"]
    for key, volume in single["volumes"].items():
        assert volume["name"] == f"dwtgbot_{key}"


def test_compose_files_have_no_forbidden_options() -> None:
    for path in _all_compose_files():
        for name, svc in (_load(path).get("services") or {}).items():
            where = f"{path.name}/{name}"
            assert not svc.get("privileged"), where
            assert "cap_add" not in svc, where
            assert svc.get("pid") != "host", where
            assert svc.get("network_mode") != "host", where
