import pytest

from hub.domain.models import (
    AiResult, AiStatus, Classification, Post, RunStatus, ScrapedPost, Trigger,
)
from hub.storage.db import Database
from hub.storage.groups import DuplicateGroup, GroupRepository
from hub.storage.posts import PostRepository
from hub.storage.runs import RunRepository

URL = "https://www.facebook.com/groups/BrasileirosEmSydneyAustralia/"


@pytest.fixture
def db(tmp_path):
    return Database(tmp_path / "hub.db")


def make_post(post_id="p1", kind="stay", score=70, ts=1000.0, body="Room Rosebery $300 pw"):
    scraped = ScrapedPost(
        post_id=post_id, group_url=URL, permalink=f"{URL}posts/{post_id}/", body=body,
        created_ts=ts, author="Ana", photos=["http://img/1.jpg"], comment_count=2, reactions=5,
    )
    cls = Classification(kind=kind, price_week=300, suburb="Rosebery", score=score)
    return Post(scraped=scraped, classification=cls, group_id=1, first_seen_at=ts)


def test_group_add_list_and_duplicate(db):
    repo = GroupRepository(db)
    g = repo.add(URL, now=10.0)
    assert g.id and g.enabled and g.url == URL
    assert [x.url for x in repo.list()] == [URL]
    with pytest.raises(DuplicateGroup):
        repo.add(URL, now=11.0)


def test_group_toggle_remove_and_run_result(db):
    repo = GroupRepository(db)
    g = repo.add(URL, now=10.0)
    repo.set_enabled(g.id, False)
    assert repo.get(g.id).enabled is False
    assert repo.list(only_enabled=True) == []
    repo.record_run_result(g.id, ts=20.0, status="ok", new_posts=4, name="BRASILEIROS EM SYDNEY", fb_group_id="1434")
    got = repo.get(g.id)
    assert (got.last_status, got.last_new_posts, got.name, got.fb_group_id) == ("ok", 4, "BRASILEIROS EM SYDNEY", "1434")
    assert repo.remove(g.id) is True
    assert repo.get(g.id) is None


def test_post_dedup_and_roundtrip(db):
    repo = PostRepository(db)
    repo.add(make_post("p1"))
    repo.add(make_post("p2"))
    assert repo.known_ids(["p1", "p2", "p3"]) == {"p1", "p2"}
    repo.add(make_post("p1"))  # re-adding is a no-op
    assert len(repo.list()) == 2
    stored = repo.get("p1")
    assert stored.scraped.photos == ["http://img/1.jpg"]
    assert stored.classification.suburb == "Rosebery"
    assert stored.ai_status == AiStatus.PENDING


def test_post_filters_new_and_mark_seen(db):
    repo = PostRepository(db)
    repo.add(make_post("p1", kind="stay"))
    repo.add(make_post("p2", kind="job"))
    assert [p.scraped.post_id for p in repo.list(kind="job")] == ["p2"]
    assert repo.count_new() == 2
    repo.mark_all_seen()
    assert repo.count_new() == 0
    assert repo.list(only_new=True) == []


def test_post_ai_queue_and_user_status(db):
    repo = PostRepository(db)
    repo.add(make_post("p1"))
    assert repo.count_pending_ai() == 1
    assert [p.scraped.post_id for p in repo.pending_ai(limit=10)] == ["p1"]
    repo.save_ai("p1", AiResult(kind="stay", fit_score=88, scam_suspected=False, summary="Good fit"))
    assert repo.count_pending_ai() == 0
    got = repo.get("p1")
    assert got.ai_status == AiStatus.DONE and got.ai.fit_score == 88
    repo.set_user_status("p1", "fav")
    assert repo.get("p1").user_status == "fav"


def test_post_list_orders_newest_first_and_skips_other_by_default(db):
    repo = PostRepository(db)
    repo.add(make_post("old", ts=100.0))
    repo.add(make_post("new", ts=200.0))
    repo.add(make_post("noise", kind="other", ts=300.0))
    assert [p.scraped.post_id for p in repo.list()] == ["new", "old"]


def test_run_lifecycle(db):
    repo = RunRepository(db)
    run = repo.start(Trigger.MANUAL, now=50.0)
    assert run.id and run.status == RunStatus.RUNNING
    run.status, run.finished_at, run.new_posts, run.groups_ok = RunStatus.OK, 60.0, 3, 2
    repo.finish(run)
    latest = repo.latest()
    assert latest.status == RunStatus.OK and latest.new_posts == 3 and latest.groups_ok == 2
    assert repo.last_finished_at(statuses=[RunStatus.OK, RunStatus.PARTIAL]) == 60.0


def test_last_finished_at_is_none_without_runs(db):
    assert RunRepository(db).last_finished_at(statuses=[RunStatus.OK]) is None
    assert RunRepository(db).latest() is None
def test_catalog_migration_repairs_legacy_hunt_without_losing_raw_data(tmp_path):
    import json
    import sqlite3
    from contextlib import closing

    from hub.storage.db import SCHEMA, restore_catalog_backup

    path = tmp_path / "hub.db"
    original_classification = json.dumps({"kind": "job", "suburb": "Rockdale", "score": 90, "is_whv_confirmed": True})
    original_ai = json.dumps({"title": "Assumed role", "pay_display": "$30-$35/h", "whv_eligible": True})
    with closing(sqlite3.connect(path)) as conn:
        conn.executescript(SCHEMA)
        conn.execute(
            "INSERT INTO posts(post_id,group_url,permalink,body,created_ts,first_seen_at,kind,classification,"
            "score,ai_status,ai_json) VALUES (?,?,?,?,?,?,?,?,?,?,?)",
            ("job_local_1", "https://venue.example/careers", "https://venue.example/careers", "Original raw body",
             1000, 1000, "job", original_classification, 90, "approved", original_ai),
        )
        conn.commit()

    db = Database(path)
    post = PostRepository(db).get("job_local_1")
    assert post.scraped.body == "Original raw body"
    assert post.ai_status.value == "skipped"
    assert post.classification.suburb == "Rockdale"
    assert post.classification.score == 0
    with db.connect() as conn:
        repaired = conn.execute(
            "SELECT ai_status,ai_json,legacy_ai_json,legacy_classification_json FROM posts WHERE post_id='job_local_1'"
        ).fetchone()
        tables = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    assert {"employers", "opportunities", "opportunity_evidence"} <= tables
    assert tuple(repaired) == ("skipped", None, original_ai, original_classification)
    backups = list(tmp_path.glob("hub.db.pre-catalog-*.db"))
    assert len(backups) == 1
    with closing(sqlite3.connect(backups[0])) as conn:
        assert conn.execute("SELECT ai_status,ai_json FROM posts").fetchone() == ("approved", original_ai)
    Database(path)
    assert list(tmp_path.glob("hub.db.pre-catalog-*.db")) == backups

    restore_catalog_backup(path)
    with closing(sqlite3.connect(path)) as conn:
        assert conn.execute("SELECT ai_status,ai_json,classification,body FROM posts").fetchone() == (
            "approved", original_ai, original_classification, "Original raw body"
        )


def test_catalog_rollback_refuses_to_erase_newer_writes(tmp_path):
    import sqlite3
    from contextlib import closing

    import pytest

    from hub.storage.db import SCHEMA, restore_catalog_backup

    path = tmp_path / "hub.db"
    with closing(sqlite3.connect(path)) as conn:
        conn.executescript(SCHEMA)
    db = Database(path)
    with db.connect() as conn:
        conn.execute("INSERT INTO groups(url) VALUES ('https://www.facebook.com/groups/new/')")
    with pytest.raises(ValueError, match="changed since migration"):
        restore_catalog_backup(path)

    preserved = restore_catalog_backup(path, force=True)
    assert preserved is not None and preserved.is_file()
    with closing(sqlite3.connect(preserved)) as conn:
        assert conn.execute('SELECT COUNT(*) FROM groups').fetchone()[0] == 1
    with closing(sqlite3.connect(path)) as conn:
        assert conn.execute('PRAGMA user_version').fetchone()[0] == 0


def test_legacy_approved_hunt_cannot_reintroduce_fabricated_claims(tmp_path):
    import sqlite3
    from hub.storage.db import Database

    db = Database(tmp_path / 'hub.db')
    with pytest.raises(sqlite3.IntegrityError, match='approved legacy'):
        with db.connect() as conn:
            conn.execute("INSERT INTO posts(post_id,group_url,permalink,body,created_ts,first_seen_at,kind,classification,ai_status) VALUES (?,?,?,?,?,?,?,?,?)", (
                'unsafe', 'https://example.com', 'https://example.com', 'Claimed salary', 1, 1,
                'job', '{"kind":"job","score":0}', 'approved',
            ))


def test_evidence_requires_existing_opportunity(tmp_path):
    import sqlite3

    db = Database(tmp_path / 'hub.db')
    with pytest.raises(sqlite3.IntegrityError, match='FOREIGN KEY'):
        with db.connect() as conn:
            conn.execute("INSERT INTO opportunity_evidence(opportunity_id,source_url,source_type,observed_at) VALUES (?,?,?,?)", (
                'missing', 'https://example.com', 'test', 1,
            ))


def test_rollback_holds_exclusive_sqlite_lock_during_check(tmp_path, monkeypatch):
    import sqlite3
    from contextlib import closing
    import hub.storage.db as storage_db

    path = tmp_path / 'hub.db'
    with closing(sqlite3.connect(path)) as conn:
        conn.executescript(storage_db.SCHEMA)
    db = Database(path)
    with db.connect() as conn:
        conn.execute("INSERT INTO groups(url) VALUES ('https://example.com/one')")
    original_digest = storage_db._digest
    blocked = []

    def digest_with_concurrent_writer(target):
        if target == path:
            with closing(sqlite3.connect(path, timeout=0)) as writer:
                try:
                    writer.execute("INSERT INTO groups(url) VALUES ('https://example.com/two')")
                    writer.commit()
                except sqlite3.OperationalError as exc:
                    blocked.append('locked' in str(exc))
        return original_digest(target)

    monkeypatch.setattr(storage_db, '_digest', digest_with_concurrent_writer)
    storage_db.restore_catalog_backup(path, force=True)
    assert blocked == [True]


def test_version_one_upgrade_has_its_own_backup_and_rollback(tmp_path):
    import sqlite3
    from contextlib import closing
    from hub.storage.db import restore_catalog_backup

    path = tmp_path / 'hub.db'
    db = Database(path)
    with db.connect() as conn:
        conn.execute("INSERT INTO employers(id,name) VALUES ('cafe','Cafe')")
        conn.execute("INSERT INTO opportunities(id,employer_id,source,source_url,verification_state,first_seen_at) VALUES (?,?,?,?,?,?)", (
            'job-1', 'cafe', 'Facebook Group', 'https://example.com/post/1', 'unverified_job_post', 1,
        ))
        conn.execute('ALTER TABLE opportunities DROP COLUMN source_group_name')
        conn.execute('ALTER TABLE opportunities DROP COLUMN source_group_url')
        conn.execute('ALTER TABLE opportunity_evidence DROP COLUMN checked_at')
        conn.execute('PRAGMA user_version=1')

    Database(path)
    assert (tmp_path / 'hub.db.catalog-v2-rollback.json').is_file()
    with closing(sqlite3.connect(path)) as conn:
        assert conn.execute('PRAGMA user_version').fetchone()[0] == 2
        assert 'source_group_name' in {row[1] for row in conn.execute('PRAGMA table_info(opportunities)')}

    restore_catalog_backup(path, target_version=1)
    with closing(sqlite3.connect(path)) as conn:
        assert conn.execute('PRAGMA user_version').fetchone()[0] == 1
        assert 'source_group_name' not in {row[1] for row in conn.execute('PRAGMA table_info(opportunities)')}
        assert conn.execute("SELECT id FROM opportunities").fetchone()[0] == 'job-1'


def test_pilot_snapshot_restores_and_guards_new_writes(tmp_path):
    from hub.storage.db import snapshot_catalog_for_pilot, restore_catalog_backup

    path = tmp_path / 'hub.db'
    db = Database(path)
    with db.connect() as conn:
        conn.execute("INSERT INTO employers(id,name) VALUES ('before','Before')")
    backup = snapshot_catalog_for_pilot(path)
    assert backup.is_file()
    with db.connect() as conn:
        conn.execute("INSERT INTO employers(id,name) VALUES ('after','After')")
    with pytest.raises(ValueError, match='changed since migration'):
        restore_catalog_backup(path, target_version=2)
    safety = restore_catalog_backup(path, target_version=2, force=True)
    assert safety is not None and safety.is_file()
    with db.connect() as conn:
        assert [row['id'] for row in conn.execute('SELECT id FROM employers')] == ['before']
        assert conn.execute('PRAGMA integrity_check').fetchone()[0] == 'ok'


def test_pilot_rollback_cli_restores_selected_database(tmp_path):
    import subprocess
    import sys
    from pathlib import Path
    from hub.storage.db import snapshot_catalog_for_pilot

    path = tmp_path / 'hub.db'
    db = Database(path)
    snapshot_catalog_for_pilot(path)
    with db.connect() as conn:
        conn.execute("INSERT INTO employers(id,name) VALUES ('new','New')")
    script = Path(__file__).resolve().parents[1] / 'rollback_catalog.py'
    result = subprocess.run([sys.executable, str(script), '--target-version', '2', '--database', str(path), '--force'],
                            capture_output=True, text=True)
    assert result.returncode == 0, result.stderr
    with db.connect() as conn:
        assert conn.execute('SELECT COUNT(*) FROM employers').fetchone()[0] == 0
