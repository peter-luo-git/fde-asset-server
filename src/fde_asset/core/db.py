"""数据层：SQLAlchemy Core 表定义与连接管理（PostgreSQL）。"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from sqlalchemy import (
    BigInteger,
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
    # 修订的是哪份已入库资产：入库时写回那份资产（编号不变、版本加一），不另起一份新资产
    Column("revises_asset_id", String(64), nullable=False, default=""),
    # 起草时除了归属作用域，还想让谁看见（company、department:x、user:x……）。
    # 评审人有权放的入库时直接生效，没权放的入库后转成可见范围申请
    Column("visibility_json", Text, nullable=False, default="[]"),
)

#: 可见范围申请：资产负责人想把资产放到自己没有评审权的那一层时，由那一层的评审人审批
#: 定时体检的结果（B10）：同一份资产、同一条规则、同一个对象只有一行；问题消失就记下解决时间，
#: 再出现算新问题。新出现的才发通知
asset_checkup_findings = Table(
    "asset_checkup_findings",
    metadata,
    Column("id", Integer, primary_key=True, autoincrement=True),
    Column("asset_id", String(64), nullable=False),
    Column("rule", String(32), nullable=False),
    #: 规则针对的对象：失效的引用、点名的人；其余规则为空
    Column("subject", String(200), nullable=False, default=""),
    Column("owner_ref", String(128), nullable=False, default=""),
    Column("detail", Text, nullable=False, default=""),
    Column("first_seen_at", DateTime(timezone=True), nullable=False, default=_now),
    Column("last_seen_at", DateTime(timezone=True), nullable=False, default=_now),
    Column("resolved_at", DateTime(timezone=True)),
    Column("notified_at", DateTime(timezone=True)),
    UniqueConstraint("asset_id", "rule", "subject", name="uq_checkup_finding"),
)

#: 可见范围里点名的人，上一次体检时和这份资产还有没有关系（B5）。
#: 有关系 → 没关系，说明这个人换了项目或部门，提醒负责人看要不要收回；一开始就没关系的
#: （有意点名给外部门的人）不提醒
asset_named_audience = Table(
    "asset_named_audience",
    metadata,
    Column("asset_id", String(64), primary_key=True),
    Column("user_id", String(64), primary_key=True),
    Column("related", Boolean, nullable=False, default=False),
    Column("checked_at", DateTime(timezone=True), nullable=False, default=_now),
)

visibility_requests = Table(
    "visibility_requests",
    metadata,
    Column("request_id", String(64), primary_key=True),
    Column("asset_id", String(64), nullable=False),
    # 申请加上的那一项：company、department:x、customer:x、engagement:x
    Column("entry", String(80), nullable=False),
    Column("requested_by", String(64), nullable=False),
    Column("note", Text, nullable=False, default=""),
    Column(
        "status", String(16), nullable=False, default="open"
    ),  # open|approved|rejected|withdrawn
    Column("decided_by", String(64), nullable=False, default=""),
    Column("decision_note", Text, nullable=False, default=""),
    Column("created_at", DateTime(timezone=True), nullable=False, default=_now),
    Column("decided_at", DateTime(timezone=True)),
    Index("ix_visibility_requests_asset", "asset_id", "status"),
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
    # sop_step：按这条流程执行；sop_none：明确不按流程做（项目设了默认流程也不再自动绑）；
    # reference：参考资产（SOP 不能当参考）
    Column("role", String(16), nullable=False, default="reference"),
    Column("sop_step", Integer),
    Column("locked", Boolean, nullable=False, default=False),
    Column("created_by", String(64), nullable=False, default=""),
    Column("created_at", DateTime(timezone=True), nullable=False, default=_now),
    # 绑定时的流程版本，以及绑定时冻结下来的合并后步骤和每个检查点的状态（见 sop_progress）
    Column("sop_version", String(32), nullable=False, default=""),
    Column("progress_json", Text, nullable=False, default="{}"),
    Column("updated_at", DateTime(timezone=True)),
    UniqueConstraint("work_item_id", "asset_id", "role", name="uq_work_item_asset"),
)

#: 技能包的两类检查结果，按内容摘要存：试跑（fde-server worker 在 Agent 同款镜像里断网跑）、模型通读
skill_deps_status = Table(
    # 技能依赖在 Agent 机器上的安装状态（fde-server worker 回报）：键和 fde-server 的算法一致
    "skill_deps_status",
    metadata,
    Column("key", String(64), primary_key=True),
    Column("requirements_json", Text, nullable=False, default="[]"),
    # ready | failed | installing | missing
    Column("state", String(16), nullable=False, default="missing"),
    Column("bytes", BigInteger, nullable=False, default=0),
    Column("reason", Text, nullable=False, default=""),
    Column("updated_at", DateTime(timezone=True), nullable=False, default=_now),
)

skill_checks = Table(
    "skill_checks",
    metadata,
    Column("id", Integer, primary_key=True, autoincrement=True),
    Column("digest", String(64), nullable=False),
    # trial | model
    Column("kind", String(16), nullable=False),
    # pending | running | passed | failed | unavailable | done
    Column("status", String(16), nullable=False, default="pending"),
    Column("candidate_id", String(32), nullable=False, default=""),
    Column("payload_json", Text, nullable=False, default="{}"),
    Column("result_json", Text, nullable=False, default="{}"),
    Column("claimed_at", DateTime(timezone=True)),
    Column("created_at", DateTime(timezone=True), nullable=False, default=_now),
    Column("updated_at", DateTime(timezone=True)),
    UniqueConstraint("digest", "kind", name="uq_skill_check"),
)

#: 附件的图片理解结果（2026-10-28）：按附件内容的 SHA-256 存，同一份文件只做一次。
#: text 是带图片说明的文字版，report_json 记下看了哪些、跳过哪些和理由。
attachment_texts = Table(
    "attachment_texts",
    metadata,
    Column("sha256", String(64), primary_key=True),
    Column("filename", String(255), nullable=False, default=""),
    # pending | running | done | failed
    Column("status", String(16), nullable=False, default="pending"),
    Column("done", Integer, nullable=False, default=0),
    Column("total", Integer, nullable=False, default=0),
    Column("text", Text, nullable=False, default=""),
    Column("report_json", Text, nullable=False, default="{}"),
    Column("include_json", Text, nullable=False, default="[]"),
    Column("error", Text, nullable=False, default=""),
    Column("claimed_at", DateTime(timezone=True)),
    Column("created_at", DateTime(timezone=True), nullable=False, default=_now),
    Column("updated_at", DateTime(timezone=True)),
)

#: 公开技能库：管理员配的来源；目录清单缓存在 catalog_json
skill_sources = Table(
    "skill_sources",
    metadata,
    Column("id", Integer, primary_key=True, autoincrement=True),
    Column("name", String(100), nullable=False),
    Column("url", String(500), nullable=False),
    Column("branch", String(100), nullable=False, default=""),
    Column("enabled", Boolean, nullable=False, default=True),
    Column("status", String(16), nullable=False, default=""),
    Column("error", Text, nullable=False, default=""),
    Column("commit_sha", String(64), nullable=False, default=""),
    Column("catalog_json", Text, nullable=False, default="[]"),
    Column("checked_at", DateTime(timezone=True)),
    Column("created_by", String(64), nullable=False, default=""),
    Column("created_at", DateTime(timezone=True), nullable=False, default=_now),
    UniqueConstraint("url", name="uq_skill_source_url"),
)

#: 默认流程：在这个范围里新建的任务自动按这条流程执行（每个范围一条）
sop_defaults = Table(
    "sop_defaults",
    metadata,
    Column("id", Integer, primary_key=True, autoincrement=True),
    # company | department | customer | engagement
    Column("scope", String(16), nullable=False),
    Column("code", String(64), nullable=False, default=""),
    Column("asset_id", String(64), nullable=False),
    Column("created_by", String(64), nullable=False, default=""),
    Column("created_at", DateTime(timezone=True), nullable=False, default=_now),
    UniqueConstraint("scope", "code", name="uq_sop_default_scope"),
)


def create_engine_for(database_url: str, echo: bool = False) -> Engine:
    """连 PostgreSQL。2026-10-18 起不再支持 SQLite。"""
    if not str(database_url).startswith("postgresql"):
        raise ValueError("资产中心只用 PostgreSQL：连接串要以 postgresql:// 开头")
    # 代码用的是同步连接，驱动固定用 psycopg；连接串里没写驱动或写了别的都按它来
    url = make_url(str(database_url)).set(drivername="postgresql+psycopg")
    # 连接放久了可能被数据库或中间网络断掉，取用前先探一下
    return create_engine(url, echo=echo, future=True, pool_pre_ping=True)


def upsert(conn: Any, table: Table, values: dict[str, Any], key: list[str]) -> None:
    """有这一行就更新，没有就插入。`key` 是用来判断「有没有」的列，要有唯一约束或是主键。"""
    from sqlalchemy.dialects.postgresql import insert

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


#: 建索引、重算等级、重建关系共用的 PostgreSQL 事务锁编号（随便取的固定值，"FDEAIDX"）
INDEX_LOCK_KEY = 0x46444541494458


def lock_index(conn) -> None:
    """在当前事务里拿索引锁，事务结束自动释放。

    评审通过、调整可见范围的请求会就地重建索引，asset-worker 发现仓库有新提交也会重建；
    两边同时改 assets、asset_relations 的同一批行，顺序不同就会死锁。拿同一把锁让它们排队。
    """
    conn.exec_driver_sql(f"SELECT pg_advisory_xact_lock({INDEX_LOCK_KEY})")


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
