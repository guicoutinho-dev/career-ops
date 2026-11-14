"""Serve the built UI with an isolated local API for browser checks."""
import sys
import tempfile
import json
from pathlib import Path

import uvicorn

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from hub.api.app import AppDeps, create_app
from hub.storage.db import Database
from hub.storage.catalog import CatalogRepository
from hub.storage.groups import GroupRepository
from hub.storage.posts import PostRepository
from hub.storage.runs import RunRepository
from web.build_dashboard import build as build_dashboard
from tests.test_api import FakeRunner, FakeScheduler


if __name__ == "__main__":
    with tempfile.TemporaryDirectory() as temp:
        db = Database(Path(temp) / "hub.db")
        catalog = CatalogRepository(db)
        seed = Path(temp) / 'jobs.json'
        seed.write_text(json.dumps([
            {'id': 'legacy-barista', 'title': 'Barista', 'company': 'Cafe Fixture', 'source': 'Seek',
             'url': 'https://example.com/jobs/barista', 'suburb': 'Rockdale', 'location': 'Rockdale, NSW', 'state': 'NSW',
             'pay_display': '$99/h', 'whv_eligible': True},
            {'id': 'legacy-lead', 'title': 'Cleaner', 'company': 'Hotel Fixture', 'source': 'Direct Employer',
             'url': 'https://example.com/hotel', 'suburb': 'Mascot', 'location': 'Mascot, NSW', 'state': 'NSW'},
        ]), encoding='utf-8')
        catalog.import_legacy_seed(seed)
        catalog.sync_posts()
        web_dir = Path(__file__).resolve().parents[1] / 'web'
        build_dashboard(web_dir)
        deps = AppDeps(
            groups=GroupRepository(db), posts=PostRepository(db), runs=RunRepository(db),
            catalog=catalog,
            runner=FakeRunner(), scheduler=FakeScheduler(),
            web_dir=web_dir,
            allowed_hosts={"127.0.0.1:8767", "localhost:8767"},
        )
        uvicorn.run(create_app(deps), host="127.0.0.1", port=8767, log_level="warning")
