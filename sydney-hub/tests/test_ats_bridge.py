import http.server
import json
import sys
import threading
import time
from pathlib import Path

import pytest

from hub.discovery.ats_bridge import AtsBridge, AtsFetchResult, AtsJob, AtsMatch

REPO_PROVIDERS = Path(__file__).resolve().parents[2] / "providers"


def test_detect_identifies_supported_ats_and_flags_unsupported_without_network():
    bridge = AtsBridge(REPO_PROVIDERS)
    urls = [
        "https://boards.greenhouse.io/acmecafe",
        "https://careers.pageuppeople.com/999/cw/en/listing/",
        "https://jobs.dayforcehcm.com/en-US/brand/CANDIDATEPORTAL",
        "https://app.employmenthero.com/en/jobs/1",
        "https://evil.example/local-parser",
    ]
    matches = bridge.detect(urls, company="Acme Cafe")
    by_url = {m.url: m for m in matches}

    assert by_url[urls[0]].supported is True and by_url[urls[0]].provider_id == "greenhouse"
    assert by_url[urls[1]].supported is True and by_url[urls[1]].provider_id == "pageup"
    assert by_url[urls[2]].supported is False and by_url[urls[2]].unsupported_vendor == "dayforce"
    assert by_url[urls[3]].supported is False and by_url[urls[3]].unsupported_vendor == "employmenthero"
    assert by_url[urls[4]].supported is False and by_url[urls[4]].provider_id is None


def test_bridge_blocks_loopback_ssrf_with_real_node_http_ctx(tmp_path):
    hits = 0

    class Handler(http.server.BaseHTTPRequestHandler):
        def do_GET(self):
            nonlocal hits
            hits += 1
            self.send_response(200)
            self.end_headers()
            self.wfile.write(b'{"jobs": []}')

        def log_message(self, *_args):
            pass

    server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    port = server.server_address[1]
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()

    providers_dir = tmp_path / "providers"
    providers_dir.mkdir()
    # Provide shared helpers relative to stub via symlink or wrapper importing repo _http.mjs
    http_mjs = (REPO_PROVIDERS / "_http.mjs").as_uri()
    (providers_dir / "ssrf-probe.mjs").write_text(
        f"""
        import {{ fetchJson }} from '{http_mjs}';
        export default {{
          id: 'ssrf-probe',
          canHandle: (entry) => entry.careers_url?.includes('ssrf.invalid'),
          async fetch(entry, ctx) {{
            const data = await ctx.fetchJson(entry.api);
            return data.jobs || [];
          }},
        }};
        """,
        encoding="utf-8",
    )
    try:
        bridge = AtsBridge(providers_dir)
        res = bridge.fetch(
            "https://ssrf.invalid/board",
            company="Localhost Trap",
            provider_id="ssrf-probe",
            api_url=f"http://127.0.0.1:{port}/jobs.json",
        )
        assert res.ok is False
        assert hits == 0
    finally:
        server.shutdown()
        server.server_close()


def test_real_bridge_fetches_parses_and_validates_greenhouse_and_pageup_from_local_http(tmp_path):
    # Spin up local HTTP server serving real Greenhouse API JSON and real PageUp HTML listing
    greenhouse_payload = json.dumps({
        "jobs": [
            {
                "id": 101,
                "title": "Barista",
                "absolute_url": "https://boards.greenhouse.io/acmecafe/jobs/101",
                "location": {"name": "Rockdale, NSW 2216"},
                "updated_at": "2026-11-12T03:00:00Z",
                "content": "<p>Make specialty coffee in Rockdale.</p>",
            },
            {
                "id": 102,
                "title": "Broken Missing Url",
                "absolute_url": "javascript:alert(1)",
                "location": {"name": "Mascot, NSW"},
            },
        ]
    }).encode("utf-8")

    pageup_html = """
    <html><body><table><tbody id="search-results-content">
      <tr><td><a class="job-link" href="/999/cw/en/job/555/housekeeper">Housekeeper</a></td></tr>
    </tbody></table></body></html>
    """.encode("utf-8")

    class Handler(http.server.BaseHTTPRequestHandler):
        def do_GET(self):
            if self.path == "/gh.json":
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.end_headers()
                self.wfile.write(greenhouse_payload)
            elif self.path.startswith("/999/cw/en/listing"):
                self.send_response(200)
                self.send_header("Content-Type", "text/html")
                self.end_headers()
                self.wfile.write(pageup_html)
            elif self.path == "/slow":
                time.sleep(2.5)
                self.send_response(200)
                self.end_headers()
            else:
                self.send_response(404)
                self.end_headers()

        def log_message(self, *_args):
            pass

    server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    port = server.server_address[1]
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()

    providers_dir = tmp_path / "providers"
    providers_dir.mkdir()
    gh_uri = (REPO_PROVIDERS / "greenhouse.mjs").as_uri()
    pu_uri = (REPO_PROVIDERS / "pageup.mjs").as_uri()
    (providers_dir / "gh-local.mjs").write_text(
        f"""
        import gh from '{gh_uri}';
        export default {{
          id: 'gh-local',
          canHandle: (entry) => entry.careers_url?.includes('gh-local.example'),
          async fetch(entry) {{
            const localCtx = {{
              transport: 'http',
              async fetchJson() {{
                const r = await fetch('http://127.0.0.1:{port}/gh.json');
                return r.json();
              }},
            }};
            return gh.fetch({{ ...entry, api: 'https://boards-api.greenhouse.io/v1/boards/acmecafe/jobs?content=true' }}, localCtx);
          }},
        }};
        """,
        encoding="utf-8",
    )
    (providers_dir / "pu-local.mjs").write_text(
        f"""
        import pu from '{pu_uri}';
        export default {{
          id: 'pu-local',
          canHandle: (entry) => entry.careers_url?.includes('pu-local.example'),
          async fetch(entry) {{
            const localCtx = {{
              transport: 'http',
              async fetchText() {{
                const r = await fetch('http://127.0.0.1:{port}/999/cw/en/listing');
                return r.text();
              }},
            }};
            return pu.fetch({{ ...entry, api: 'https://careers.pageuppeople.com/999/cw/en/listing/' }}, localCtx);
          }},
        }};
        """,
        encoding="utf-8",
    )
    (providers_dir / "slow-local.mjs").write_text(
        f"""
        export default {{
          id: 'slow-local',
          canHandle: (entry) => entry.careers_url?.includes('slow-local.example'),
          async fetch(entry, ctx) {{
            const signal = undefined;
            const r = await fetch('http://127.0.0.1:{port}/slow', {{ signal }});
            return r.json();
          }},
        }};
        """,
        encoding="utf-8",
    )
    try:
        bridge = AtsBridge(providers_dir, max_requests_per_provider=5, max_total_requests=10)
        res_gh = bridge.fetch("https://gh-local.example/jobs", company="Acme Cafe", provider_id="gh-local")
        assert res_gh.ok is True
        assert [j.title for j in res_gh.jobs] == ["Barista"]
        assert res_gh.jobs[0].external_id == "101"
        assert res_gh.jobs[0].location == "Rockdale, NSW 2216"
        assert "Rockdale" in (res_gh.jobs[0].description or "")

        res_pu = bridge.fetch("https://pu-local.example/jobs", company="Bayside Hotel", provider_id="pu-local")
        assert res_pu.ok is True
        assert [(j.title, j.url, j.location) for j in res_pu.jobs] == [
            ("Housekeeper", "https://careers.pageuppeople.com/999/cw/en/job/555", "")
        ]

        # Timeout isolation: slow provider returns ok=False without crashing caller, subsequent calls keep working
        res_slow = bridge.fetch("https://slow-local.example/jobs", company="Slow Co", provider_id="slow-local", timeout_s=0.6)
        assert res_slow.ok is False and res_slow.error is not None
        assert bridge.fetch("https://pu-local.example/jobs", company="Bayside Hotel", provider_id="pu-local").ok is True
    finally:
        server.shutdown()
        server.server_close()


def test_bridge_resilience_caps_budgets_and_refuses_local_parser(tmp_path):
    providers_dir = tmp_path / "providers"
    providers_dir.mkdir()
    (providers_dir / "noisy.mjs").write_text(
        """
        export default {
          id: 'noisy',
          canHandle: (entry) => entry.careers_url?.includes('noisy.example'),
          async fetch() {
            return Array.from({ length: 15 }, (_, i) => ({
              title: `Cleaner ${i}`,
              url: `https://noisy.example/job/${i}`,
              company: 'Clean Co',
              location: 'Mascot NSW',
              description: 'X'.repeat(6000),
            }));
          },
        };
        """,
        encoding="utf-8",
    )
    bridge = AtsBridge(
        providers_dir,
        max_stdin_bytes=512,
        max_stdout_bytes=8192,
        max_jobs_per_provider=5,
        max_requests_per_provider=2,
        max_total_requests=3,
    )
    # 1. local-parser refused
    lp = bridge.fetch("https://evil.example/", company="Evil", provider_id="local-parser")
    assert lp.ok is False and "local-parser" in (lp.error or "")

    # 2. Oversized stdin rejected
    oversized = bridge.detect(["https://noisy.example/" + "a" * 600])
    assert oversized == []

    # 3. Max jobs and description capped cleanly within stdout byte budget
    noisy = bridge.fetch("https://noisy.example/", company="Clean Co", provider_id="noisy")
    assert noisy.ok is True
    assert len(noisy.jobs) <= 5
    assert all(len(j.description or "") <= 4000 for j in noisy.jobs)

    # 4. Exhausted global budget fails closed without invoking Node
    bridge.total_requests = 3
    exhausted = bridge.fetch("https://noisy.example/", company="Clean Co", provider_id="noisy")
    assert exhausted.ok is False and "budget" in (exhausted.error or "").lower()
