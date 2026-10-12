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
