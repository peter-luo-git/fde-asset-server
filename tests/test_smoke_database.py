import pytest

from scripts import smoke_database


@pytest.fixture()
def ownership(monkeypatch, tmp_path):
    prefix = "asset_smoke_" + "a" * 32 + "_"
    monkeypatch.setenv("FDE_ASSET_TEST_DATABASE_PREFIX", prefix)
    monkeypatch.setenv("FDE_ASSET_TEST_DATABASE_MANIFEST", str(tmp_path))
    return prefix, tmp_path


def test_fixture_registers_before_create_and_clears_only_after_drop(monkeypatch, ownership):
    from tests import conftest
    import sqlalchemy

    prefix, directory = ownership
    events = []

    class Connection:
        def __enter__(self):
            return self

        def __exit__(self, *args):
            pass

        def exec_driver_sql(self, command):
            markers = list(directory.iterdir())
            assert len(markers) == 1
            assert markers[0].name.startswith(prefix)
            assert markers[0].name in command
            events.append(command.split()[0])

    class Engine:
        def connect(self):
            return Connection()

        def dispose(self):
            events.append("dispose")

    monkeypatch.setattr(sqlalchemy, "create_engine", lambda *args, **kwargs: Engine())
    with conftest.temporary_database() as url:
        assert prefix in url
        assert len(list(directory.iterdir())) == 1
    assert events == ["CREATE", "DROP", "dispose"]
    assert not list(directory.iterdir())


def test_cleanup_refuses_foreign_manifest_entry_before_any_database_connection(
    monkeypatch,
    ownership,
):
    import psycopg

    prefix, directory = ownership
    (directory / (prefix + "b" * 12)).touch()
    (directory / "asset_test_foreign").touch()

    def forbidden(*args, **kwargs):
        pytest.fail("foreign manifest must be rejected before connecting")

    monkeypatch.setattr(psycopg, "connect", forbidden)
    with pytest.raises(ValueError, match="foreign entry"):
        smoke_database.cleanup()
    assert len(list(directory.iterdir())) == 2


def test_missing_half_of_ownership_cannot_create_untracked_database(monkeypatch, tmp_path):
    monkeypatch.delenv("FDE_ASSET_TEST_DATABASE_PREFIX", raising=False)
    monkeypatch.setenv("FDE_ASSET_TEST_DATABASE_MANIFEST", str(tmp_path))
    with pytest.raises(ValueError, match="ownership"):
        smoke_database.register_database()


def test_cleanup_rejects_symlink_manifest_entry(ownership):
    prefix, directory = ownership
    target = directory / "target"
    target.touch()
    (directory / (prefix + "b" * 12)).symlink_to(target)
    with pytest.raises(ValueError, match="foreign entry"):
        smoke_database.cleanup()
