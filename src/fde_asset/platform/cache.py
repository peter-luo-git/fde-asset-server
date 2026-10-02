"""极简 TTL 缓存。

推荐的精确模式要调模型，一次十几秒；同一个人反复打开同一个项目不该每次都等。
缓存只放"算出来的推荐"，不放资产内容本身——资产的可见性每次都重新判，
缓存键里带了用户 id，所以不会把别人能看的东西缓存给我。
"""

from __future__ import annotations

import threading
import time
from dataclasses import dataclass
from typing import Any


@dataclass
class _Entry:
    value: Any
    expires_at: float


class TtlCache:
    def __init__(self, ttl_seconds: float = 600, max_entries: int = 500) -> None:
        self.ttl = ttl_seconds
        self.max_entries = max_entries
        self._data: dict[str, _Entry] = {}
        self._lock = threading.Lock()

    def get(self, key: str) -> Any | None:
        now = time.monotonic()
        with self._lock:
            entry = self._data.get(key)
            if entry is None:
                return None
            if entry.expires_at < now:
                self._data.pop(key, None)
                return None
            return entry.value

    def set(self, key: str, value: Any) -> None:
        now = time.monotonic()
        with self._lock:
            if len(self._data) >= self.max_entries:
                # 简单粗暴：满了就清掉已过期的，还满就清掉最早到期的那一半
                expired = [k for k, v in self._data.items() if v.expires_at < now]
                for k in expired:
                    self._data.pop(k, None)
                if len(self._data) >= self.max_entries:
                    oldest = sorted(self._data.items(), key=lambda kv: kv[1].expires_at)
                    for k, _ in oldest[: len(oldest) // 2]:
                        self._data.pop(k, None)
            self._data[key] = _Entry(value=value, expires_at=now + self.ttl)

    def invalidate(self, prefix: str = "") -> int:
        """清掉匹配前缀的条目；不传前缀就全清。"""
        with self._lock:
            keys = [k for k in self._data if not prefix or k.startswith(prefix)]
            for key in keys:
                self._data.pop(key, None)
            return len(keys)

    def stats(self) -> dict[str, Any]:
        now = time.monotonic()
        with self._lock:
            alive = sum(1 for entry in self._data.values() if entry.expires_at >= now)
        return {"entries": alive, "ttl_seconds": self.ttl}


#: 推荐结果缓存。进程内即可：重启丢了也只是重算一次
recommendation_cache = TtlCache(ttl_seconds=600)
