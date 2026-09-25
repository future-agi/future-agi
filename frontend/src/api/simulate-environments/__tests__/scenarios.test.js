import { describe, it, expect, beforeEach, afterEach, vi } from "vitest";

// The runtime source switch: force it per-test to exercise the sample-mode
// delegation without touching window.location.
vi.mock("../scenariosSampleMode", () => ({
  isScenarioSampleMode: vi.fn(() => false),
  SAMPLE_PAGE_SIZE: 5,
}));

import {
  serializeScenarioParams,
  scenarioFromApi,
  listScenarios,
  scenarioCoverage,
  amendScenarios,
  scenariosCoveragePath,
  scenariosAmendPath,
} from "../scenarios";
import {
  isContractedApiPath,
  getContractedApiMethods,
} from "src/api/contracts/api-surface";
import { OPENAPI_CONTRACT } from "src/api/contracts/openapi-contract.generated";
import { MAX_SCENARIOS } from "src/sections/simulate/environments/panels/scenarioCountRules";
import { isScenarioSampleMode } from "../scenariosSampleMode";
import {
  queryScenarioFixture,
  amendScenarioFixture,
  coverageScenarioFixture,
  resetScenarioFixture,
  SAMPLE_ROWS,
} from "../_fixtures/scenariosFixtures";

import page1 from "../_fixtures/scenarioSamples/01-list-page-1.json";
import page2 from "../_fixtures/scenarioSamples/02-list-page-2.json";
import accentGrouped from "../_fixtures/scenarioSamples/03-list-grouped-by-accent.json";
import accentOr from "../_fixtures/scenarioSamples/04-list-filtered-accent-or.json";
import negated from "../_fixtures/scenarioSamples/05-list-filtered-negated.json";
import searchSample from "../_fixtures/scenarioSamples/06-list-search.json";
import coverageSample from "../_fixtures/scenarioSamples/07-coverage.json";
import coverageFilteredSample from "../_fixtures/scenarioSamples/08-coverage-filtered.json";
import keywordsSample from "../_fixtures/scenarioSamples/09-list-filtered-keywords.json";

const numbersOf = (res) => res.results.map((r) => r.number);

// The list / coverage / amend engines share one mutable WORKING copy; restore
// the committed suite before every test so an amend never leaks across cases.
beforeEach(() => resetScenarioFixture());

describe("scenarios.js — sample-mode delegation (?scnSample)", () => {
  afterEach(() => isScenarioSampleMode.mockReturnValue(false));

  it("serves the list from the emulator (not axios) when sample mode is on", async () => {
    isScenarioSampleMode.mockReturnValue(true);
    const res = await listScenarios("any-job", { page: 1, limit: 5 });
    expect(res.count).toBe(SAMPLE_ROWS.length);
    expect(res.current_page).toBe(1);
    expect(res.results).toHaveLength(5);
  });

  it("serves coverage from the emulator when sample mode is on", async () => {
    isScenarioSampleMode.mockReturnValue(true);
    expect(await scenarioCoverage("any-job", {})).toEqual(coverageSample);
  });

  it("routes an amend through the emulator when sample mode is on", async () => {
    isScenarioSampleMode.mockReturnValue(true);
    const name = SAMPLE_ROWS[3].name;
    const res = await amendScenarios("any-job", { changes: [{ op: "drop", scenario: name }] });
    expect(res.receipts).toEqual([{ scenario: name, outcome: "applied" }]);
  });
});

describe("serializeScenarioParams", () => {
  it("repeats an array key so it becomes an OR-set on the wire", () => {
    const qs = serializeScenarioParams({ "persona.accent": ["Canadian", "Indian"] });
    expect(qs).toBe("persona.accent=Canadian&persona.accent=Indian");
  });

  it("passes dotted and _not keys through untouched", () => {
    const qs = serializeScenarioParams({ "coverage.overlay_not": ["none"] });
    // URLSearchParams leaves dots and underscores unescaped.
    expect(qs).toBe("coverage.overlay_not=none");
  });

  it("drops undefined/null/empty-array values but keeps the empty string", () => {
    const qs = serializeScenarioParams({
      search: "",
      group_by: "",
      ordering: undefined,
      page: null,
      "persona.accent": [],
      limit: 25,
    });
    // search="" and group_by="" survive (group_by="" = no grouping, must send);
    // ordering/page/empty-array drop out.
    expect(qs).toBe("search=&group_by=&limit=25");
  });
});

describe("scenarioFromApi", () => {
  it("maps a server row into the SCENARIO_SHAPE and keeps the raw row", () => {
    const raw = SAMPLE_ROWS.find((r) => r.number === 3);
    const mapped = scenarioFromApi(raw);
    expect(mapped.id).toBe(raw.id);
    expect(mapped.number).toBe(3);
    expect(mapped.name).toBe(raw.name);
    expect(mapped.useCase).toBe(raw.use_case);
    expect(mapped.situation).toBe(raw.instruction);
    expect(mapped.task).toBe(raw.instruction);
    expect(mapped.expected).toBe(raw.tests);
    expect(mapped.conversationBranch).toBe(raw.branch);
    expect(mapped.subTasks).toEqual(raw.sub_goals);
    expect(mapped.persona.ageGroup).toBe(raw.persona.age_group);
    expect(mapped.persona.communicationStyle).toBe(raw.persona.communication_style);
    expect(mapped.coverage).toEqual(raw.coverage);
    expect(mapped.group).toBe(raw.group);
    expect(mapped._raw).toBe(raw);
  });

  it("tolerates a persona-less row", () => {
    expect(scenarioFromApi({ id: "x" }).persona).toBeNull();
  });
});

describe("queryScenarioFixture — pagination", () => {
  it("reproduces page 1 exactly — row order and page sections", () => {
    const res = queryScenarioFixture({ page: 1, limit: 5 });
    expect(res.count).toBe(page1.count);
    expect(res.total_pages).toBe(page1.total_pages);
    expect(res.current_page).toBe(1);
    expect(res.previous).toBeNull();
    expect(res.next).not.toBeNull();
    // The page is number-ordered then group-sorted, so a group can span pages.
    expect(numbersOf(res)).toEqual(numbersOf(page1));
    expect(res.groups).toEqual(page1.groups);
  });

  it("reproduces page 2 exactly", () => {
    const res = queryScenarioFixture({ page: 2, limit: 5 });
    expect(res.current_page).toBe(2);
    expect(numbersOf(res)).toEqual(numbersOf(page2));
    expect(res.groups).toEqual(page2.groups);
  });

  it("covers the whole suite across all four pages exactly once", () => {
    const seen = [];
    for (let p = 1; p <= 4; p += 1) {
      seen.push(...numbersOf(queryScenarioFixture({ page: p, limit: 5 })));
    }
    expect(new Set(seen).size).toBe(page1.count);
    expect(seen).toHaveLength(page1.count);
  });
});

describe("queryScenarioFixture — grouping", () => {
  it("reproduces the accent grouping exactly (row order and sections)", () => {
    const res = queryScenarioFixture({ limit: 25, group_by: "accent" });
    expect(res.group_by).toBe("accent");
    expect(numbersOf(res)).toEqual(numbersOf(accentGrouped));
    expect(res.groups).toEqual(accentGrouped.groups);
  });

  it("defaults to goal grouping when group_by is omitted", () => {
    const res = queryScenarioFixture({ limit: 25 });
    expect(res.group_by).toBe("goal");
    // Sections are contiguous runs of one group each (no group repeats).
    const names = res.groups.map((g) => g.name);
    expect(new Set(names).size).toBe(names.length);
  });

  it("drops grouping entirely for group_by=''", () => {
    const res = queryScenarioFixture({ limit: 25, group_by: "" });
    expect(res.group_by).toBe("");
    expect(res.results.every((r) => r.group === "")).toBe(true);
  });
});

describe("queryScenarioFixture — filters", () => {
  it("ORs a repeated key (persona.accent Canadian|Indian)", () => {
    const res = queryScenarioFixture({
      limit: 25,
      "persona.accent": ["Canadian", "Indian"],
    });
    expect(res.count).toBe(accentOr.count);
    expect(numbersOf(res)).toEqual(numbersOf(accentOr));
  });

  it("negates with a _not suffix (coverage.overlay_not=none)", () => {
    const res = queryScenarioFixture({ limit: 25, "coverage.overlay_not": ["none"] });
    expect(res.count).toBe(negated.count);
    expect(numbersOf(res)).toEqual(numbersOf(negated));
  });

  it("matches list membership with an OR across keywords", () => {
    const res = queryScenarioFixture({
      limit: 25,
      keywords: ["driver_status", "fare_quote"],
    });
    expect(res.count).toBe(keywordsSample.count);
    expect(numbersOf(res)).toEqual(numbersOf(keywordsSample));
  });
});

describe("queryScenarioFixture — search", () => {
  it("matches every word across name/instruction/use_case/branch, snake or spaced", () => {
    const res = queryScenarioFixture({ limit: 25, search: "payment sms" });
    expect(res.count).toBe(searchSample.count);
    expect(numbersOf(res)).toEqual(numbersOf(searchSample));
  });
});

describe("queryScenarioFixture — fields catalogue", () => {
  it("counts choices over the searched, not the filtered, suite", () => {
    const unfiltered = queryScenarioFixture({ limit: 25 });
    const filtered = queryScenarioFixture({
      limit: 25,
      "persona.accent": ["Canadian", "Indian"],
    });
    const accentOf = (res) => res.fields.find((f) => f.value === "persona.accent");
    // Applying an accent filter must NOT shrink the accent choices — that is how
    // an OR stays buildable. Matches sample 03 (no filter) vs 04 (filtered).
    expect(accentOf(filtered).counts).toEqual(accentOf(unfiltered).counts);
    expect(accentOf(unfiltered).counts).toEqual(
      accentOr.fields.find((f) => f.value === "persona.accent").counts,
    );
  });

  it("orders enum choices count-desc then name and drops unused properties", () => {
    const res = queryScenarioFixture({ limit: 25 });
    const accent = res.fields.find((f) => f.value === "persona.accent");
    expect(accent.choices).toEqual(["Canadian", "Australian", "Neutral", "American", "Indian"]);
    // A field the suite never varies (search that hits one row) drops enums with
    // no members; string fields (name) always remain.
    const narrow = queryScenarioFixture({ limit: 25, search: "payment sms" });
    expect(narrow.fields.some((f) => f.value === "name")).toBe(true);
  });
});

describe("amendScenarioFixture — drop", () => {
  const nameOf = (n) => SAMPLE_ROWS.find((r) => r.number === n).name;

  it("drops a scenario by name and the list no longer serves it", () => {
    const before = queryScenarioFixture({ limit: 25 }).count;
    const name = nameOf(4);
    const res = amendScenarioFixture({ changes: [{ op: "drop", scenario: name }] });

    expect(res.receipts).toEqual([{ scenario: name, outcome: "applied" }]);
    const after = queryScenarioFixture({ limit: 25 });
    expect(after.count).toBe(before - 1);
    expect(after.results.some((r) => r.name === name)).toBe(false);
  });

  it("bulk-drops an array of scenarios in one change, one receipt each", () => {
    const names = [nameOf(1), nameOf(2), nameOf(3)];
    const res = amendScenarioFixture({ changes: [{ op: "drop", scenarios: names }] });

    expect(res.receipts).toHaveLength(3);
    expect(res.receipts.every((r) => r.outcome === "applied")).toBe(true);
    expect(queryScenarioFixture({ limit: 25 }).count).toBe(SAMPLE_ROWS.length - 3);
  });

  it("resolves a number and does not renumber the survivors", () => {
    amendScenarioFixture({ changes: [{ op: "drop", scenario: "2" }] });
    const numbers = queryScenarioFixture({ limit: 25, group_by: "" }).results.map((r) => r.number);
    expect(numbers).not.toContain(2);
    // 1 and 3 survive with their original numbers — a drop leaves 1, 3, 4…
    expect(numbers).toContain(1);
    expect(numbers).toContain(3);
  });

  it("resolves a numeric range", () => {
    const res = amendScenarioFixture({ changes: [{ op: "drop", scenario: "1-3" }] });
    expect(res.receipts).toHaveLength(3);
    const numbers = queryScenarioFixture({ limit: 25, group_by: "" }).results.map((r) => r.number);
    expect(numbers).not.toContain(1);
    expect(numbers).not.toContain(2);
    expect(numbers).not.toContain(3);
  });

  it("refuses a token that matches no scenario", () => {
    const res = amendScenarioFixture({ changes: [{ op: "drop", scenario: "no_such_scenario" }] });
    expect(res.receipts[0]).toMatchObject({ scenario: "no_such_scenario", outcome: "refused" });
    expect(res.receipts[0].why).toMatch(/no scenario matches/);
  });
});

describe("amendScenarioFixture — set_field", () => {
  const nameOf = (n) => SAMPLE_ROWS.find((r) => r.number === n).name;

  it("applies an editable descriptive field (tests) immediately", () => {
    const name = nameOf(4);
    const res = amendScenarioFixture({
      changes: [{ op: "set_field", scenario: name, field: "tests", value: "Passes when it does the thing." }],
    });
    expect(res.receipts).toEqual([{ scenario: name, outcome: "applied" }]);
    const row = queryScenarioFixture({ limit: 25 }).results.find((r) => r.name === name);
    expect(row.tests).toBe("Passes when it does the thing.");
  });

  it("reworks a behavioural field (max_turns) when rework is true", () => {
    const name = nameOf(4);
    const res = amendScenarioFixture({
      rework: true,
      changes: [{ op: "set_field", scenario: name, field: "max_turns", value: 15 }],
    });
    expect(res.receipts).toEqual([{ scenario: name, outcome: "reworked" }]);
    const row = queryScenarioFixture({ limit: 25 }).results.find((r) => r.name === name);
    expect(row.max_turns).toBe(15);
  });

  it("refuses a behavioural field when rework is false", () => {
    const name = nameOf(4);
    const res = amendScenarioFixture({
      rework: false,
      changes: [{ op: "set_field", scenario: name, field: "background_noise", value: "cafe" }],
    });
    expect(res.receipts[0]).toMatchObject({ scenario: name, outcome: "refused" });
    expect(res.receipts[0].why).toMatch(/re-proof/);
  });

  it("refuses a non-editable field with a proved-not-described why", () => {
    const name = nameOf(4);
    const res = amendScenarioFixture({
      rework: true,
      changes: [{ op: "set_field", scenario: name, field: "branch", value: "anything" }],
    });
    expect(res.receipts[0]).toMatchObject({ scenario: name, outcome: "refused" });
    expect(res.receipts[0].why).toMatch(/not editable: it is proved, not described/);
  });
});

describe("amendScenarioFixture — set_persona", () => {
  const nameOf = (n) => SAMPLE_ROWS.find((r) => r.number === n).name;

  it("merges an editable persona field and reworks it", () => {
    const name = nameOf(4);
    const res = amendScenarioFixture({
      rework: true,
      changes: [{ op: "set_persona", scenario: name, persona: { accent: "Irish" } }],
    });
    expect(res.receipts).toEqual([{ scenario: name, outcome: "reworked" }]);
    const row = queryScenarioFixture({ limit: 25 }).results.find((r) => r.name === name);
    expect(row.persona.accent).toBe("Irish");
  });

  it("refuses a non-editable persona field (name)", () => {
    const name = nameOf(4);
    const res = amendScenarioFixture({
      rework: true,
      changes: [{ op: "set_persona", scenario: name, persona: { name: "Someone Else" } }],
    });
    expect(res.receipts[0]).toMatchObject({ scenario: name, outcome: "refused" });
    expect(res.receipts[0].why).toMatch(/not editable/);
  });
});

describe("coverageScenarioFixture", () => {
  it("reproduces the unfiltered coverage sample (07) exactly, zeros included", () => {
    expect(coverageScenarioFixture({})).toEqual(coverageSample);
  });

  it("reproduces the accent-filtered coverage sample (08)", () => {
    expect(coverageScenarioFixture({ "persona.accent": ["Canadian"] })).toEqual(
      coverageFilteredSample,
    );
  });

  it("keeps empty cross-tab cells as explicit zeros", () => {
    const cov = coverageScenarioFixture({});
    const zero = cov.cells.find((c) => c.row === "authenticate_otp" && c.column === "destructive");
    expect(zero).toEqual({ row: "authenticate_otp", column: "destructive", count: 0 });
  });

  it("honours row_axis / col_axis", () => {
    const cov = coverageScenarioFixture({ row_axis: "counterparty", col_axis: "interface" });
    expect(cov.row_axis).toBe("counterparty");
    expect(cov.col_axis).toBe("interface");
    expect(cov.rows).toEqual(cov.rows.slice().sort());
  });

  it("reshapes after an amend drop", () => {
    const before = coverageScenarioFixture({}).per_axis.find((a) => a.axis === "task").scenarios;
    amendScenarioFixture({ changes: [{ op: "drop", scenario: "1-5" }] });
    const after = coverageScenarioFixture({}).per_axis.find((a) => a.axis === "task").scenarios;
    expect(after).toBe(before - 5);
  });
});

describe("scenarios.js — live routes are in the generated contract", () => {
  // The three routes ship on the backend's feat/environment-v3 schema. Before
  // that schema was adopted apiPath() threw for all three, so the live Scenarios
  // tab could not issue a single request. These assert the surface carries them.
  it.each([
    ["/simulate/api/harness-jobs/{id}/scenarios/", "get"],
    ["/simulate/api/harness-jobs/{id}/scenarios/coverage/", "get"],
    ["/simulate/api/harness-jobs/{id}/scenarios/amend/", "post"],
  ])("registers %s (%s)", (template, method) => {
    expect(isContractedApiPath(template)).toBe(true);
    expect(getContractedApiMethods(template)).toContain(method);
  });

  it("resolves the coverage and amend path helpers without throwing", () => {
    expect(scenariosCoveragePath("job-1")).toBe(
      "/simulate/api/harness-jobs/job-1/scenarios/coverage/",
    );
    expect(scenariosAmendPath("job-1")).toBe(
      "/simulate/api/harness-jobs/job-1/scenarios/amend/",
    );
  });

  it("publishes every response field the scenarios tab reads", () => {
    const { definitions } = OPENAPI_CONTRACT;
    const keys = (name) => Object.keys(definitions[name].properties);
    expect(keys("HarnessScenarioListResponse")).toEqual(
      expect.arrayContaining([
        "results",
        "count",
        "groups",
        "group_by",
        "groupings",
        "fields",
        "scenario_editing",
        "level_labels",
      ]),
    );
    expect(keys("HarnessScenarioEditing")).toEqual(
      expect.arrayContaining([
        "editable_fields",
        "persona_fields",
        "persona_choices",
      ]),
    );
    expect(keys("HarnessScenarioCoverageResponse")).toEqual(
      expect.arrayContaining([
        "per_axis",
        "rows",
        "columns",
        "cells",
        "axis_labels",
        "level_labels",
      ]),
    );
    expect(keys("HarnessScenarioAmendResponse")).toEqual(["receipts"]);
  });

  it("caps the request contract's scenario_count at the UI's MAX_SCENARIOS", () => {
    expect(
      OPENAPI_CONTRACT.definitions.HarnessJobCreate.properties.scenario_count.maximum,
    ).toBe(MAX_SCENARIOS);
    expect(
      OPENAPI_CONTRACT.definitions.HarnessPreflight.properties.scenario_count.maximum,
    ).toBe(MAX_SCENARIOS);
  });
});
