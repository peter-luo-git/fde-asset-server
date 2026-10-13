"""真实容器验收的资源归属回归：拒绝覆盖，按标签清理，保留镜像/volume。"""

from __future__ import annotations

import json
import subprocess

import pytest


def test_existing_image_is_rejected_before_any_resource_creation(monkeypatch):
    from tests.docker_resources import OwnedDockerResources

    created = []

    def command(args, **kwargs):
        if args[1:3] == ["image", "inspect"]:
            return subprocess.CompletedProcess(args, 0, "[{}]", "")
        created.append(args)
        return subprocess.CompletedProcess(args, 0, "", "")

    monkeypatch.setattr(subprocess, "run", command)
    resources = OwnedDockerResources()
    with pytest.raises(RuntimeError, match="已存在"):
        resources.__enter__()
    assert created == []


def test_cleanup_leaves_unowned_containers_and_all_images_and_volumes(monkeypatch):
    from tests.docker_resources import OwnedDockerResources

    resources = OwnedDockerResources()
    remaining = {"owned": resources.owner, "foreign": "another-run"}
    images = {"preexisting", resources.tag}
    volumes = {"preexisting-volume"}

    def command(args, **kwargs):
        if args[1:3] == ["ps", "-a"]:
            return subprocess.CompletedProcess(args, 0, "owned\nforeign\n", "")
        if args[1:3] == ["container", "inspect"]:
            labels = {"fde.test.owner": remaining[args[-1]]}
            return subprocess.CompletedProcess(args, 0, json.dumps(labels), "")
        if args[1:3] == ["rm", "-f"]:
            del remaining[args[-1]]
            return subprocess.CompletedProcess(args, 0, "", "")
        if args[1] in {"rmi", "image"}:
            images.clear()
        if args[1] == "volume":
            volumes.clear()
        return subprocess.CompletedProcess(args, 0, "", "")

    monkeypatch.setattr(subprocess, "run", command)
    resources.cleanup_containers()
    assert remaining == {"foreign": "another-run"}
    assert images == {"preexisting", resources.tag}
    assert volumes == {"preexisting-volume"}


def test_resource_namespace_is_unique_per_case():
    from tests.docker_resources import OwnedDockerResources

    first, second = OwnedDockerResources(), OwnedDockerResources()
    assert first.tag != second.tag
    assert first.network != second.network
    assert first.container != second.container
    assert first.project != second.project


def test_cleanup_handles_owned_container_auto_removed_after_listing(monkeypatch):
    from tests.docker_resources import OwnedDockerResources

    remaining = {"foreign"}

    def command(args, **kwargs):
        if args[1:3] == ["ps", "-a"]:
            return subprocess.CompletedProcess(args, 0, "already-removed\n", "")
        if args[1:3] == ["container", "inspect"]:
            return subprocess.CompletedProcess(
                args, 1, "", "Error response from daemon: No such container: already-removed"
            )
        if args[1:3] == ["rm", "-f"]:
            remaining.clear()
        return subprocess.CompletedProcess(args, 0, "", "")

    monkeypatch.setattr(subprocess, "run", command)
    OwnedDockerResources().cleanup_containers()
    assert remaining == {"foreign"}


@pytest.mark.parametrize(
    "race_error",
    [
        "Error response from daemon: removal of container owned-id is already in progress",
        "Error response from daemon: No such container: owned-id",
    ],
)
def test_cleanup_confirms_exact_owned_container_is_gone_after_remove_race(monkeypatch, race_error):
    from tests.docker_resources import OWNER_LABEL, OwnedDockerResources

    resources = OwnedDockerResources()
    inspections = 0
    removed = []

    def command(args, **kwargs):
        nonlocal inspections
        if args[1:3] == ["ps", "-a"]:
            return subprocess.CompletedProcess(args, 0, "owned-id\n", "")
        if args[1:3] == ["container", "inspect"]:
            inspections += 1
            if inspections == 3:
                return subprocess.CompletedProcess(
                    args, 1, "", "Error: No such container: owned-id"
                )
            return subprocess.CompletedProcess(
                args, 0, json.dumps({OWNER_LABEL: resources.owner}), ""
            )
        if args[1:3] == ["rm", "-f"]:
            removed.append(args[-1])
            return subprocess.CompletedProcess(args, 1, "", race_error)
        raise AssertionError(f"不允许访问其他资源：{args}")

    monkeypatch.setattr(subprocess, "run", command)
    resources.cleanup_containers()
    assert inspections == 3  # Removal was confirmed, not merely ignored.
    assert removed == ["owned-id"]


@pytest.mark.parametrize(
    "error",
    [
        "permission denied while removing owned-id",
        "Error response from daemon: removal of container another-id is already in progress",
        "Error response from daemon: No such container: owned-id-extra",
    ],
)
def test_cleanup_preserves_remove_errors_unrelated_to_exact_owned_container(monkeypatch, error):
    from tests.docker_resources import OWNER_LABEL, OwnedDockerResources

    resources = OwnedDockerResources()

    def command(args, **kwargs):
        if args[1:3] == ["ps", "-a"]:
            return subprocess.CompletedProcess(args, 0, "owned-id\n", "")
        if args[1:3] == ["container", "inspect"]:
            return subprocess.CompletedProcess(
                args, 0, json.dumps({OWNER_LABEL: resources.owner}), ""
            )
        if args[1:3] == ["rm", "-f"]:
            return subprocess.CompletedProcess(args, 1, "", error)
        raise AssertionError(f"不允许访问其他资源：{args}")

    monkeypatch.setattr(subprocess, "run", command)
    with pytest.raises(RuntimeError) as caught:
        resources.cleanup_containers()
    assert str(caught.value) == error


@pytest.mark.parametrize(
    "after_error",
    [
        "permission denied",
        "Error: No such container: owned-id-extra",
        "owner-changed",
        "still-present",
    ],
)
def test_cleanup_remove_race_does_not_hide_failed_absence_or_ownership_check(
    monkeypatch, after_error
):
    from tests.docker_resources import OWNER_LABEL, OwnedDockerResources

    resources = OwnedDockerResources()
    inspections = 0
    race_error = "Error response from daemon: removal of container owned-id is already in progress"

    def command(args, **kwargs):
        nonlocal inspections
        if args[1:3] == ["ps", "-a"]:
            return subprocess.CompletedProcess(args, 0, "owned-id\n", "")
        if args[1:3] == ["container", "inspect"]:
            inspections += 1
            if inspections > 1 and after_error not in {"owner-changed", "still-present"}:
                return subprocess.CompletedProcess(args, 1, "", after_error)
            owner = (
                "another-owner"
                if inspections > 1 and after_error == "owner-changed"
                else resources.owner
            )
            return subprocess.CompletedProcess(args, 0, json.dumps({OWNER_LABEL: owner}), "")
        if args[1:3] == ["rm", "-f"]:
            return subprocess.CompletedProcess(args, 1, "", race_error)
        raise AssertionError(f"不允许访问其他资源：{args}")

    monkeypatch.setattr(subprocess, "run", command)
    with pytest.raises(RuntimeError) as caught:
        resources.cleanup_containers()
    assert inspections > 1
    if after_error in {"permission denied", "Error: No such container: owned-id-extra"}:
        assert str(caught.value) == after_error
    if after_error == "still-present":
        assert inspections <= 12  # Polling must remain bounded when removal never finishes.
