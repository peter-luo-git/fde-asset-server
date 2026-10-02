"""推荐缓存：同一个人反复打开同一个项目不该每次都重算。"""

from __future__ import annotations

import time

from fde_asset.platform.cache import TtlCache
from tests.conftest import as_user


def test_hit_and_expire() -> None:
    cache = TtlCache(ttl_seconds=0.2)
    cache.set("k", 1)
    assert cache.get("k") == 1
    time.sleep(0.25)
    assert cache.get("k") is None, "过期要自己失效"


def test_invalidate_by_prefix() -> None:
    cache = TtlCache()
    cache.set("chen:a", 1)
    cache.set("chen:b", 2)
    cache.set("wang:a", 3)
    assert cache.invalidate("chen:") == 2
    assert cache.get("wang:a") == 3


def test_eviction_keeps_size_bounded() -> None:
    cache = TtlCache(ttl_seconds=60, max_entries=10)
    for index in range(30):
        cache.set(f"k{index}", index)
    assert cache.stats()["entries"] <= 10


def test_compute_is_cached_and_marked(client) -> None:
    chen = as_user(client, "chen")
    body = {"target_type": "engagement", "target_id": "policy-import"}
    first = chen.post("/api/v1/recommend/compute", json=body).json()
    second = chen.post("/api/v1/recommend/compute", json=body).json()
    assert first["mode"] == "keyword"
    assert second["mode"].endswith("-cached"), "第二次要走缓存，并且让调用方看得出来"
    assert [item["asset_id"] for item in first["items"]] == [
        item["asset_id"] for item in second["items"]
    ]


def test_refresh_skips_the_cache(client) -> None:
    chen = as_user(client, "chen")
    body = {"target_type": "engagement", "target_id": "policy-import"}
    chen.post("/api/v1/recommend/compute", json=body)
    refreshed = chen.post("/api/v1/recommend/compute", json={**body, "refresh": True}).json()
    assert not refreshed["mode"].endswith("-cached")


def test_linking_invalidates_the_cache(client) -> None:
    """关联之后已关联的不再推荐，缓存必须失效，否则会看到已经关联过的东西。"""
    chen = as_user(client, "chen")
    body = {"target_type": "engagement", "target_id": "policy-import"}
    first = chen.post("/api/v1/recommend/compute", json=body).json()
    target = first["items"][0]["asset_id"]

    as_user(client, "wang").post(
        "/api/v1/recommend/link",
        json={**body, "asset_id": target},
    )

    after = as_user(client, "chen").post("/api/v1/recommend/compute", json=body).json()
    assert not after["mode"].endswith("-cached")
    assert target not in [item["asset_id"] for item in after["items"]]


def test_cache_is_per_user(client) -> None:
    """可见范围因人而异，缓存不能把别人能看的给我。"""
    body = {"target_type": "engagement", "target_id": "policy-import"}
    chen = as_user(client, "chen").post("/api/v1/recommend/compute", json=body).json()
    zhao = as_user(client, "zhao").post("/api/v1/recommend/compute", json=body).json()
    assert len(zhao["items"]) <= len(chen["items"])
    assert not zhao["mode"].endswith("-cached"), "换个人不该命中别人的缓存"
