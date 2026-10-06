"""Lever Postings API adapter.

Public, documented API -- no credentials needed for reading published
postings: https://github.com/lever/postings-api
("Note that all job postings in the `published` state are publicly viewable.")

Endpoint used:

    GET https://api.lever.co/v0/postings/{site}?mode=json&skip=N&limit=M
        (EU instance: https://api.eu.lever.co/v0/postings/{site})
        -> [{id, text, categories: {location, commitment, team, department,
             allLocations}, country, description, descriptionPlain, lists:
             [{text, content}], additional, hostedUrl, applyUrl,
             workplaceType, salaryRange: {currency, interval, min, max}, ...}]

The API's own filters (`location`, `commitment`, `team`) are exact,
case-sensitive matches on each company's own free-text values, so they can't
express "Berlin" matching "Berlin, Germany". JobPilot therefore pages through
all postings with `skip`/`limit` and applies every filter itself
(`native_filters` is empty).

Lever's API has no company-name field: the configured display name (or the
site name) is used. `createdAt` (epoch milliseconds) is not in Lever's field
table; it is used as the posting date only when present.
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
from app.services.job_discovery.sources.base import SourceResponseError
from app.services.job_discovery.sources.html import (
    classified_lists,
    classify_heading,
    extract_sections,
    html_list_items,
    html_to_text,
)
from app.services.job_discovery.sources.http import JsonFetcher

API_HOSTS = {"global": "api.lever.co", "eu": "api.eu.lever.co"}
PAGE_SIZE = 100


class LeverJobSource(BoardJobSource):
    name = "lever"
    display_name = "Lever"
    description = "Public Lever job sites of the configured companies."

    def __init__(
        self,
        boards: list[Board],
        fetcher: JsonFetcher,
        *,
        max_jobs_per_board: int,
        instance: str = "global",
    ) -> None:
        super().__init__(boards, fetcher, max_jobs_per_board=max_jobs_per_board)
        if instance not in API_HOSTS:
            raise ValueError("LEVER_INSTANCE must be 'global' or 'eu'.")
        self.api_host = API_HOSTS[instance]

    async def fetch_board(self, client: httpx.AsyncClient, board: Board) -> BoardResult:
        url = f"https://{self.api_host}/v0/postings/{quote(board.key, safe='')}"
        jobs: list[RawJob] = []
        unreadable = 0
        skip = 0
        while skip < self._max_jobs_per_board:
            page = await self._fetcher.get_json(
                client,
                url,
                params={"mode": "json", "skip": str(skip), "limit": str(PAGE_SIZE)},
                target=board.key,
            )
            if not isinstance(page, list):
                raise SourceResponseError(target=board.key)
            read = self.read_items(page, board, lambda item: self.to_raw_job(item, board=board))
            jobs.extend(read.jobs)
            unreadable += read.unreadable_items
            if len(page) < PAGE_SIZE:
                break
            skip += PAGE_SIZE
        return BoardResult(jobs=jobs, unreadable_items=unreadable)

    def to_raw_job(self, item: dict[str, Any], *, board: Board) -> RawJob:
        categories = item.get("categories")
        categories = categories if isinstance(categories, dict) else {}

        location = as_str(categories.get("location"))
        locations = [location] if location else []
        for extra in categories.get("allLocations") or []:
            if (value := as_str(extra)) and value not in locations:
                locations.append(value)

        description_html = as_str(item.get("description"))
        requirements: list[str] = []
        responsibilities: list[str] = []
        description_parts: list[str] = []
        if text := html_to_text(description_html) or as_str(item.get("descriptionPlain")):
            description_parts.append(text)
        from_description = classified_lists(extract_sections(description_html))
        requirements.extend(from_description["requirements"])
        responsibilities.extend(from_description["responsibilities"])

        for entry in item.get("lists") or []:
            if not isinstance(entry, dict):
                continue
            heading = as_str(entry.get("text"))
            items = html_list_items(as_str(entry.get("content")))
            if heading or items:
                bullet_lines = "\n".join(f"• {i}" for i in items)
                description_parts.append("\n".join(p for p in (heading, bullet_lines) if p))
            kind = classify_heading(heading)
            if kind == "requirements":
                requirements.extend(items)
            elif kind == "responsibilities":
                responsibilities.extend(items)

        if additional := html_to_text(as_str(item.get("additional"))):
            description_parts.append(additional)

        team = as_str(categories.get("department")) or as_str(categories.get("team"))
        workplace = as_str(item.get("workplaceType"))
        created_at = item.get("createdAt")

        return RawJob(
            source=self.name,
            source_job_id=as_str(item.get("id")),
            source_company_key=board.key,
            title=as_str(item.get("text")),
            company_name=board.company_name or board.key,
            url=as_str(item.get("hostedUrl")),
            apply_url=as_str(item.get("applyUrl")),
            location=location,
            locations=tuple(locations),
            country=as_str(item.get("country")),
            department=team,
            description="\n\n".join(description_parts) or None,
            requirements=tuple(requirements),
            responsibilities=tuple(responsibilities),
            salary=_salary(item.get("salaryRange")),
            employment_type=as_str(categories.get("commitment")),
            work_model=None if workplace == "unspecified" else workplace,
            posted_at=created_at if isinstance(created_at, int | float) else None,
            raw_payload=item,
        )


def _salary(value: object) -> RawSalary | None:
    if not isinstance(value, dict):
        return None
    min_amount = value.get("min")
    max_amount = value.get("max")
    if not isinstance(min_amount, int | float) and not isinstance(max_amount, int | float):
        return None
    return RawSalary(
        min_amount=min_amount if isinstance(min_amount, int | float) else None,
        max_amount=max_amount if isinstance(max_amount, int | float) else None,
        currency=as_str(value.get("currency")),
        period=as_str(value.get("interval")),
    )
