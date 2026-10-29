"""新迁移必须能在空库与既有 0015 库写下完整 revision，保留既有资产。"""

from pathlib import Path

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import create_engine, inspect, text

from tests.conftest import temporary_database


@pytest.mark.parametrize("existing_0015", [False, True])
def test_harvest_migration_accepts_the_full_revision_and_preserves_assets(
    monkeypatch, existing_0015
):
    root = Path(__file__).resolve().parents[1]
    config = Config(str(root / "alembic.ini"))
    config.set_main_option("script_location", str(root / "migrations"))
    with temporary_database() as database_url:
        monkeypatch.setenv("FDE_ASSET_DATABASE_URL", database_url)
        engine = create_engine(database_url)
        try:
            if existing_0015:
                command.upgrade(config, "0015_attachment_texts")
                with engine.begin() as conn:
                    # 0001 使用当前 metadata；去掉未来的表并还原既有版本表宽度来模拟旧库。
                    conn.exec_driver_sql("DROP TABLE harvest_suggestions")
                    conn.exec_driver_sql(
                        "ALTER TABLE alembic_version ALTER COLUMN version_num TYPE VARCHAR(32)"
                    )
                    conn.exec_driver_sql("""INSERT INTO assets
                        (asset_id,scope,repo,path,kind,name,title,summary,conclusion_md,
                        applicability_json,tags_json,industry_json,owner_ref,owner_kind,
                        owner_value,lifecycle,quality,nature,version,replaced_by,source_json,
                        kind_spec_json,attachments_json,content_text,commit_sha,tree_sha,
                        visibility_json,restricted,valid,created_at,updated_at,customer_code)
                        VALUES ('existing','company','assets','assets/existing','Case','existing',
                        'existing','','','{}','[]','[]','','','','experimental','bronze','',
                        '1.0.0','','{}','{}','[]','','','','[]',false,true,now(),now(),'')""")
            command.upgrade(config, "head")
            command.upgrade(config, "head")
            with engine.connect() as conn:
                assert (
                    conn.execute(text("SELECT version_num FROM alembic_version")).scalar_one()
                    == "0017_harvest_suggestion_decisions"
                )
                columns = {
                    column["name"] for column in inspect(conn).get_columns("harvest_suggestions")
                }
                assert {"mentions", "decided_at", "related_asset_id", "merged_into"} <= columns
                if existing_0015:
                    assert (
                        conn.execute(
                            text("SELECT title FROM assets WHERE asset_id='existing'")
                        ).scalar_one()
                        == "existing"
                    )
        finally:
            engine.dispose()
