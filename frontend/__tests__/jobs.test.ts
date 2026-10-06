import { describe, expect, it } from "vitest";

import { daysAgoIso, formatSalary, safeExternalUrl } from "@/lib/jobs";
import { EMPTY_SEARCH, toCriteria, toListQuery } from "@/lib/jobSearch";

const NOW = new Date("2026-10-05T12:00:00Z");

describe("job search mapping", () => {
  it("maps empty controls to no filters", () => {
    expect(toListQuery(EMPTY_SEARCH, NOW)).toEqual({
      q: undefined,
      location: undefined,
      work_model: undefined,
      employment_type: undefined,
      source: undefined,
      posted_after: undefined,
    });
    expect(toCriteria(EMPTY_SEARCH, NOW)).toEqual({
      keywords: [],
      locations: [],
      work_models: [],
      employment_types: [],
      sources: null,
      posted_after: null,
    });
  });

  it("maps controls to discovery criteria", () => {
    const values = {
      keyword: " python , ,django ",
      location: "Berlin, Munich",
      workModel: "remote" as const,
      employmentType: "full_time" as const,
      source: "greenhouse",
      postedWithin: "7",
    };
    expect(toCriteria(values, NOW)).toEqual({
      keywords: ["python", "django"],
      locations: ["Berlin", "Munich"],
      work_models: ["remote"],
      employment_types: ["full_time"],
      sources: ["greenhouse"],
      posted_after: "2026-09-28",
    });
    expect(toListQuery(values, NOW)).toMatchObject({
      q: "python , ,django",
      posted_after: "2026-09-28",
    });
  });

  it("computes posted-after dates", () => {
    expect(daysAgoIso(1, NOW)).toBe("2026-10-04");
  });
});

describe("formatSalary", () => {
  const base = { salary_min: null, salary_max: null, salary_currency: null, salary_period: null };

  it("formats ranges, open ranges and periods", () => {
    expect(
      formatSalary({ ...base, salary_min: "60000", salary_max: "75000", salary_currency: "EUR", salary_period: "year" })
    ).toBe("€60,000 – €75,000 per year");
    expect(formatSalary({ ...base, salary_min: "45", salary_currency: "USD", salary_period: "hour" })).toBe(
      "from $45 per hour"
    );
    expect(formatSalary({ ...base, salary_max: "90000" })).toBe("up to 90,000");
    expect(formatSalary({ ...base, salary_min: "5000", salary_max: "5000", salary_currency: "EURO" })).toBe(
      "5,000"
    );
    expect(formatSalary(base)).toBeNull();
  });
});

describe("safeExternalUrl", () => {
  it("allows only http(s)", () => {
    expect(safeExternalUrl("https://jobs.lever.co/x/1")).toBe("https://jobs.lever.co/x/1");
    expect(safeExternalUrl("http://example.com/a?b=1")).toBe("http://example.com/a?b=1");
    expect(safeExternalUrl("javascript:alert(1)")).toBeNull();
    expect(safeExternalUrl("data:text/html,x")).toBeNull();
    expect(safeExternalUrl("/relative")).toBeNull();
    expect(safeExternalUrl(null)).toBeNull();
  });
});
