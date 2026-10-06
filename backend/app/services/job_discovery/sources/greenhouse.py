"""Greenhouse Job Board API adapter.

Public, documented API -- no credentials needed:
https://developers.greenhouse.io/job-board.html
("Job Board data is publicly available, so authentication is not required
for any GET endpoints.")

Endpoints used (both GET, both public):

    https://boards-api.greenhouse.io/v1/boards/{board_token}
        -> {"name": "<organization name>", "content": "..."}
    https://boards-api.greenhouse.io/v1/boards/{board_token}/jobs?content=true
        -> {"jobs": [{id, internal_job_id, title, updated_at, requisition_id,
                      location: {name}, absolute_url, language, metadata,
                      content, departments, offices, ...}], "meta": {"total"}}

`id` is the job *post* id (what the posting URL and applications use), so
it is the `source_job_id`. `content` is entity-encoded HTML.

The list endpoint has no search parameters, so every filter is applied by
JobPilot after fetching (`native_filters` is empty). Fields the docs only
show on single-job responses (`company_name`, `first_published`,
`pay_input_ranges`) are used when present and never assumed.
"""

from typing import Any
from urllib.parse import quote

import httpx

from app.services.job_discovery.raw_job import RawJob, RawSalary
from app.services.job_discovery.sources.ats import (
    Board,
    BoardJobSource,
    BoardResult,
    as_str,
)
from app.services.job_discovery.sources.base import SourceError, SourceResponseError
from app.services.job_discovery.sources.html import (
    classified_lists,
    extract_sections,
    html_to_text,
)

API_HOST = "boards-api.greenhouse.io"
API_BASE_URL = f"https://{API_HOST}/v1/boards"

# Custom job fields ("metadata") a company may expose. Matched on the
# casefolded field name; only plain string values are used.
_EMPLOYMENT_TYPE_FIELDS = ("employment type", "time type", "job type", "contract type")
_WORK_MODEL_FIELDS = ("workplace type", "work model", "remote", "location type", "work type")
_SALARY_FIELDS = ("salary", "compensation", "pay range")


class GreenhouseJobSource(BoardJobSource):
    name = "greenhouse"
    display_name = "Greenhouse"
    description = "Public Greenhouse job boards of the configured companies."

    async def fetch_board(self, client: httpx.AsyncClient, board: Board) -> BoardResult:
        token = quote(board.key, safe="")
        data = await self._fetcher.get_json(
            client,
            f"{API_BASE_URL}/{token}/jobs",
            params={"content": "true"},
            target=board.key,
        )
        if not isinstance(data, dict) or not isinstance(data.get("jobs"), list):
            raise SourceResponseError(target=board.key)

        company_name = board.company_name or await self._board_name(client, board)

        return self.read_items(
            data["jobs"],
            board,
            lambda item: self.to_raw_job(item, board=board, company_name=company_name),
        )

    async def _board_name(self, client: httpx.AsyncClient, board: Board) -> str | None:
        """The organization name from the board endpoint. Best effort: if it
        fails, jobs still get the company name from their own payload or,
        as a last resort, the board token."""
        try:
            data = await self._fetcher.get_json(
                client, f"{API_BASE_URL}/{quote(board.key, safe='')}", target=board.key
            )
        except SourceError:
            return None
        return as_str(data.get("name")) if isinstance(data, dict) else None

    def to_raw_job(
        self, item: dict[str, Any], *, board: Board, company_name: str | None
    ) -> RawJob:
        content = as_str(item.get("content"))
        sections = classified_lists(extract_sections(content))
        metadata = _metadata_values(item.get("metadata"))

        location = item.get("location")
        location_name = as_str(location.get("name")) if isinstance(location, dict) else None
        locations = [location_name] if location_name else []
        for office in item.get("offices") or []:
            if isinstance(office, dict):
                office_location = as_str(office.get("location")) or as_str(office.get("name"))
                if office_location and office_location not in locations:
                    locations.append(office_location)

        departments = [
            name
            for dep in item.get("departments") or []
            if isinstance(dep, dict) and (name := as_str(dep.get("name")))
        ]

        return RawJob(
            source=self.name,
            source_job_id=as_str(item.get("id")),
            source_company_key=board.key,
            title=as_str(item.get("title")),
            # A configured display name wins; then the posting's own field, the
            # board's organization name, and finally the board token.
            company_name=board.company_name
            or as_str(item.get("company_name"))
            or company_name
            or board.key,
            url=as_str(item.get("absolute_url")),
            location=location_name,
            locations=tuple(locations),
            department=", ".join(departments) or None,
            description=html_to_text(content),
            requirements=tuple(sections["requirements"]),
            responsibilities=tuple(sections["responsibilities"]),
            salary=_salary(item, metadata),
            employment_type=_first_field(metadata, _EMPLOYMENT_TYPE_FIELDS),
            work_model=_first_field(metadata, _WORK_MODEL_FIELDS),
            posted_at=as_str(item.get("first_published")),
            updated_at=as_str(item.get("updated_at")),
            raw_payload=item,
        )


def _metadata_values(metadata: object) -> dict[str, str]:
    values: dict[str, str] = {}
    if not isinstance(metadata, list):
        return values
    for entry in metadata:
        if not isinstance(entry, dict):
            continue
        name = as_str(entry.get("name"))
        value = entry.get("value")
        if name and isinstance(value, str) and value.strip():
            values[name.casefold()] = value
    return values


def _first_field(metadata: dict[str, str], names: tuple[str, ...]) -> str | None:
    for field_name, value in metadata.items():
        if any(name in field_name for name in names):
            return value
    return None


def _salary(item: dict[str, Any], metadata: dict[str, str]) -> RawSalary | None:
    ranges = item.get("pay_input_ranges")
    if isinstance(ranges, list):
        for pay_range in ranges:
            if not isinstance(pay_range, dict):
                continue
            min_cents = pay_range.get("min_cents")
            max_cents = pay_range.get("max_cents")
            if isinstance(min_cents, int | float) or isinstance(max_cents, int | float):
                return RawSalary(
                    min_amount=min_cents / 100 if isinstance(min_cents, int | float) else None,
                    max_amount=max_cents / 100 if isinstance(max_cents, int | float) else None,
                    currency=as_str(pay_range.get("currency_type")),
                )
    text = _first_field(metadata, _SALARY_FIELDS)
    return RawSalary(text=text) if text else None
