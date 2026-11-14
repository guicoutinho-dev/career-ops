import httpx
import httpcore
import pytest

from hub.discovery.website import _PublicBackend, inspect_website, safe_website_client


def _client(pages):
    seen = []

    def respond(request):
        seen.append(str(request.url))
        if str(request.url) not in pages:
            return httpx.Response(404, text="Not Found", headers={"content-type": "text/html"})
        value = pages[str(request.url)]
        if isinstance(value, Exception):
            raise value
        if isinstance(value, tuple):
            return httpx.Response(value[0], headers=value[1])
        return httpx.Response(200, text=value, headers={"content-type": "text/html"})

    return httpx.Client(transport=httpx.MockTransport(respond), follow_redirects=False), seen


@pytest.fixture(autouse=True)
def public_dns(monkeypatch):
    monkeypatch.setattr("socket.getaddrinfo", lambda *_args, **_kwargs: [(2, 1, 6, "", ("93.184.215.14", 443))])


def test_homepage_creates_lead():
    client, _ = _client({"https://cafe.example/": '<meta property="og:site_name" content="Cafe Aurora"><h1>Welcome</h1>'})
    result = inspect_website("https://cafe.example/", client=client, checked_at=123)
    assert result.outcome == "ok"
    assert [(f.verification_state, f.source_url, f.employer_name, f.title) for f in result.findings] == [
        ("employer_lead", "https://cafe.example/", "Cafe Aurora", None)
    ]


def test_generic_careers_is_not_verified():
    client, _ = _client({
        "https://cafe.example/": '<title>Cafe Aurora</title><a href="/careers">Careers</a>',
        "https://cafe.example/careers": '<h1>Careers</h1><p>We are always hiring friendly people.</p>',
    })
    result = inspect_website("https://cafe.example/", client=client, checked_at=123)
    assert result.outcome == "ok"
    assert all(f.verification_state != "verified_job" for f in result.findings)
    assert result.findings[0].verification_state == "employer_lead"


def test_evergreen_barista_careers_is_not_verified():
    client, _ = _client({
        "https://cafe.example/": (
            '<title>Cafe Aurora</title><h1>Barista careers</h1>'
            '<p>We are always hiring baristas. Send a speculative application.</p>'
            '<a href="/apply">Apply now</a>'
        ),
    })
    result = inspect_website("https://cafe.example/", client=client, checked_at=123)
    assert not any(f.verification_state == 'verified_job' for f in result.findings)


def test_specific_barista_listing_is_verified():
    client, _ = _client({
        "https://cafe.example/": '<meta property="og:site_name" content="Cafe Aurora"><a href="/careers">Careers</a>',
        "https://cafe.example/careers": '<h1>Careers</h1><a href="/careers/barista">Barista vacancy</a>',
        "https://cafe.example/careers/barista": '<h1>Barista</h1><p>Prepare coffee for guests.</p><a href="/apply/barista">Apply now</a>',
    })
    result = inspect_website("https://cafe.example/", client=client, checked_at=123)
    found = [f for f in result.findings if f.verification_state == "verified_job"]
    assert result.requests == 3
    assert len(found) == 1
    assert found[0].title == "Barista"
    assert found[0].source_url == "https://cafe.example/careers/barista"
    assert "Barista" in found[0].excerpt and "Apply now" in found[0].excerpt
    assert found[0].checked_at == 123
    assert not hasattr(found[0], "pay_display") and not hasattr(found[0], "location")
    assert not any(f.verification_state == "employer_lead" for f in result.findings)


def test_hiring_mention_without_listing_is_unverified():
    client, _ = _client({
        "https://cafe.example/": '<title>Cafe Aurora</title><a href="/careers">Careers</a>',
        "https://cafe.example/careers": '<h1>Careers</h1><p>We are hiring baristas. Email us to learn more.</p>',
    })
    result = inspect_website("https://cafe.example/", client=client, checked_at=123)
    assert any(f.verification_state == "unverified_job_post" for f in result.findings)
    assert not any(f.verification_state == "verified_job" for f in result.findings)


def test_timeout_has_no_job_finding():
    client, _ = _client({"https://cafe.example/": httpx.ReadTimeout("slow")})
    result = inspect_website("https://cafe.example/", client=client, checked_at=123)
    assert result.outcome == "timeout"
    assert result.findings == []


def test_redirect_to_loopback_is_blocked():
    client, seen = _client({"https://cafe.example/": (302, {"location": "http://127.0.0.1/private"})})
    result = inspect_website("https://cafe.example/", client=client, checked_at=123)
    assert result.outcome == "blocked"
    assert seen == ["https://cafe.example/"]


def test_private_dns_answer_is_blocked(monkeypatch):
    monkeypatch.setattr("socket.getaddrinfo", lambda *_args, **_kwargs: [(2, 1, 6, "", ("10.0.0.5", 443))])
    client, seen = _client({})
    result = inspect_website("https://cafe.example/", client=client, checked_at=123)
    assert result.outcome == "blocked"
    assert seen == []


def test_secondary_page_timeout_keeps_lead_and_reports_timeout():
    client, _ = _client({
        "https://cafe.example/": '<title>Cafe Aurora</title><a href="/careers">Careers</a>',
        "https://cafe.example/careers": httpx.ReadTimeout("slow"),
    })
    result = inspect_website("https://cafe.example/", client=client, checked_at=123)
    assert result.outcome == "timeout"
    assert [f.verification_state for f in result.findings] == ["employer_lead"]


def test_later_timeout_after_hiring_finding_is_reported():
    client, _ = _client({
        'https://cafe.example/': (
            '<title>Cafe Aurora</title><p>We are hiring baristas.</p>'
            '<a href="/careers/barista">Barista opening</a>'
        ),
        'https://cafe.example/careers/barista': httpx.ReadTimeout('slow'),
    })
    result = inspect_website('https://cafe.example/', client=client, checked_at=123)
    assert result.outcome == 'timeout'
    assert any(f.verification_state == 'unverified_job_post' for f in result.findings)


def test_safe_client_rechecks_and_pins_dns_at_connection(monkeypatch):
    answers = iter(['93.184.215.14', '127.0.0.1'])
    monkeypatch.setattr('socket.getaddrinfo', lambda *_args, **_kwargs: [
        (2, 1, 6, '', (next(answers), 443))
    ])
    connected = []
    monkeypatch.setattr(httpcore.SyncBackend, 'connect_tcp', lambda self, host, port, **kwargs:
                        connected.append(host))
    with safe_website_client() as client:
        result = inspect_website('https://cafe.example/', client=client, checked_at=123)
    assert result.outcome == 'blocked'
    assert connected == []


def test_safe_backend_connects_only_to_checked_ip(monkeypatch):
    monkeypatch.setattr('socket.getaddrinfo', lambda *_args, **_kwargs: [
        (2, 1, 6, '', ('93.184.215.14', 443))
    ])
    connected = []
    monkeypatch.setattr(httpcore.SyncBackend, 'connect_tcp', lambda self, host, port, **kwargs:
                        connected.append((host, port)) or 'socket')
    assert _PublicBackend().connect_tcp('cafe.example', 443) == 'socket'
    assert connected == [('93.184.215.14', 443)]


def test_closed_listing_is_not_verified():
    client, _ = _client({"https://cafe.example/": (
        '<title>Cafe Aurora</title><h1>Barista</h1><p>Applications closed. Position filled.</p>'
        '<a href="/apply">Apply now</a>'
    )})
    result = inspect_website("https://cafe.example/", client=client, checked_at=123)
    assert not any(f.verification_state == "verified_job" for f in result.findings)


def test_fragment_apply_link_is_not_a_job_application():
    client, _ = _client({"https://cafe.example/": (
        '<title>Cafe Aurora</title><h1>Barista</h1><a href="#">Apply now</a>'
    )})
    result = inspect_website("https://cafe.example/", client=client, checked_at=123)
    assert not any(f.verification_state == "verified_job" for f in result.findings)


def test_offsite_redirect_is_not_followed():
    client, seen = _client({"https://cafe.example/": (302, {"location": "https://other.example/jobs"})})
    result = inspect_website("https://cafe.example/", client=client, checked_at=123)
    assert result.outcome == "blocked"
    assert seen == ["https://cafe.example/"]


def test_script_text_does_not_create_hiring_post():
    client, _ = _client({"https://cafe.example/": (
        '<title>Cafe Aurora</title><script>const fake = "We are hiring baristas";</script>'
    )})
    result = inspect_website("https://cafe.example/", client=client, checked_at=123)
    assert [f.verification_state for f in result.findings] == ["employer_lead"]


def test_reads_modern_page_above_256kb_and_rejects_oversized_response():
    padding = " " * 350_000
    large_but_valid = (
        f'<title>Modern Cafe</title><body>{padding}'
        '<a href="/careers">Careers</a></body>'
    )
    huge = "x" * 2_100_000
    client, _ = _client({
        "https://modern.example/": large_but_valid,
        "https://modern.example/careers": '<h1>Barista</h1><p>Morning shift.</p><a href="/apply">Apply now</a>',
        "https://huge.example/": huge,
    })
    ok_res = inspect_website("https://modern.example/", client=client, checked_at=123)
    assert ok_res.outcome == "ok"
    assert [f.verification_state for f in ok_res.findings] == ["verified_job"]

    huge_res = inspect_website("https://huge.example/", client=client, checked_at=123)
    assert huge_res.outcome == "error"
    assert huge_res.findings == []


def test_blocks_multicast_empty_userinfo_non_http_port_and_https_downgrade(monkeypatch):
    monkeypatch.setattr("socket.getaddrinfo", lambda host, *_args, **_kwargs: [
        (2, 1, 6, "", ("224.0.0.1" if host == "224.0.0.1" else "93.184.215.14", 443))
    ])
    client, seen = _client({
        "https://cafe.example/": (302, {"location": "http://cafe.example/careers"}),
        "http://cafe.example/careers": "<h1>Careers</h1>",
    })
    for bad in ("https://224.0.0.1/", "http://@cafe.example/", "https://cafe.example:6379/"):
        assert inspect_website(bad, client=client, checked_at=1.0).outcome == "blocked"
    assert seen == []
    downgrade = inspect_website("https://cafe.example/", client=client, checked_at=1.0)
    assert downgrade.outcome == "blocked"
    assert seen == ["https://cafe.example/"]


def test_real_local_socket_loopback_blocked_without_connect_tcp_mock(monkeypatch):
    import http.server
    import threading

    monkeypatch.undo()
    hits = 0

    class Handler(http.server.BaseHTTPRequestHandler):
        def do_GET(self):
            nonlocal hits
            hits += 1
            self.send_response(200)
            self.end_headers()
            self.wfile.write(b"<h1>Should not connect</h1>")

        def log_message(self, *_args):
            pass

    server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    port = server.server_address[1]
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        with safe_website_client() as client:
            result = inspect_website(f"http://127.0.0.1:{port}/", client=client, checked_at=1.0)
        assert result.outcome == "blocked"
        assert hits == 0
    finally:
        server.shutdown()
        server.server_close()


def test_public_backend_falls_back_to_second_public_ip_on_connect_error(monkeypatch):
    monkeypatch.setattr("socket.getaddrinfo", lambda *_args, **_kwargs: [
        (2, 1, 6, "", ("93.184.215.1", 443)),
        (2, 1, 6, "", ("93.184.215.14", 443)),
    ])
    attempts = []

    def fake_connect(self, host, port, **kwargs):
        attempts.append((host, port))
        if host == "93.184.215.1":
            raise OSError("first ip unreachable")
        return "socket-ok"

    monkeypatch.setattr(httpcore.SyncBackend, "connect_tcp", fake_connect)
    assert _PublicBackend().connect_tcp("cafe.example", 443) == "socket-ok"
    assert attempts == [("93.184.215.1", 443), ("93.184.215.14", 443)]


def test_subpath_url_seeds_root_and_probes_canonical_careers_capturing_outbound_ats():
    client, seen = _client({
        "https://venue.example/eat-and-drink.html": '<title>Bayside Venue</title><p>Come dine with us.</p>',
        "https://venue.example/": '<title>Bayside Venue</title><a href="/about">About</a>',
        "https://venue.example/careers": (
            '<title>Careers - Bayside Venue</title>'
            '<iframe src="https://acme.pageuppeople.com/123/cw/en/listing/"></iframe>'
            '<a href="https://jobs.lever.co/acme">Open roles</a>'
        ),
        "https://venue.example/jobs": "<h1>404</h1>",
    })
    result = inspect_website("https://venue.example/eat-and-drink.html", client=client, checked_at=10.0)
    assert result.outcome == "ok"
    assert "https://venue.example/" in seen
    assert "https://venue.example/careers" in seen
    assert set(result.outbound_urls) == {
        "https://acme.pageuppeople.com/123/cw/en/listing/",
        "https://jobs.lever.co/acme",
    }
    assert all(url.startswith("https://venue.example/") for url in seen)


def test_footer_talent_pool_does_not_veto_explicit_listing_and_mailto_apply_works():
    client, _ = _client({
        "https://cafe.example/careers": (
            "<title>Aurora Cafe</title>"
            "<section><h2>Barista</h2>"
            "<p>Prepare specialty coffee. <a href='mailto:jobs@cafe.example'>Email resume</a></p></section>"
            "<footer>Join our talent pool anytime</footer>"
        ),
    })
    result = inspect_website("https://cafe.example/careers", client=client, checked_at=10.0)
    verified = [f for f in result.findings if f.verification_state == "verified_job"]
    assert len(verified) == 1
    assert verified[0].title == "Barista"

    mention_client, _ = _client({
        "https://bistro.example/careers": (
            "<title>Bistro</title><h1>Careers</h1>"
            "<p>We are hiring waitstaff. Email us to learn more.</p>"
        ),
    })
    mention_res = inspect_website("https://bistro.example/careers", client=mention_client, checked_at=10.0)
    assert [f.verification_state for f in mention_res.findings] == ["unverified_job_post"]
