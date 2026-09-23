"""Free public discovery works on a fresh install; fixtures make no network calls."""
import inspect
import json

import pytest

from careerops import discovery
from careerops.policy import default_settings
from careerops.registry import board_identity


def test_public_boards_fetch_without_credentials(monkeypatch):
    settings = default_settings()
    assert settings["search"]["sources"] == []
    sources = [{"type": "greenhouse", "company": "Example A", "url": "https://job-boards.greenhouse.io/example-a", "enabled": True},
               {"type": "greenhouse", "company": "Example B", "url": "https://job-boards.greenhouse.io/example-b", "enabled": True},
               {"type": "ashby", "company": "Example C", "url": "https://jobs.ashbyhq.com/example-c", "enabled": True},
               {"type": "lever", "company": "Example D", "url": "https://jobs.lever.co/example-d", "enabled": True}]
    settings["search"]["sources"] = sources
    assert {board_identity(source["url"])["type"] for source in sources} == {"greenhouse", "ashby", "lever"}
    calls = []

    def fetch(url, **kwargs):
        kwargs["request_hook"](url)
        calls.append(url)
        assert kwargs["max_bytes"] == discovery.BOARD_MAX_BYTES
        if "greenhouse" in url:
            data = {"jobs": [{"id": len(calls), "internal_job_id": len(calls), "title": "Engineer",
                             "content": "Build Python systems.", "location": {"name": "London, UK"},
                             "absolute_url": f"https://example.org/jobs/{len(calls)}"}]}
        elif "ashbyhq" in url:
            data = {"jobs": [{"id": "example", "title": "Research Engineer", "descriptionPlain": "Build Python tools.",
                             "location": "London, UK", "jobUrl": "https://example.org/jobs/ashby", "isListed": True}]}
        else:
            data = [{"id": "example", "text": "Forward Deployed Engineer", "descriptionPlain": "Build Python tools.",
                     "categories": {"location": "London, UK"}, "hostedUrl": "https://example.org/jobs/lever"}]
        return discovery.Page(url, 200, {}, json.dumps(data).encode())

    monkeypatch.setattr(discovery, "safe_fetch", fetch)
    events = []
    result = discovery.discover(settings, "normal", events.append, lambda: False)
    assert len(calls) == result["boards_verified"] == result["jobs"] == 4
    assert result["spent_usd"] == "0" and result["queries"] == 0
    assert result["warnings"] == 0
    assert result["coverage"]["max_requests"] == 120
    assert result["coverage"]["timeout_seconds"] == 180
    assert all(record["response_byte_limit"] == discovery.BOARD_MAX_BYTES for record in result["pagination"].values())


def test_fresh_install_lists_every_overseas_country_disabled_with_recognised_cities():
    from careerops.policy import COUNTRIES, country_codes
    from careerops.store import validate_settings
    settings = default_settings()
    validate_settings(settings)
    locations = settings["locations"]
    assert set(locations) == set(COUNTRIES) - {"GB"}
    assert not any(config["enabled"] for config in locations.values())
    assert len({config["priority"] for config in locations.values()}) == 1
    for code, config in locations.items():
        assert config["cities"] and all(country_codes(city) == [code] for city in config["cities"])


def test_empty_or_disabled_sources_require_setup_without_silent_fallback(monkeypatch):
    def forbidden(*args, **kwargs):
        raise AssertionError("A disabled or absent source must not make a request")

    monkeypatch.setattr(discovery, "safe_fetch", forbidden)
    for sources in ([], [{"url": "https://job-boards.greenhouse.io/example", "enabled": False}]):
        settings = default_settings()
        settings["search"]["sources"] = sources
        events = []
        result = discovery.discover(settings, "normal", events.append, lambda: False)
        assert result["status"] == "setup_required"
        assert result["requests"] == result["jobs"] == 0
        assert "Enable an employer board in Settings" in result["message"]
        assert settings["search"]["sources"] == sources


def test_source_failure_is_reported_while_other_sources_continue(monkeypatch):
    settings = default_settings()
    settings["search"]["sources"] = [{"url": "https://job-boards.greenhouse.io/example"},
                                      {"url": "https://job-boards.greenhouse.io/healthy"}]

    def fetch(url, **kwargs):
        kwargs["request_hook"](url)
        if "/healthy/" in url:
            return discovery.Page(url, 200, {}, b'{"jobs": []}')
        raise discovery.FetchError("Source returned HTTP 503; retry later.")

    monkeypatch.setattr(discovery, "safe_fetch", fetch)
    events = []
    result = discovery.discover(settings, "normal", events.append, lambda: False)
    assert result["status"] == "completed_with_source_limits"
    assert result["boards_verified"] == 1 and result["requests"] == 2
    assert "source_failed" in result["limitations"] and "could not be read" in result["message"]
    assert any("job-boards.greenhouse.io" in event.get("message", "") for event in events if event["kind"] == "warning")


def test_larger_ats_feed_bound_does_not_raise_ordinary_page_limit():
    assert discovery.BOARD_MAX_BYTES == 32_000_000
    assert inspect.signature(discovery.safe_fetch).parameters["max_bytes"].default == 2_000_000


def test_commercial_spend_is_not_salary_and_does_not_drop_following_jobs(monkeypatch, tmp_path):
    from careerops.store import Store

    settings = default_settings()
    settings["search"]["sources"] = [{"url": "https://job-boards.greenhouse.io/example"}]
    rows = [{"id": n, "title": "Customer Success Manager", "content": text,
             "location": {"name": "London, UK"}, "absolute_url": f"https://example.org/jobs/{n}"}
            for n, text in enumerate([
                "Manage customer accounts ranging from ~$100K to $10M+ in annual spend.\nBase salary £60,000 to £80,000 per year.",
                "Support client accounts with £100k in annual spend.",
                "Salary £90,000 to £70,000 per year; confirm the stated range.",
                "Salary £70,000 per year.",
            ], 1)]

    def fetch(url, **kwargs):
        kwargs["request_hook"](url)
        return discovery.Page(url, 200, {}, json.dumps({"jobs": [None, *rows]}).encode())

    monkeypatch.setattr(discovery, "safe_fetch", fetch)
    store = Store(tmp_path / "discovery.sqlite3")

    def save(event):
        if event["kind"] == "job":
            store.upsert_job(event["job"], refresh=False)

    result = discovery.discover(settings, "normal", save, lambda: False)
    jobs = list(reversed(store.jobs()))
    assert result["jobs"] == len(jobs) == 4 and result["malformed"] == 1
    assert (jobs[0]["salary_min"], jobs[0]["salary_max"]) == (60000, 80000)
    assert jobs[1]["salary_min"] is jobs[1]["salary_max"] is None
    assert jobs[2]["salary_min"] is jobs[2]["salary_max"] is None
    assert jobs[3]["salary_min"] == jobs[3]["salary_max"] == 70000
    assert result["pagination"]["greenhouse:example"]["page_complete"]


def test_workspace_failure_is_not_misreported_as_a_bad_public_board(monkeypatch):
    settings = default_settings()
    settings["search"]["sources"] = [{"url": "https://job-boards.greenhouse.io/example"}]

    def fetch(url, **kwargs):
        kwargs["request_hook"](url)
        return discovery.Page(url, 200, {}, b'{"jobs": []}')

    def broken_workspace(event):
        if event["kind"] == "board":
            raise ValueError("Synthetic storage rejection")

    monkeypatch.setattr(discovery, "safe_fetch", fetch)
    with pytest.raises(RuntimeError, match="persist its progress") as caught:
        discovery.discover(settings, "normal", broken_workspace, lambda: False)
    assert isinstance(caught.value.__cause__, ValueError)


def test_relocation_checks_skip_irrelevant_adverts_and_preserve_unicode(monkeypatch):
    from careerops import inventory

    search, patterns = inventory.re.search, []

    def tracked(pattern, *args, **kwargs):
        patterns.append(pattern)
        return search(pattern, *args, **kwargs)

    monkeypatch.setattr(inventory.re, "search", tracked)
    for value in (True, "advertised", "available", "provided", "yes"):
        assert inventory._relocation({"relocation": value}) == "advertised"
    for value in (False, "unavailable", "not_provided", "no"):
        assert inventory._relocation({"relocation": value}) == "unavailable"
    assert patterns == []
    assert inventory._relocation({"description": "We offer a London office."}) == "unknown"
    assert patterns == ["relocation"]
    assert inventory._relocation({"description": "RELOCATİON SUPPORT IS PROVIDED"}) == "advertised"
    assert inventory._relocation({"description": "No relocation assistance is offered."}) == "unavailable"
