import { describe, expect, it } from "vitest";
import {
  defaultSamplingPolicy,
  samplingCoordinationErrors,
  samplingPolicyForApi,
  samplingRuleErrors,
  withCoordination,
} from "./samplingPolicyForm";
import {
  fitSamplingWindow,
  isSimpleSamplingPolicy,
  rowCount,
  samplingContextError,
  samplingMatcherValue,
  samplingPreviewGroups,
  samplingLocalDay,
  samplingPopulationChange,
  samplingPreviewShortfall,
  samplingPreviewWork,
  samplingRunChoice,
  samplingShortfallText,
  simpleGroupsForValues,
  simpleSamplingPolicy,
  simpleSelectionText,
  unitCount,
} from "./simpleSamplingPolicy";
const values = {
  rowType: "spans",
  runType: "historical",
  startDate: "2026-10-01T00:00:00.000Z",
  endDate: "2026-10-02T00:00:00.000Z",
  samplingRate: 50,
};

describe("opaque advanced settings and unchanged rule validation", () => {
  it.each(["min_n", "max_n", "rare_below"])(
    "retains zero %s and rejects blanks, fractions, negatives and excessive values",
    (key) => {
      expect(samplingRuleErrors({ target_n: 10, [key]: 0 })).toEqual({});
      for (const bad of ["", -1, 0.5, 1000001, Infinity, NaN])
        expect(samplingRuleErrors({ target_n: 10, [key]: bad })).toHaveProperty(
          key,
        );
      const policy = {
        ...defaultSamplingPolicy(),
        default: { target_n: 10, [key]: 0 },
      };
      expect(isSimpleSamplingPolicy(policy)).toBe(false);
      expect(samplingPolicyForApi(policy)).toEqual(policy);
    },
  );
  it("does not permit a minimum greater than its maximum", () => {
    expect(
      samplingRuleErrors({ target_n: 10, min_n: 2, max_n: 1 }),
    ).toHaveProperty("min_n");
  });
  it("keeps independent policies key-free and validates explicit project coordination without mutating the input", () => {
    const original = simpleSamplingPolicy(values);
    const coordinated = {
      ...withCoordination(original, "project"),
      panel: "q4",
      prn_start: 12.5,
    };
    expect(samplingCoordinationErrors(coordinated)).toEqual({});
    expect(isSimpleSamplingPolicy(coordinated)).toBe(false);
    expect(withCoordination(coordinated, "independent")).toEqual(original);
    expect(original).not.toHaveProperty("coordination");
    expect(samplingPolicyForApi(coordinated)).toEqual(coordinated);
    for (const bad of [-1, 100, 1.0000001, "", NaN])
      expect(
        samplingCoordinationErrors({ ...coordinated, prn_start: bad }),
      ).toHaveProperty("prn_start");
    for (const bad of ["!", " ", "a".repeat(65)])
      expect(
        samplingCoordinationErrors({ ...coordinated, panel: bad }),
      ).toHaveProperty("panel");
  });
  it("recognizes a normalized server policy without dropping its default filters or independent setting", () => {
    const normalized = {
      ...simpleSamplingPolicy(values),
      default: { target_n: 100, filters: [] },
      coordination: "independent",
    };
    expect(isSimpleSamplingPolicy(normalized)).toBe(true);
    expect(samplingPolicyForApi(normalized)).toEqual(normalized);
    expect(
      isSimpleSamplingPolicy({
        ...normalized,
        default: { ...normalized.default, filters: {} },
      }),
    ).toBe(false);
    expect(isSimpleSamplingPolicy({ ...normalized, group_by: {} })).toBe(false);
  });
  it("keeps typed values and disjoint first-match assignment, existing order, rule names and zero quotas", () => {
    const field = { source: "metadata", key: "contact.language" };
    const policy = simpleSamplingPolicy(values);
    policy.strata = simpleGroupsForValues(policy, field, [
      "1",
      2,
      false,
      "French, English",
    ]);
    policy.strata[0].target_n = 0;
    const before = structuredClone(policy);
    expect(isSimpleSamplingPolicy(policy)).toBe(true);
    expect(
      simpleGroupsForValues(policy, field, ["1", 2, false, "French, English"]),
    ).toEqual(before.strata);
    expect(policy).toEqual(before);
    const repeated = {
      ...policy,
      strata: [...policy.strata, { ...policy.strata[0], name: "duplicate" }],
    };
    expect(isSimpleSamplingPolicy(repeated)).toBe(false);
  });
  it.each([
    [42, "42"],
    [false, "false"],
    [0.00001, "1e-05"],
  ])(
    "keeps matcher-alias groups %j read-only instead of hiding first-match precedence",
    (numeric, text) => {
      expect(samplingMatcherValue(numeric)).toBe(samplingMatcherValue(text));
      const policy = simpleSamplingPolicy(values);
      policy.strata = simpleGroupsForValues(
        policy,
        { source: "span", key: "locale" },
        [numeric, text],
      );
      const before = structuredClone(policy);
      expect(isSimpleSamplingPolicy(policy)).toBe(false);
      expect(samplingPolicyForApi(policy)).toEqual(before);
      expect(policy).toEqual(before);
    },
  );
  it("blocks incompatible rows, future/invalid windows and live historical schedules without rewriting a policy", () => {
    const policy = simpleSamplingPolicy(values),
      before = structuredClone(policy);
    expect(
      samplingContextError({
        ...values,
        rowType: "sessions",
        samplingPolicy: { ...policy, sampling_unit: "span" },
      }),
    ).toMatch(/cannot be finer/);
    const live = {
      ...policy,
      mode: "live",
      default: { rate: 10 },
      window: { start: values.startDate, end: null },
    };
    expect(samplingContextError({ ...values, samplingPolicy: live })).toMatch(
      /New incoming data schedule/,
    );
    expect(
      samplingContextError({
        ...values,
        samplingPolicy: {
          ...policy,
          window: { start: "", end: values.endDate },
        },
      }),
    ).toMatch(/valid past sampling window/);
    expect(
      samplingContextError({
        ...values,
        samplingPolicy: { ...live, window: { start: null, end: null } },
        runType: "continuous",
      }),
    ).toMatch(/valid past sampling window/);
    expect(policy).toEqual(before);
  });
});

describe("group-picking window from the task's own time range", () => {
  const now = Date.parse("2026-11-11T12:00:00.000Z");
  it("keeps a past range of at most 31 days as it is", () => {
    expect(
      fitSamplingWindow(
        "2026-10-01T00:00:00.000Z",
        "2026-10-02T00:00:00.000Z",
        now,
      ),
    ).toEqual({
      window: {
        start: "2026-10-01T00:00:00.000Z",
        end: "2026-10-02T00:00:00.000Z",
      },
      shortened: false,
    });
  });
  it("ends a rolling range at now instead of its future midnight, without shortening it", () => {
    expect(
      fitSamplingWindow(
        "2026-11-04T12:00:00.000Z",
        "2026-11-12T00:00:00.000Z",
        now,
      ),
    ).toEqual({
      window: {
        start: "2026-11-04T12:00:00.000Z",
        end: "2026-11-11T11:59:59.000Z",
      },
      shortened: false,
    });
  });
  it("keeps the latest 31 days of a longer range and says it shortened it", () => {
    expect(
      fitSamplingWindow(
        "2025-11-11T12:00:00.000Z",
        "2026-11-12T00:00:00.000Z",
        now,
      ),
    ).toEqual({
      window: {
        start: "2026-10-11T11:59:59.000Z",
        end: "2026-11-11T11:59:59.000Z",
      },
      shortened: true,
    });
  });
  it("finds nothing to pick from in a future or unreadable range", () => {
    expect(
      fitSamplingWindow(
        "2026-11-12T00:00:00.000Z",
        "2026-11-13T00:00:00.000Z",
        now,
      ),
    ).toBeNull();
    expect(fitSamplingWindow("", "2026-11-01T00:00:00.000Z", now)).toBeNull();
    expect(fitSamplingWindow("soon", "later", now)).toBeNull();
  });
  it("builds the policy window from the task range, falling back only when nothing is past", () => {
    const year = simpleSamplingPolicy({
      ...values,
      startDate: "2025-10-01T00:00:00.000Z",
      endDate: "2026-10-01T00:00:00.000Z",
    });
    expect(year.window).toEqual({
      start: "2026-08-31T00:00:00.000Z",
      end: "2026-10-01T00:00:00.000Z",
    });
    const future = simpleSamplingPolicy({
      ...values,
      startDate: new Date(Date.now() + 86400000).toISOString(),
      endDate: new Date(Date.now() + 2 * 86400000).toISOString(),
    });
    expect(Date.parse(future.window.end)).toBeLessThanOrEqual(Date.now());
    expect(
      Date.parse(future.window.end) - Date.parse(future.window.start),
    ).toBeLessThanOrEqual(86400000);
  });
});

describe("plain selection wording", () => {
  const closed = (unit, target = 10) => ({
    ...simpleSamplingPolicy(values),
    sampling_unit: unit,
    default: { target_n: target },
  });
  it("says what is picked and what is evaluated, without calling a conversation a group", () => {
    expect(simpleSelectionText(closed("session"), "spans")).toBe(
      "Up to 10 conversations, picked at random; every matching span in a picked conversation is evaluated.",
    );
    expect(simpleSelectionText(closed("span", 1000), "spans")).toBe(
      "Up to 1,000 spans, picked at random.",
    );
  });
  it("counts in singular and plural", () => {
    expect(unitCount(1, "session")).toBe("1 conversation");
    expect(unitCount(0, "attribute")).toBe("0 custom IDs");
    expect(rowCount(1, "spans")).toBe("1 span");
    expect(rowCount(2, "voiceCalls")).toBe("2 voice calls");
  });
});

describe("preview per group", () => {
  const cell = (name, eligible, selected, rows) => ({
    stratum: JSON.stringify([name, []]),
    eligible_units: eligible,
    selected_units: selected,
    selected_rows: rows,
  });
  const policy = {
    ...simpleSamplingPolicy(values),
    default: { target_n: 100 },
    strata: simpleGroupsForValues(
      { ...simpleSamplingPolicy(values), strata: [] },
      { source: "span", key: "region" },
      ["North America", "Europe"],
    ),
  };
  it("matches each cell to its rule, keeps rules with no members, and reports the shortfall", () => {
    const groups = samplingPreviewGroups(
      { strata: [cell("Group 1", 8, 8, 80), cell("default", 400, 100, 900)] },
      policy,
    );
    expect(groups).toEqual([
      {
        key: "Group 1",
        label: "North America",
        requested: 50,
        eligible: 8,
        selected: 8,
        rows: 80,
      },
      {
        key: "Group 2",
        label: "Europe",
        requested: 50,
        eligible: 0,
        selected: 0,
        rows: 0,
      },
      {
        key: "default",
        label: "All other conversations",
        requested: 100,
        eligible: 400,
        selected: 100,
        rows: 900,
      },
    ]);
    expect(samplingPreviewShortfall(groups, policy)).toEqual({
      requested: 200,
      selected: 108,
      short: [groups[0], groups[1]],
    });
  });
  it("names the short groups instead of saying some groups", () => {
    const group = (label, requested, selected) => ({
      key: label,
      label,
      requested,
      eligible: selected,
      selected,
      rows: 0,
    });
    expect(
      samplingShortfallText(
        { requested: 25, selected: 23, short: [group("Europe", 10, 8)] },
        "session",
      ),
    ).toBe(
      "Europe has 8 of the 10 requested, so 23 of 25 conversations can be picked. Widen the window or lower its count.",
    );
    expect(
      samplingShortfallText(
        {
          requested: 35,
          selected: 23,
          short: [group("Europe", 10, 8), group("Asia Pacific", 5, 0)],
        },
        "session",
      ),
    ).toBe(
      "Europe (8 of 10) and Asia Pacific (0 of 5) have fewer than requested, so 23 of 35 conversations can be picked. Widen the window or lower those counts.",
    );
    expect(
      samplingShortfallText(
        {
          requested: 50,
          selected: 10,
          short: ["A", "B", "C", "D", "E"].map((name) => group(name, 10, 2)),
        },
        "user",
      ),
    ).toBe(
      "A (2 of 10), B (2 of 10), C (2 of 10) and 2 more groups have fewer than requested, so 10 of 50 users can be picked. Widen the window or lower those counts.",
    );
  });
  it("states what the picks came from and what they cost, without the headline counts", () => {
    const preview = (evaluators) => ({
      evaluator_count: evaluators,
      strata: [cell("Group 1", 8, 8, 128), cell("default", 400, 15, 240)],
    });
    expect(samplingPreviewWork(preview(2), policy, "spans")).toBe(
      "Picked from 408 matching conversations. Each span runs 2 evaluators: 736 runs.",
    );
    expect(samplingPreviewWork(preview(0), policy, "spans")).toBe(
      "Picked from 408 matching conversations. Add evaluators to estimate runs.",
    );
    expect(
      samplingPreviewWork(
        { evaluator_count: 1, strata: [cell("default", 1, 1, 1)] },
        policy,
        "traces",
      ),
    ).toBe(
      "Picked from 1 matching conversation. Each trace runs 1 evaluator: 1 run.",
    );
    expect(
      samplingPreviewWork(
        {
          evaluator_count: 1,
          strata: [{ ...cell("default", 1, 1, 1), eligible_units: null }],
        },
        policy,
        "spans",
      ),
    ).toBeNull();
  });
  it("reports no shortfall when every count is met, or for new incoming shares", () => {
    const met = samplingPreviewGroups(
      {
        strata: [
          cell("Group 1", 60, 50, 1),
          cell("Group 2", 60, 50, 1),
          cell("default", 400, 100, 1),
        ],
      },
      policy,
    );
    expect(samplingPreviewShortfall(met, policy)).toBeNull();
    expect(samplingPreviewShortfall(met, { ...policy, mode: "live" })).toBe(
      null,
    );
  });
  it("gives up rather than guessing when cells cannot be read rule by rule", () => {
    expect(
      samplingPreviewGroups({ strata: [cell("Unknown", 1, 1, 1)] }, policy),
    ).toBeNull();
    expect(
      samplingPreviewGroups(
        { strata: [{ ...cell("default", 1, 1, 1), selected_rows: null }] },
        policy,
      ),
    ).toBeNull();
    expect(
      samplingPreviewGroups(
        {
          strata: [{ eligible_units: 1, selected_units: 1, selected_rows: 1 }],
        },
        policy,
      ),
    ).toBeNull();
    expect(
      samplingPreviewGroups(
        { strata: [cell("default", 1, 1, 1)] },
        { ...policy, coordination: "project" },
      ),
    ).toBeNull();
  });
});

describe("Re-run choice for sampled tasks", () => {
  const saved = { ...simpleSamplingPolicy(values), default: { target_n: 10 } };
  const filters = {
    project_id: "p1",
    filters: [
      {
        column_id: "region",
        filter_config: {
          filter_type: "text",
          filter_op: "equals",
          filter_value: "Europe",
        },
      },
    ],
  };
  const population = (overrides = {}) => ({
    policy: saved,
    filters,
    project: "p1",
    rowType: "spans",
    ...overrides,
  });
  it("sees no change in an untouched sampled task", () => {
    expect(
      samplingPopulationChange(population(), structuredClone(population())),
    ).toBeNull();
    // The legacy task window never makes a population look edited.
    expect(
      samplingPopulationChange(
        population(),
        population({
          filters: {
            ...filters,
            date_range: [
              "2026-10-01T00:00:00.000Z",
              "2026-10-02T00:00:00.000Z",
            ],
            date_preset: "custom",
          },
        }),
      ),
    ).toBeNull();
    // The API compares numeric predicates after coercion; so does this.
    const numeric = {
      ...saved,
      strata: [
        {
          name: "Group 1",
          filters: [
            {
              source: "span",
              key: "llm.token_count.total",
              op: "gte",
              values: [5],
            },
          ],
          target_n: 3,
        },
      ],
    };
    expect(
      samplingPopulationChange(
        population({ policy: numeric }),
        population({
          policy: {
            ...numeric,
            strata: [
              {
                ...numeric.strata[0],
                filters: [{ ...numeric.strata[0].filters[0], values: ["5"] }],
              },
            ],
          },
        }),
      ),
    ).toBeNull();
  });
  it("names what changed: sampling, filters, both, project or row type", () => {
    const counts = { ...saved, default: { target_n: 20 } };
    const narrower = {
      ...filters,
      filters: [
        {
          ...filters.filters[0],
          filter_config: {
            ...filters.filters[0].filter_config,
            filter_value: "Asia",
          },
        },
      ],
    };
    expect(
      samplingPopulationChange(population(), population({ policy: counts })),
    ).toBe("the sampling");
    expect(
      samplingPopulationChange(population(), population({ filters: narrower })),
    ).toBe("the filters");
    expect(
      samplingPopulationChange(
        population(),
        population({ policy: counts, filters: narrower }),
      ),
    ).toBe("the sampling and the filters");
    expect(
      samplingPopulationChange(population(), population({ policy: null })),
    ).toBe("the sampling");
    expect(
      samplingPopulationChange(population(), population({ rowType: "traces" })),
    ).toBe("the project or row type");
  });
  it("leaves tasks that never sample to the API's other rules", () => {
    expect(
      samplingPopulationChange(
        population({ policy: null }),
        population({ policy: null, filters: {} }),
      ),
    ).toBeNull();
  });
  it("keeps the generic dialog for tasks without a frozen sample", () => {
    expect(samplingRunChoice(null, null)).toBeNull();
    expect(samplingRunChoice({ ...saved, mode: "live" }, null)).toBeNull();
  });
  it("says both options keep the frozen picks of an unchanged historical sample", () => {
    expect(samplingRunChoice(saved, null)).toEqual({
      content: "Choose what happens to the current sample.",
      options: {
        fresh_run: {
          description:
            "Removes the current results and evaluates the same picked conversations again.",
        },
        edit_rerun: {
          description:
            "Keeps the conversations already picked and their results, and evaluates only what is missing.",
        },
      },
    });
  });
  it("disables the option the API refuses once the population changed", () => {
    expect(samplingRunChoice(saved, "the filters")).toEqual({
      content:
        "You changed the filters, so the current sample no longer applies.",
      options: {
        fresh_run: {
          description:
            "Removes the current results and picks a new sample with your changes.",
        },
        edit_rerun: {
          disabled: true,
          description: "Not available: you changed the filters.",
        },
      },
    });
    // A task that only now starts sampling has no sample to keep either.
    expect(samplingRunChoice(null, "the sampling").options.edit_rerun).toEqual({
      disabled: true,
      description: "Not available: you changed the sampling.",
    });
  });
});

describe("one-line sampling dates", () => {
  it("uses the window's local date format without the time", () => {
    const value = "2026-11-02T12:00:00.000Z";
    expect(samplingLocalDay(value)).toBe(
      new Intl.DateTimeFormat(undefined, {
        year: "numeric",
        month: "short",
        day: "numeric",
      }).format(new Date(value)),
    );
    expect(samplingLocalDay("")).toBe("Not set");
  });
});
