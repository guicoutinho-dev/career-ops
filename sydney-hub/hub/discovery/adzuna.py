"""Private coverage evaluation only; Adzuna adverts never enter the catalog."""
from __future__ import annotations

import time
from dataclasses import dataclass
from datetime import datetime
from urllib.parse import urlsplit

import httpx


_SEARCH = "https://api.adzuna.com/v1/api/jobs/au/search"


@dataclass(frozen=True)
class AdzunaEvaluation:
    source: str
    requests: int
    ads_seen: int
    with_created: int
    with_location: int
    with_redirect: int
    predicted_salary: int
    advertised_salary: int
    errors: tuple[str, ...]


def evaluate_adzuna_au(
    client: httpx.Client, *, app_id: str, app_key: str, queries: list[str], max_pages: int
) -> AdzunaEvaluation:
    if not app_id or not app_key:
        raise ValueError("Adzuna credentials are required")
    if max_pages < 1:
        raise ValueError("At least one page is required")

    requests = seen = created = located = redirected = predicted = advertised = 0
    errors: list[str] = []
    stop = False
    for query in [item.strip()[:80] for item in queries if isinstance(item, str) and item.strip()][:3]:
        for page in range(1, min(max_pages, 2) + 1):
            if stop:
                break
            for attempt in range(2):
                requests += 1
                try:
                    response = client.get(
                        f"{_SEARCH}/{page}",
                        params={"app_id": app_id, "app_key": app_key, "results_per_page": 20,
                                "what": query, "where": "Sydney", "content-type": "application/json"},
                        headers={"accept": "application/json"}, timeout=10.0, follow_redirects=False,
                    )
                except httpx.HTTPError:
                    errors.append("network_error")
                    break
                if response.status_code == 401:
                    errors.append("http_401")
                    stop = True
                    break
                if response.status_code == 429 or response.status_code >= 500:
                    if attempt == 0:
                        time.sleep(1.0)
                        continue
                    errors.append(f"http_{response.status_code}")
                    break
                if response.status_code != 200:
                    errors.append(f"http_{response.status_code}")
                    break
                try:
                    payload = response.json()
                except ValueError:
                    errors.append("invalid_json")
                    break
                rows = payload.get("results") if isinstance(payload, dict) else None
                if not isinstance(rows, list):
                    errors.append("invalid_results")
                    break
                for row in rows[:20]:
                    if not isinstance(row, dict):
                        continue
                    seen += 1
                    raw_date = row.get("created")
                    try:
                        parsed = datetime.fromisoformat(raw_date.replace("Z", "+00:00")) if isinstance(raw_date, str) else None
                        created += bool(parsed and parsed.tzinfo)
                    except ValueError:
                        pass
                    location = row.get("location")
                    located += bool(isinstance(location, dict) and isinstance(location.get("display_name"), str)
                                    and location["display_name"].strip())
                    link = row.get("redirect_url")
                    parsed_url = urlsplit(link) if isinstance(link, str) else None
                    redirected += bool(parsed_url and parsed_url.scheme in {"http", "https"} and parsed_url.hostname)
                    has_salary = isinstance(row.get("salary_min"), (int, float)) or isinstance(row.get("salary_max"), (int, float))
                    predicted += bool(has_salary and row.get("salary_is_predicted") in (1, True))
                    advertised += bool(has_salary and row.get("salary_is_predicted") in (0, False))
                break
        if stop:
            break
    return AdzunaEvaluation("Adzuna API (private evaluation)", requests, seen, created, located,
                            redirected, predicted, advertised, tuple(errors))
