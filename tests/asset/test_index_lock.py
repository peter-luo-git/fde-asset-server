"""建索引、重算等级、重建关系排队进行，不能和另一处同时改同一批行。

调整可见范围、评审通过的请求会就地重建索引并收尾，asset-worker 发现仓库有新提交也会重建；
两边交错着改 assets、asset_relations，PostgreSQL 上会死锁（冒烟测试里"收回分享"报过一次）。
"""

from __future__ import annotations

import threading
import time

from fde_asset.core.db import INDEX_LOCK_KEY, lock_index
from fde_asset.modules.asset import indexer


class _Dialect:
    def __init__(self, name: str) -> None:
        self.name = name


class _Conn:
    def __init__(self, dialect: str) -> None:
        self.dialect = _Dialect(dialect)
        self.sql: list[str] = []

    def exec_driver_sql(self, sql: str) -> None:
        self.sql.append(sql)


def test_postgres_transactions_take_the_shared_index_lock() -> None:
    conn = _Conn("postgresql")
    lock_index(conn)
    assert conn.sql == [f"SELECT pg_advisory_xact_lock({INDEX_LOCK_KEY})"]


def test_sqlite_needs_no_lock() -> None:
    conn = _Conn("sqlite")
    lock_index(conn)
    assert conn.sql == []


def test_finish_index_waits_for_an_index_in_progress(monkeypatch) -> None:
    """同一个进程里，另一处还在建索引时，收尾要等它建完，不能插进去改关系。"""
    steps: list[str] = []
    monkeypatch.setattr(indexer, "refresh_grades", lambda engine: steps.append("grades"))
    monkeypatch.setattr(indexer, "auto_link", lambda engine: steps.append("relations"))
    monkeypatch.setattr(indexer, "_snapshot_and_notify", lambda engine, owner_of: None)

    finished = threading.Event()
    with indexer._INDEX_LOCK:
        worker = threading.Thread(
            target=lambda: (indexer.finish_index(object()), finished.set()), daemon=True
        )
        worker.start()
        time.sleep(0.2)
        assert not finished.is_set() and steps == [], "建索引期间收尾不能开始"
    worker.join(timeout=5)
    assert finished.is_set() and steps == ["grades", "relations"]


def test_index_all_can_finish_while_holding_the_lock(indexed) -> None:
    """index_all 建完索引接着收尾，同一线程重入这把锁不能把自己卡住。"""
    done = threading.Event()
    worker = threading.Thread(
        target=lambda: (indexer.finish_index(indexed.engine), done.set()), daemon=True
    )
    with indexer._INDEX_LOCK:
        indexer.finish_index(indexed.engine)
    worker.start()
    worker.join(timeout=30)
    assert done.is_set()
