"""Source adapters, the JobSource contract, the HTTP layer and the registry.

All HTTP is mocked (`FakeJobBoards`); no test reaches a real job board.
"""

import asyncio
from decimal import Decimal

import httpx
import pytest

from app.core.config import Settings
from app.core.errors import ValidationAppError
from app.schemas.jobs import JobSearchCriteria
from app.services.job_discovery.normalizer import JobNormalizer
from app.services.job_discovery.raw_job import RawJob
from app.services.job_discovery.registry import JobSourceRegistry, build_default_registry
from app.services.job_discovery.sources.ats import parse_board_config
from app.services.job_discovery.sources.base import (
    AcquisitionMethod,
    JobSource,
    SourceNotConfiguredError,
    SourceSearchResult,
)
from app.services.job_discovery.sources.greenhouse import API_HOST as GREENHOUSE_HOST
from app.services.job_discovery.sources.html import (
    classify_heading,
    extract_sections,
    html_to_text,
)
from tests.job_helpers import (
    LEVER_HOST,
    FakeJobBoards,
    fetcher_for,
    greenhouse_source,
    lever_source,
    load_fixture,
)

CRITERIA = JobSearchCriteria()


def search(source: JobSource) -> SourceSearchResult:
    return asyncio.run(source.search(CRITERIA))


@pytest.fixture
def boards() -> FakeJobBoards:
    boards = FakeJobBoards()
    boards.greenhouse_board("acme")
    boards.lever_site("globex")
    return boards


# --- JobSource contract ---------------------------------------------------------------


def test_job_source_is_abstract() -> None:
    with pytest.raises(TypeError):
        JobSource()  # type: ignore[abstract]


def test_a_minimal_custom_source_satisfies_the_contract() -> None:
    class StaticSource(JobSource):
        name = "static"
        display_name = "Static"
        acquisition_method = AcquisitionMethod.WEB

        async def search(self, criteria: JobSearchCriteria) -> SourceSearchResult:
            return SourceSearchResult(
                jobs=[RawJob("static", "1", "Engineer", "Acme", "https://x.com/1")],
                targets_attempted=1,
            )

    result = search(StaticSource())
    assert StaticSource().is_configured
    assert JobNormalizer().normalize(result.jobs[0]).dedup_key == "static:id:1"


# --- Greenhouse -------------------------------------------------------------------------


def test_greenhouse_parses_fixture_into_raw_jobs(boards: FakeJobBoards) -> None:
    result = search(greenhouse_source(boards))

    assert result.targets_attempted == 1
    assert result.targets_failed == 0
    assert result.unreadable_items == 1  # the non-object entry
    assert len(result.jobs) == 5  # incl. 2 that the normalizer will reject

    job = result.jobs[0]
    assert job.source == "greenhouse"
    assert job.source_job_id == "4012345"
    assert job.source_company_key == "acme"
    assert job.company_name == "Acme GmbH"  # from the board endpoint
    assert job.url.startswith("https://job-boards.greenhouse.io/acme/jobs/4012345")
    assert job.location == "Berlin, Germany"
    assert job.department == "Engineering"
    assert job.employment_type == "Full-time"  # from exposed custom field
    assert job.posted_at == "2026-09-20T08:00:00Z"
    assert job.updated_at == "2026-09-28T10:15:00-04:00"
    assert job.salary is not None
    assert (job.salary.min_amount, job.salary.max_amount, job.salary.currency) == (
        70000,
        90000,
        "EUR",
    )
    assert job.responsibilities == (
        "Design and build Python services",
        "Own our PostgreSQL data model",
    )
    assert job.requirements == (
        "5+ years of backend experience",
        "Kubernetes and AWS in production",
    )
    # Entity-encoded HTML became plain text; scripts are dropped entirely.
    assert job.description is not None
    assert "We build logistics software & love it." in job.description
    assert "<" not in job.description and "alert" not in job.description
    assert job.raw_payload["id"] == 4012345

    werkstudent = result.jobs[2]
    assert werkstudent.requirements == ("Studium der Informatik", "SQL-Kenntnisse")
    assert werkstudent.responsibilities == ("Dashboards mit Tableau bauen",)
    assert werkstudent.posted_at == "2026-09-26T12:00:00+02:00"


def test_greenhouse_requests_documented_endpoints(boards: FakeJobBoards) -> None:
    search(greenhouse_source(boards))
    paths = {(r.url.path, r.url.params.get("content")) for r in boards.requests}
    assert ("/v1/boards/acme/jobs", "true") in paths
    assert ("/v1/boards/acme", None) in paths
    assert all(r.url.scheme == "https" and r.url.host == GREENHOUSE_HOST for r in boards.requests)


def test_greenhouse_company_name_falls_back_when_board_endpoint_fails() -> None:
    boards = FakeJobBoards()
    boards.greenhouse_board("acme")
    boards.set(GREENHOUSE_HOST, "/v1/boards/acme", httpx.Response(500))
    result = search(greenhouse_source(boards))
    assert result.targets_failed == 0
    assert result.jobs[0].company_name == "acme"


def test_greenhouse_uses_configured_company_name(boards: FakeJobBoards) -> None:
    result = search(greenhouse_source(boards, "acme:ACME Corp"))
    assert result.jobs[0].company_name == "ACME Corp"
    assert not any(r.url.path == "/v1/boards/acme" for r in boards.requests)


@pytest.mark.parametrize(
    ("responder", "code"),
    [
        (httpx.Response(404), "SOURCE_NOT_FOUND"),
        (httpx.Response(429), "SOURCE_RATE_LIMITED"),
        (httpx.Response(503), "SOURCE_UNAVAILABLE"),
        (httpx.Response(403), "SOURCE_HTTP_ERROR"),
        (httpx.Response(302, headers={"location": "http://169.254.169.254/"}), "SOURCE_HTTP_ERROR"),
        (httpx.Response(200, content=b"<html>not json</html>"), "SOURCE_INVALID_RESPONSE"),
        (httpx.Response(200, json=["not", "an", "object"]), "SOURCE_INVALID_RESPONSE"),
        (httpx.Response(200, json={"jobs": "nope"}), "SOURCE_INVALID_RESPONSE"),
        (httpx.ReadTimeout("slow"), "SOURCE_TIMEOUT"),
        (httpx.ConnectError("refused"), "SOURCE_UNAVAILABLE"),
    ],
)
def test_greenhouse_errors_are_mapped_to_user_safe_issues(
    responder: httpx.Response | Exception, code: str
) -> None:
    boards = FakeJobBoards()
    boards.set(GREENHOUSE_HOST, "/v1/boards/acme/jobs", responder)
    result = search(greenhouse_source(boards))
    assert result.targets_failed == result.targets_attempted == 1
    assert result.jobs == []
    issue = result.issues[0]
    assert issue.code == code
    assert issue.target == "acme"
    assert "Traceback" not in issue.message and "not json" not in issue.message


def test_redirects_are_never_followed() -> None:
    boards = FakeJobBoards()
    boards.set(
        GREENHOUSE_HOST,
        "/v1/boards/acme/jobs",
        httpx.Response(301, headers={"location": "https://evil.example.com/"}),
    )
    search(greenhouse_source(boards))
    assert all(r.url.host == GREENHOUSE_HOST for r in boards.requests)


def test_oversized_responses_are_rejected() -> None:
    boards = FakeJobBoards()
    boards.greenhouse_board("acme")
    result = search(greenhouse_source(boards, max_response_bytes=100))
    assert result.issues[0].code == "SOURCE_INVALID_RESPONSE"


def test_one_failing_board_does_not_discard_the_others() -> None:
    boards = FakeJobBoards()
    boards.greenhouse_board("acme")
    boards.set(GREENHOUSE_HOST, "/v1/boards/broken/jobs", httpx.Response(500))
    result = search(greenhouse_source(boards, "acme,broken"))
    assert result.targets_attempted == 2
    assert result.targets_failed == 1
    assert len(result.jobs) == 5
    assert [i.target for i in result.issues] == ["broken"]


def test_unconfigured_source_raises() -> None:
    with pytest.raises(SourceNotConfiguredError):
        search(greenhouse_source(FakeJobBoards(), ""))


def test_max_jobs_per_board_is_enforced(boards: FakeJobBoards) -> None:
    result = search(greenhouse_source(boards, max_jobs_per_board=2))
    assert len(result.jobs) == 2


# --- Lever ----------------------------------------------------------------------------------


def test_lever_parses_fixture_into_raw_jobs(boards: FakeJobBoards) -> None:
    result = search(lever_source(boards))
    assert len(result.jobs) == 2
    job = result.jobs[0]
    assert job.source == "lever"
    assert job.source_job_id == "5ac21346-8e0c-4494-8e7a-3eb92ff77902"
    assert job.company_name == "Globex"  # configured display name
    assert job.url == "https://jobs.lever.co/globex/5ac21346-8e0c-4494-8e7a-3eb92ff77902"
    assert job.apply_url is not None and job.apply_url.endswith("/apply")
    assert job.locations == ("Berlin", "Hamburg")
    assert job.country == "DE"
    assert job.department == "Engineering"
    assert job.employment_type == "Full-time"
    assert job.work_model == "hybrid"
    assert job.requirements == ("3+ years of TypeScript and React", "Experience with Next.js")
    assert job.responsibilities == ("Build our planning UI", "Improve accessibility")
    assert job.description is not None
    assert "Benefits" in job.description and "30 days of vacation" in job.description
    assert "equal opportunity" in job.description
    assert job.salary is not None and job.salary.period == "per-year-salary"
    assert job.posted_at == 1758000000000


def test_lever_without_display_name_uses_site_name(boards: FakeJobBoards) -> None:
    assert search(lever_source(boards, "globex")).jobs[0].company_name == "globex"


def test_lever_pages_through_all_postings() -> None:
    postings = load_fixture("lever_postings.json")
    many = [
        {**postings[1], "id": f"id-{i}", "hostedUrl": f"https://jobs.lever.co/globex/id-{i}"}
        for i in range(230)
    ]
    boards = FakeJobBoards()
    boards.lever_site("globex", many)
    result = search(lever_source(boards))
    assert len(result.jobs) == 230
    skips = [r.url.params["skip"] for r in boards.requests_to(LEVER_HOST)]
    assert skips == ["0", "100", "200"]
    assert all(r.url.params["mode"] == "json" for r in boards.requests_to(LEVER_HOST))


def test_lever_pagination_respects_cap() -> None:
    postings = load_fixture("lever_postings.json")
    many = [{**postings[1], "id": f"id-{i}"} for i in range(500)]
    boards = FakeJobBoards()
    boards.lever_site("globex", many)
    result = search(lever_source(boards, max_jobs_per_board=150))
    assert len(result.jobs) == 150
    assert len(boards.requests_to(LEVER_HOST)) == 2


def test_lever_malformed_response() -> None:
    boards = FakeJobBoards()
    boards.lever_site("globex", {"error": "unexpected"})
    result = search(lever_source(boards))
    assert result.targets_failed == 1
    assert result.issues[0].code == "SOURCE_INVALID_RESPONSE"


def test_lever_eu_instance_uses_eu_host() -> None:
    source = lever_source(FakeJobBoards())
    assert source.api_host == "api.lever.co"
    from app.services.job_discovery.sources.lever import LeverJobSource

    eu = LeverJobSource([], fetcher_for(FakeJobBoards(), []), max_jobs_per_board=1, instance="eu")
    assert eu.api_host == "api.eu.lever.co"
    with pytest.raises(ValueError):
        LeverJobSource([], fetcher_for(FakeJobBoards(), []), max_jobs_per_board=1, instance="x")


# --- HTTP safety ----------------------------------------------------------------------------


def test_fetcher_refuses_hosts_outside_the_allowlist() -> None:
    fetcher = fetcher_for(FakeJobBoards(), [GREENHOUSE_HOST])

    async def run(url: str) -> None:
        async with fetcher.session() as client:
            await fetcher.get_json(client, url)

    for url in (
        "https://169.254.169.254/latest/meta-data",
        "http://boards-api.greenhouse.io/v1/boards/acme/jobs",
        "https://user:pw@boards-api.greenhouse.io/v1/boards",
        "https://localhost/v1",
    ):
        with pytest.raises(ValueError):
            asyncio.run(run(url))


def test_board_names_are_validated_before_use() -> None:
    boards = parse_board_config("acme, ../etc ,evil.com/x,a b, ok_board-1:Nice Name ,ACME")
    assert [(b.key, b.company_name) for b in boards] == [
        ("acme", None),
        ("ok_board-1", "Nice Name"),
    ]


# --- HTML helpers --------------------------------------------------------------------------------


def test_html_to_text_handles_structure_and_entities() -> None:
    html = "<h2>Role</h2><p>Hello&nbsp;<em>world</em> &amp; co</p><ul><li>One</li><li>Two</li></ul>"
    assert html_to_text(html) == "Role\n\nHello world & co\n\n• One\n• Two"
    assert html_to_text("") is None
    assert html_to_text("<style>p{}</style><script>x()</script>") is None


@pytest.mark.parametrize(
    ("heading", "expected"),
    [
        ("Requirements", "requirements"),
        ("What you'll need", "requirements"),
        ("What we're looking for", "requirements"),
        ("Dein Profil", "requirements"),
        ("What you'll do", "responsibilities"),
        ("Your responsibilities", "responsibilities"),
        ("Deine Aufgaben", "responsibilities"),
        ("Benefits", None),
        ("About us", None),
        (None, None),
    ],
)
def test_classify_heading(heading: str | None, expected: str | None) -> None:
    assert classify_heading(heading) == expected


def test_bold_only_paragraph_is_a_heading_but_long_bold_text_is_not() -> None:
    sections = extract_sections(
        "<p><strong>Your tasks</strong></p><ul><li>A</li></ul>"
        f"<p><b>{'Very important emphasis. ' * 10}</b></p>"
    )
    assert sections[0].heading == "Your tasks"
    assert sections[0].items == ["A"]
    assert len(sections) == 1  # the long bold paragraph stays body text


# --- Registry ------------------------------------------------------------------------------------


def test_registry_selection(boards: FakeJobBoards) -> None:
    registry = JobSourceRegistry()
    registry.register(greenhouse_source(boards))
    registry.register(lever_source(boards), enabled=False)

    assert [s.name for s in registry.select(None)] == ["greenhouse"]
    assert [s.name for s in registry.all()] == ["greenhouse", "lever"]
    with pytest.raises(ValidationAppError):
        registry.select(["lever"])  # disabled
    with pytest.raises(ValidationAppError):
        registry.select(["linkedin"])  # unknown
    with pytest.raises(ValueError):
        registry.register(greenhouse_source(boards))  # duplicate name


def test_default_registry_reads_configuration() -> None:
    config = Settings(
        JOB_SOURCES_ENABLED="greenhouse",
        GREENHOUSE_BOARD_TOKENS="acme,globex",
        LEVER_SITES="initech:Initech",
        LEVER_INSTANCE="eu",
    )
    registry = build_default_registry(config)
    greenhouse, lever = registry.all()
    assert registry.is_enabled("greenhouse") and not registry.is_enabled("lever")
    assert greenhouse.target_count == 2
    assert lever.target_count == 1
    assert lever.api_host == "api.eu.lever.co"  # type: ignore[attr-defined]


def test_raw_salary_amounts_survive_normalization(boards: FakeJobBoards) -> None:
    job = JobNormalizer().normalize(search(greenhouse_source(boards)).jobs[0])
    assert (job.salary_min, job.salary_max, job.salary_currency) == (
        Decimal("70000.00"),
        Decimal("90000.00"),
        "EUR",
    )
