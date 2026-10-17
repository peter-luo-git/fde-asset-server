"""数据层：SQLAlchemy Core 表定义与连接管理（SQLite / PostgreSQL 通用）。"""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from sqlalchemy import (
    Boolean,
    Column,
    DateTime,
    Float,
    Index,
    Integer,
    MetaData,
    String,
    Table,
    Text,
    UniqueConstraint,
    create_engine,
    inspect,
    literal,
)
from sqlalchemy.engine import Engine, make_url

metadata = MetaData()


def _now() -> datetime:
    return datetime.now(timezone.utc)


assets = Table(
    "assets",
    metadata,
    Column("asset_id", String(64), primary_key=True),
    Column("scope", String(16), nullable=False),  # company | department | customer | engagement
    Column("department_code", String(64)),
    Column("customer_code", String(64), nullable=False, default=""),
    Column("engagement_slug", String(64)),
    Column("repo", String(200), nullable=False),
    Column("path", String(500), nullable=False),
    Column("kind", String(32), nullable=False),
    Column("name", String(128), nullable=False),
    Column("title", String(200), nullable=False, default=""),
    Column("summary", Text, nullable=False, default=""),
    Column("conclusion_md", Text, nullable=False, default=""),
    Column("applicability_json", Text, nullable=False, default="{}"),
    Column("tags_json", Text, nullable=False, default="[]"),
    Column("industry_json", Text, nullable=False, default="[]"),
    Column("owner_ref", String(128), nullable=False, default=""),
    Column("owner_kind", String(16), nullable=False, default=""),
    Column("owner_value", String(64), nullable=False, default=""),
    Column("lifecycle", String(16), nullable=False, default="experimental"),
    Column("quality", String(16), nullable=False, default="bronze"),
    Column("nature", String(16), nullable=False, default=""),
    Column("version", String(32), nullable=False, default="0.1.0"),
    Column("replaced_by", String(128), nullable=False, default=""),
    Column("source_json", Text, nullable=False, default="{}"),
    Column("kind_spec_json", Text, nullable=False, default="{}"),
    Column("attachments_json", Text, nullable=False, default="[]"),
    Column("content_text", Text, nullable=False, default=""),
    Column("commit_sha", String(64), nullable=False, default=""),
    # 资产目录的内容哈希：目录里任何文件变了它才变，用来判断这份资产是不是真的有新版本
    Column("tree_sha", String(64), nullable=False, default=""),
    # asset.yaml 里写的可见范围原样存一份，页面展示用；判断谁看得见查的是 asset_audiences
    Column("visibility_json", Text, nullable=False, default="[]"),
    Column("restricted", Boolean, nullable=False, default=False),
    Column("valid", Boolean, nullable=False, default=True),
    Column("created_at", DateTime(timezone=True), nullable=False, default=_now),
    Column("updated_at", DateTime(timezone=True), nullable=False, default=_now),
    Column("deleted_at", DateTime(timezone=True)),
    # 同一个负责人名下同类型不重名，由写入和索引时检查，不靠数据库约束：
    # 索引是从仓库重建出来的，仓库里真出现了重名也得能建完索引，再把重名的那份标成无效
    Index("ix_assets_owner_kind_name", "owner_ref", "kind", "name"),
)

#: 一份资产对谁可见：每一项一行。任何一项覆盖到某个人，他就看得见这份资产。
#: assets 表上的 scope 和几个归属列只记其中范围最大的那一项，给展示、排序、评审分派用。
asset_audiences = Table(
    "asset_audiences",
    metadata,
    Column("id", Integer, primary_key=True, autoincrement=True),
    Column("asset_id", String(64), nullable=False),
    Column("audience", String(16), nullable=False),  # company|department|customer|engagement|user
    Column("code", String(64), nullable=False, default=""),
    UniqueConstraint("asset_id", "audience", "code", name="uq_asset_audience"),
    Index("ix_asset_audiences_lookup", "audience", "code"),
)

asset_index_findings = Table(
    "asset_index_findings",
    metadata,
    Column("id", Integer, primary_key=True, autoincrement=True),
    Column("repo", String(200), nullable=False),
    Column("path", String(500), nullable=False),
    Column("kind", String(32), nullable=False, default=""),
    Column("name", String(128), nullable=False, default=""),
    Column("severity", String(16), nullable=False, default="error"),
    Column("code", String(64), nullable=False),
    Column("message", Text, nullable=False),
    Column("detected_at", DateTime(timezone=True), nullable=False, default=_now),
)

asset_relations = Table(
    "asset_relations",
    metadata,
    Column("id", Integer, primary_key=True, autoincrement=True),
    Column("from_asset_id", String(64), nullable=False),
    Column("to_ref", String(200), nullable=False),
    Column("to_asset_id", String(64)),
    Column("type", String(32), nullable=False, default="relatedTo"),
    Column("source", String(16), nullable=False, default="metadata"),  # metadata | body
)

asset_usages = Table(
    "asset_usages",
    metadata,
    Column("id", Integer, primary_key=True, autoincrement=True),
    Column("asset_id", String(64), nullable=False),
    Column("event", String(16), nullable=False),  # loaded | read | applied
    Column("session_id", String(64), nullable=False, default=""),
    Column("engagement_slug", String(64), nullable=False, default=""),
    Column("work_item_id", String(64), nullable=False, default=""),
    Column("snapshot_sha", String(64), nullable=False, default=""),
    Column("delivered_pr", Boolean, nullable=False, default=False),
    Column("actor_type", String(16), nullable=False, default="agent"),
    Column("actor_id", String(64), nullable=False, default=""),
    Column("created_at", DateTime(timezone=True), nullable=False, default=_now),
    UniqueConstraint("asset_id", "session_id", "event", name="uq_usage_session_event"),
)

asset_references = Table(
    "asset_references",
    metadata,
    Column("id", Integer, primary_key=True, autoincrement=True),
    Column("asset_id", String(64), nullable=False),
    Column("asset_version", String(32), nullable=False, default=""),
    Column(
        "source_type", String(32), nullable=False
    ),  # message | work_item | asset | session_summary
    Column("source_id", String(128), nullable=False),
    Column("engagement_slug", String(64), nullable=False, default=""),
    Column("created_by", String(64), nullable=False, default=""),
    Column("created_at", DateTime(timezone=True), nullable=False, default=_now),
    UniqueConstraint("asset_id", "source_type", "source_id", name="uq_reference_source"),
)

harvest_candidates = Table(
    "harvest_candidates",
    metadata,
    Column("candidate_id", String(64), primary_key=True),
    Column("kind", String(32), nullable=False),
    Column("scope", String(16), nullable=False, default="engagement"),
    Column("department_code", String(64), nullable=False, default=""),
    Column("engagement_slug", String(64), nullable=False, default=""),
    # 客户级草稿归哪个客户；没有它，提交时算不出目标仓库，也判不了谁能评审
    Column("customer_code", String(64), nullable=False, default=""),
    Column("name", String(128), nullable=False),
    Column("title", String(200), nullable=False, default=""),
    Column(
        "status", String(16), nullable=False, default="draft"
    ),  # draft|submitted|merged|rejected
    Column("origin", String(32), nullable=False, default="manual"),
    Column("source_json", Text, nullable=False, default="{}"),
    Column("files_json", Text, nullable=False, default="{}"),
    Column("checks_json", Text, nullable=False, default="{}"),
    Column("medium_risk_confirmed", Boolean, nullable=False, default=False),
    Column("created_by", String(64), nullable=False, default=""),
    Column("created_at", DateTime(timezone=True), nullable=False, default=_now),
    Column("updated_at", DateTime(timezone=True), nullable=False, default=_now),
    Column("review_id", String(64), nullable=False, default=""),
)

asset_reviews = Table(
    "asset_reviews",
    metadata,
    Column("review_id", String(64), primary_key=True),
    Column("candidate_id", String(64), nullable=False),
    Column("repo", String(200), nullable=False),
    Column("branch", String(200), nullable=False),
    Column("status", String(16), nullable=False, default="open"),  # open | merged | rejected
    Column("scope", String(16), nullable=False, default="engagement"),
    Column("summary", Text, nullable=False, default=""),
    Column("submitted_by", String(64), nullable=False, default=""),
    Column("submitted_at", DateTime(timezone=True), nullable=False, default=_now),
    Column("decided_by", String(64), nullable=False, default=""),
    Column("decided_at", DateTime(timezone=True)),
    Column("note", Text, nullable=False, default=""),
)

asset_events = Table(
    "asset_events",
    metadata,
    Column("id", Integer, primary_key=True, autoincrement=True),
    Column("type", String(64), nullable=False),
    Column("payload_json", Text, nullable=False, default="{}"),
    Column("created_at", DateTime(timezone=True), nullable=False, default=_now),
)

asset_leads = Table(
    "asset_leads",
    metadata,
    Column("lead_id", String(64), primary_key=True),
    Column("rule", String(16), nullable=False),
    Column("owner_user", String(64), nullable=False),
    Column("engagement_slug", String(64), nullable=False, default=""),
    Column("subject_type", String(32), nullable=False, default=""),
    Column("subject_id", String(64), nullable=False, default=""),
    Column("suggested_kind", String(32), nullable=False, default=""),
    Column("title", String(200), nullable=False, default=""),
    Column("detail", Text, nullable=False, default=""),
    Column("score", Float, nullable=False, default=0.0),
    Column("status", String(16), nullable=False, default="open"),  # open | ignored | drafted
    Column("created_at", DateTime(timezone=True), nullable=False, default=_now),
    UniqueConstraint("rule", "subject_type", "subject_id", name="uq_lead_subject"),
)

asset_recommendations = Table(
    "asset_recommendations",
    metadata,
    Column("recommendation_id", String(64), primary_key=True),
    Column("asset_id", String(64), nullable=False),
    Column("target_type", String(16), nullable=False),  # engagement | agent
    Column("target_id", String(64), nullable=False),
    Column("target_owner", String(64), nullable=False, default=""),
    Column("source", String(16), nullable=False, default="self"),  # self | dept_admin | auto
    Column("recommended_by", String(64), nullable=False, default=""),
    Column("reason_json", Text, nullable=False, default="[]"),
    Column("score", Float, nullable=False, default=0),
    # suggested（算出来还没动）| sent（推给别人了）| accepted | declined
    Column("status", String(16), nullable=False, default="suggested"),
    Column("decided_by", String(64), nullable=False, default=""),
    Column("decided_at", DateTime(timezone=True)),
    Column("note", Text, nullable=False, default=""),
    Column("created_at", DateTime(timezone=True), nullable=False, default=_now),
    Column("updated_at", DateTime(timezone=True), nullable=False, default=_now),
    UniqueConstraint("asset_id", "target_type", "target_id", name="uq_recommendation_target"),
)

#: 接受推荐后写进来：某个项目或 Agent 关联了哪些资产
target_assets = Table(
    "target_assets",
    metadata,
    Column("id", Integer, primary_key=True, autoincrement=True),
    Column("target_type", String(16), nullable=False),  # engagement | agent
    Column("target_id", String(64), nullable=False),
    Column("asset_id", String(64), nullable=False),
    Column("source", String(16), nullable=False, default="manual"),  # manual | recommendation
    # 这份资产在这个目标的 Agent 那里叫什么。关联时定下来，之后不变：
    # 平时就是资产的标识；目标已经有同类型同标识的另一份时，后来的这份加上负责人
    Column("mount_name", String(200), nullable=False, default=""),
    Column("created_by", String(64), nullable=False, default=""),
    Column("created_at", DateTime(timezone=True), nullable=False, default=_now),
    UniqueConstraint("target_type", "target_id", "asset_id", name="uq_target_asset"),
)

#: 必选资产：在这个范围里干活的 Agent 一定带上它，不用一个个去关联。
#: 由管理员或这个范围的负责人设置（评审通过时可以顺手设）。个人级只在这个人和 Agent 的私聊里生效
asset_mandatory = Table(
    "asset_mandatory",
    metadata,
    Column("id", Integer, primary_key=True, autoincrement=True),
    Column("asset_id", String(64), nullable=False),
    # company | department | customer | engagement | user
    Column("scope", String(16), nullable=False),
    # 全公司的不填；其他是部门代号、客户代号、项目标识、账号
    Column("code", String(64), nullable=False, default=""),
    Column("created_by", String(64), nullable=False, default=""),
    Column("created_at", DateTime(timezone=True), nullable=False, default=_now),
    UniqueConstraint("asset_id", "scope", "code", name="uq_asset_mandatory"),
    Index("ix_asset_mandatory_lookup", "scope", "code"),
)

#: 用过之后的反馈：这份资产到底帮没帮上忙
asset_feedback = Table(
    "asset_feedback",
    metadata,
    Column("id", Integer, primary_key=True, autoincrement=True),
    Column("asset_id", String(64), nullable=False),
    Column("verdict", String(16), nullable=False),  # helpful | not_helpful | outdated
    Column("note", Text, nullable=False, default=""),
    Column("engagement_slug", String(64), nullable=False, default=""),
    Column("created_by", String(64), nullable=False, default=""),
    Column("created_at", DateTime(timezone=True), nullable=False, default=_now),
    #: 选「已过时」时自动起草的修订草稿
    Column("candidate_id", String(64), nullable=False, default=""),
)

#: 应用资产的演示地址探活结果
app_health = Table(
    "app_health",
    metadata,
    Column("asset_id", String(64), primary_key=True),
    # online | offline | unreachable（平台够不着，多半是内网或本机）| skipped
    Column("status", String(16), nullable=False, default="unknown"),
    Column("network", String(16), nullable=False, default=""),
    Column("url", Text, nullable=False, default=""),
    Column("http_status", Integer, nullable=False, default=0),
    Column("latency_ms", Integer, nullable=False, default=0),
    Column("detail", Text, nullable=False, default=""),
    Column("checked_at", DateTime(timezone=True), nullable=False, default=_now),
    Column("last_online_at", DateTime(timezone=True)),
    # 这次结果是谁探的：server = 平台自己；browser = 某个用户的电脑替平台探的（平台够不着的网络）
    Column("checked_via", String(16), nullable=False, default="server"),
    Column("checked_by", String(64), nullable=False, default=""),
)

#: 应用的容器化演示：上传镜像 → 审核 → 手动启停
app_deployments = Table(
    "app_deployments",
    metadata,
    Column("asset_id", String(64), primary_key=True),
    Column("image_file", String(255), nullable=False, default=""),
    Column("image_tag", String(255), nullable=False, default=""),
    Column("image_bytes", Integer, nullable=False, default=0),
    Column("uploaded_by", String(64), nullable=False, default=""),
    Column("uploaded_at", DateTime(timezone=True)),
    # pending（待审核）| approved（允许运行）| rejected
    Column("review_status", String(16), nullable=False, default="pending"),
    Column("reviewed_by", String(64), nullable=False, default=""),
    Column("reviewed_at", DateTime(timezone=True)),
    Column("review_note", Text, nullable=False, default=""),
    # stopped | running | failed
    Column("run_status", String(16), nullable=False, default="stopped"),
    Column("container_id", String(128), nullable=False, default=""),
    Column("host_port", Integer, nullable=False, default=0),
    Column("access_url", Text, nullable=False, default=""),
    Column("started_by", String(64), nullable=False, default=""),
    Column("started_at", DateTime(timezone=True)),
    Column("last_error", Text, nullable=False, default=""),
)

#: 订阅：我自己盯什么。推荐是别人推给我，订阅是我主动关注
asset_subscriptions = Table(
    "asset_subscriptions",
    metadata,
    Column("subscription_id", String(64), primary_key=True),
    Column("user_id", String(64), nullable=False),
    # kind | industry | tag | owner | asset（盯某一份资产的更新）
    Column("filter_type", String(16), nullable=False),
    Column("filter_value", String(128), nullable=False),
    Column("created_at", DateTime(timezone=True), nullable=False, default=_now),
)

#: 站内通知：变更通知与订阅命中都落这里，由 fde-server 决定要不要再推 IM
asset_notifications = Table(
    "asset_notifications",
    metadata,
    Column("notification_id", String(64), primary_key=True),
    Column("user_id", String(64), nullable=False),
    # asset_changed | asset_new | recommendation | review
    Column("kind", String(24), nullable=False),
    Column("asset_id", String(64), nullable=False, default=""),
    Column("title", Text, nullable=False, default=""),
    Column("body", Text, nullable=False, default=""),
    Column("reason", Text, nullable=False, default=""),
    Column("read_at", DateTime(timezone=True)),
    Column("created_at", DateTime(timezone=True), nullable=False, default=_now),
)

#: 资产版本快照：用来算"改了什么"，变更通知靠它出 diff 摘要
asset_versions = Table(
    "asset_versions",
    metadata,
    Column("id", Integer, primary_key=True, autoincrement=True),
    Column("asset_id", String(64), nullable=False),
    Column("version", String(32), nullable=False, default=""),
    Column("commit_sha", String(64), nullable=False, default=""),
    Column("summary", Text, nullable=False, default=""),
    Column("sections_json", Text, nullable=False, default="{}"),
    Column("created_at", DateTime(timezone=True), nullable=False, default=_now),
    UniqueConstraint("asset_id", "commit_sha", name="uq_asset_version"),
)

#: 系统配置：大小上限、重排开关等，改完即生效，不用重启
system_settings = Table(
    "system_settings",
    metadata,
    Column("key", String(64), primary_key=True),
    Column("value", Text, nullable=False, default=""),
    Column("updated_by", String(64), nullable=False, default=""),
    Column("updated_at", DateTime(timezone=True), nullable=False, default=_now),
)

work_item_assets = Table(
    "work_item_assets",
    metadata,
    Column("id", Integer, primary_key=True, autoincrement=True),
    Column("engagement_slug", String(64), nullable=False),
    Column("work_item_id", String(64), nullable=False),
    Column("asset_id", String(64), nullable=False),
    Column("role", String(16), nullable=False, default="reference"),  # sop_step | reference
    Column("sop_step", Integer),
    Column("locked", Boolean, nullable=False, default=False),
    Column("created_by", String(64), nullable=False, default=""),
    Column("created_at", DateTime(timezone=True), nullable=False, default=_now),
    UniqueConstraint("work_item_id", "asset_id", "role", name="uq_work_item_asset"),
)


def create_engine_for(db_path: Path | str, echo: bool = False) -> Engine:
    """给一个 PostgreSQL 连接串就连 PostgreSQL，给一个路径就用那个 SQLite 文件。"""
    if str(db_path).startswith("postgresql"):
        # 代码用的是同步连接，驱动固定用 psycopg；连接串里没写驱动或写了别的都按它来
        url = make_url(str(db_path)).set(drivername="postgresql+psycopg")
        # 连接放久了可能被数据库或中间网络断掉，取用前先探一下
        return create_engine(url, echo=echo, future=True, pool_pre_ping=True)
    path = Path(db_path).expanduser()
    path.parent.mkdir(parents=True, exist_ok=True)
    return create_engine(f"sqlite+pysqlite:///{path}", echo=echo, future=True)


def upsert(conn: Any, table: Table, values: dict[str, Any], key: list[str]) -> None:
    """有这一行就更新，没有就插入。`key` 是用来判断「有没有」的列，要有唯一约束或是主键。"""
    from sqlalchemy.dialects.postgresql import insert as postgresql_insert
    from sqlalchemy.dialects.sqlite import insert as sqlite_insert

    insert = postgresql_insert if conn.dialect.name == "postgresql" else sqlite_insert
    statement = insert(table).values(**values)
    conn.execute(
        statement.on_conflict_do_update(
            index_elements=[table.c[name] for name in key],
            set_={name: value for name, value in values.items() if name not in key},
        )
    )


def clean_text(value: str) -> str:
    """去掉文字里的空字符。

    从 PDF、Office 文件里抽出来的正文偶尔带着它，PostgreSQL 的文本列不收，整条写入会失败。
    """
    return value.replace("\x00", "") if "\x00" in value else value


def init_db(engine: Engine) -> None:
    metadata.create_all(engine)
    add_missing_columns(engine)
    drop_legacy_asset_constraint(engine)


#: 建索引、重算等级、重建关系共用的 PostgreSQL 事务锁编号（随便取的固定值，"FDEAIDX"）
INDEX_LOCK_KEY = 0x46444541494458


def lock_index(conn) -> None:
    """在当前事务里拿索引锁，事务结束自动释放。

    评审通过、调整可见范围的请求会就地重建索引，asset-worker 发现仓库有新提交也会重建；
    两边同时改 assets、asset_relations 的同一批行，顺序不同就会死锁。拿同一把锁让它们排队。
    SQLite 本来就只有一个写者，不需要。
    """
    if conn.dialect.name == "postgresql":
        conn.exec_driver_sql(f"SELECT pg_advisory_xact_lock({INDEX_LOCK_KEY})")


def drop_legacy_asset_constraint(engine: Engine) -> bool:
    """旧库的资产表带着「同一层级内同类型不重名」的唯一约束，去掉它。返回这次有没有动。

    现在的规则是同一个负责人名下不重名，不同负责人的同名资产可以在同一层；
    留着旧约束，这种资产一入库建索引就会整个失败。SQLite 去不掉表上的约束，只能重建表。
    """
    if engine.dialect.name != "sqlite":
        return False
    with engine.begin() as conn:
        ddl = conn.exec_driver_sql(
            "SELECT sql FROM sqlite_master WHERE type = 'table' AND name = 'assets'"
        ).scalar()
        if not ddl or "uq_assets_scope_kind_name" not in ddl:
            return False
        columns = ", ".join(f'"{column.name}"' for column in assets.columns)
        conn.exec_driver_sql("ALTER TABLE assets RENAME TO assets_legacy")
        conn.exec_driver_sql("DROP INDEX IF EXISTS ix_assets_owner_kind_name")
        assets.create(conn)
        conn.exec_driver_sql(f"INSERT INTO assets ({columns}) SELECT {columns} FROM assets_legacy")
        conn.exec_driver_sql("DROP TABLE assets_legacy")
    return True


def _default_literal(engine: Engine, column: Column) -> str | None:
    default = column.default
    if default is None or not default.is_scalar:
        return None
    compiled = literal(default.arg, column.type).compile(
        dialect=engine.dialect, compile_kwargs={"literal_binds": True}
    )
    return str(compiled)


def add_missing_columns(engine: Engine) -> list[str]:
    """给旧库补上代码里新增的列，返回补了哪些（`表.列`）。

    `create_all` 只建缺失的表，不会给已有的表加列；本地库和演示库不走 alembic，
    代码加了列以后旧库一启动就报 no such column。由 alembic 管理的库不在这里动，
    否则迁移脚本再加同一列会撞车。
    """
    inspector = inspect(engine)
    if inspector.has_table("alembic_version"):
        return []
    quote = engine.dialect.identifier_preparer.quote
    added: list[str] = []
    with engine.begin() as conn:
        for table in metadata.sorted_tables:
            existing = {item["name"] for item in inspector.get_columns(table.name)}
            for column in table.columns:
                if column.name in existing:
                    continue
                ddl = f"{quote(column.name)} {column.type.compile(dialect=engine.dialect)}"
                if not column.nullable:
                    default = _default_literal(engine, column)
                    if column.primary_key or default is None:
                        raise RuntimeError(
                            f"旧库缺少列 {table.name}.{column.name}，它非空且没有固定默认值，"
                            "无法自动补齐；请写迁移脚本或重建数据目录"
                        )
                    ddl += f" NOT NULL DEFAULT {default}"
                conn.exec_driver_sql(f"ALTER TABLE {quote(table.name)} ADD COLUMN {ddl}")
                added.append(f"{table.name}.{column.name}")
    return added


def record_event(conn: Any, event_type: str, payload: dict[str, Any]) -> None:
    import json

    conn.execute(
        asset_events.insert().values(
            type=event_type, payload_json=json.dumps(payload, ensure_ascii=False)
        )
    )
