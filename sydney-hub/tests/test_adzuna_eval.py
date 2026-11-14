import httpx
import pytest

from hub.discovery.adzuna import evaluate_adzuna_au


def test_bounded_au_search_counts_evidence_without_publishing_rows():
    requests = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(200, json={"results": [
            {"title": "Barista", "created": "2026-11-12T03:00:00Z",
             "location": {"display_name": "Rockdale, Sydney"},
             "redirect_url": "https://www.adzuna.com.au/jobs/land/ad/123",
             "salary_min": 30, "salary_max": 35, "salary_is_predicted": 1},
            {"title": "Kitchen Hand", "salary_min": 28, "salary_is_predicted": 0},
            {"title": "Cleaner", "salary_min": 25},
        ]})

    with httpx.Client(transport=httpx.MockTransport(handler)) as client:
        report = evaluate_adzuna_au(client, app_id="secret-id", app_key="secret-key",
                                    queries=["barista", "kitchen hand", "cleaner", "ignored"], max_pages=99)
    assert report.requests <= 6
    assert len(requests) == report.requests
    assert all(req.url.host == "api.adzuna.com" and "/v1/api/jobs/au/search/" in req.url.path for req in requests)
    assert all(req.url.params["where"] == "Sydney" and req.url.params["results_per_page"] == "20" for req in requests)
    assert report.ads_seen == 3 * report.requests
    assert report.with_created == report.requests
    assert report.with_location == report.requests
    assert report.with_redirect == report.requests
    assert report.predicted_salary == report.requests
    assert report.advertised_salary == report.requests
    assert "secret-id" not in repr(report) and "secret-key" not in repr(report)
    assert not hasattr(report, "listings")


def test_missing_credentials_refuses_request():
    with httpx.Client(transport=httpx.MockTransport(lambda _r: pytest.fail("unexpected network"))) as client:
        with pytest.raises(ValueError, match="credentials"):
            evaluate_adzuna_au(client, app_id="", app_key="", queries=["barista"], max_pages=1)


def test_unauthorized_and_rate_limit_are_redacted_and_bounded(monkeypatch):
    delays = []
    monkeypatch.setattr("hub.discovery.adzuna.time.sleep", delays.append)
    attempts = 0

    def handler(_request: httpx.Request) -> httpx.Response:
        nonlocal attempts
        attempts += 1
        return httpx.Response(429 if attempts <= 2 else 401)

    with httpx.Client(transport=httpx.MockTransport(handler)) as client:
        report = evaluate_adzuna_au(client, app_id="secret-id", app_key="secret-key",
                                    queries=["barista", "cleaner"], max_pages=1)
    assert report.requests == 3
    assert report.ads_seen == 0
    assert delays == [1.0]
    assert report.errors == ("http_429", "http_401")
    assert "secret" not in repr(report)
