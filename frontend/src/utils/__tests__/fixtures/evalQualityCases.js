// Shared inputs for NK20's real tooltip callbacks and summary contract.
export const countKeys = [
  "count",
  "scored_count",
  "unscored_count",
  "completed_count",
  "error_count",
  "pending_count",
  "running_count",
  "skipped_count",
  "failed_count",
];
export const qualityPoint = (overrides = {}) => ({
  ...Object.fromEntries(countKeys.map((key) => [key, 0])),
  value: null,
  ...overrides,
});
export const errorPoint = qualityPoint({
  count: 1,
  unscored_count: 1,
  error_count: 1,
});
export const zeroPoint = qualityPoint({
  count: 1,
  scored_count: 1,
  completed_count: 1,
  failed_count: 1,
  value: 0,
});
const without = (point, key) => {
  const copy = { ...point };
  delete copy[key];
  return copy;
};
export const validationCases = [
  { id: "MISSING-SCORED", point: without(qualityPoint(), "scored_count") },
  { id: "CONTRADICTORY-ZERO", point: qualityPoint({ scored_count: 1 }) },
  ...countKeys.flatMap((key) => {
    const base = qualityPoint({ count: 1, unscored_count: 1 });
    return [
      { id: `MISSING-${key.toUpperCase()}`, point: without(base, key) },
      ...[
        ["STRING", "1"],
        ["NEGATIVE", -1],
        ["FRACTIONAL", 0.5],
        ["NULL", null],
        ["UNSAFE", Number.MAX_SAFE_INTEGER + 1],
      ].map(([kind, value]) => ({
        id: `${kind}-${key.toUpperCase()}`,
        point: { ...base, [key]: value },
      })),
    ];
  }),
  { id: "PARTITION", point: qualityPoint({ count: 1 }) },
  {
    id: "SCORED-EXCEEDS-COMPLETED",
    point: qualityPoint({ count: 1, scored_count: 1 }),
  },
  {
    id: "FAILURES-EXCEED-SCORED",
    point: qualityPoint({ count: 1, unscored_count: 1, failed_count: 1 }),
  },
  {
    id: "LIFECYCLE-EXCEEDS-ATTEMPTS",
    point: qualityPoint({
      count: 1,
      unscored_count: 1,
      pending_count: 1,
      running_count: 1,
    }),
  },
];
export const nonfiniteCases = countKeys.flatMap((key) =>
  [NaN, Infinity, -Infinity].map((value) => ({
    id: `${key}-${String(value)}`,
    point: qualityPoint({ [key]: value }),
  })),
);
export const invalidCases = [...validationCases, ...nonfiniteCases];

// Exact expected strings are copied from the reviewed NK20 row cases.
export const exactSummaryCases = [
  {
    id: "EMPTY-BUCKETS",
    isEvaluation: true,
    expectedExact: "No data",
    input: [qualityPoint({}), qualityPoint({})],
  },
  {
    id: "ONE-ERROR",
    isEvaluation: true,
    expectedExact:
      "Unscored · 1 execution error (100.0%) · 0 quality failures · 0 pending · 0 running · 0 skipped · 1 without a quality score",
    input: [qualityPoint({ count: 1, error_count: 1, unscored_count: 1 })],
  },
  {
    id: "TWO-ERRORS",
    isEvaluation: true,
    expectedExact:
      "Unscored · 2 execution errors (100.0%) · 0 quality failures · 0 pending · 0 running · 0 skipped · 2 without a quality score",
    input: [qualityPoint({ count: 2, error_count: 2, unscored_count: 2 })],
  },
  {
    id: "ZERO-SCORE",
    isEvaluation: true,
    expectedExact:
      "1 scored · 0 execution errors (0.0%) · 1 quality failure · 0 pending · 0 running · 0 skipped · 0 without a quality score",
    input: [
      qualityPoint({
        completed_count: 1,
        count: 1,
        failed_count: 1,
        scored_count: 1,
        value: 0,
      }),
    ],
  },
  {
    id: "NONTERMINAL",
    isEvaluation: true,
    expectedExact:
      "Unscored · 0 execution errors · 0 quality failures · 1 pending · 1 running · 1 skipped · 3 without a quality score",
    input: [
      qualityPoint({
        count: 3,
        pending_count: 1,
        running_count: 1,
        skipped_count: 1,
        unscored_count: 3,
      }),
    ],
  },
  {
    id: "UNKNOWN",
    isEvaluation: true,
    expectedExact:
      "Unscored · 0 execution errors · 0 quality failures · 0 pending · 0 running · 0 skipped · 1 without a quality score",
    input: [qualityPoint({ count: 1, unscored_count: 1 })],
  },
  {
    id: "COMPLETED-NONNUMERIC",
    isEvaluation: true,
    expectedExact:
      "Unscored · 0 execution errors (0.0%) · 0 quality failures · 0 pending · 0 running · 0 skipped · 1 without a quality score",
    input: [qualityPoint({ completed_count: 1, count: 1, unscored_count: 1 })],
  },
  {
    id: "THIRDS",
    isEvaluation: true,
    expectedExact:
      "1 scored · 1 execution error (33.3%) · 1 quality failure · 1 pending · 1 running · 1 skipped · 5 without a quality score",
    input: [
      qualityPoint({
        completed_count: 2,
        count: 6,
        error_count: 1,
        failed_count: 1,
        pending_count: 1,
        running_count: 1,
        scored_count: 1,
        skipped_count: 1,
        unscored_count: 5,
      }),
    ],
  },
  {
    id: "WEIGHTED-RATE",
    isEvaluation: true,
    expectedExact:
      "2 scored · 2 execution errors (50.0%) · 0 quality failures · 0 pending · 0 running · 0 skipped · 2 without a quality score",
    input: [
      qualityPoint({
        completed_count: 2,
        count: 3,
        error_count: 1,
        scored_count: 2,
        unscored_count: 1,
      }),
      qualityPoint({ count: 1, error_count: 1, unscored_count: 1 }),
    ],
  },
  {
    id: "TWO-QUALITY-FAILURES",
    isEvaluation: true,
    expectedExact:
      "2 scored · 0 execution errors (0.0%) · 2 quality failures · 0 pending · 0 running · 0 skipped · 0 without a quality score",
    input: [
      qualityPoint({
        completed_count: 2,
        count: 2,
        failed_count: 2,
        scored_count: 2,
        value: 0,
      }),
    ],
  },
  {
    id: "NONQUALITY",
    isEvaluation: false,
    expectedExact: "",
    input: [{ value: 0 }],
  },
  {
    id: "UNTYPED-EMPTY-ARRAY",
    isEvaluation: null,
    expectedExact: "",
    input: [],
  },
  {
    id: "EXPLICIT-EVAL-EMPTY-ARRAY",
    isEvaluation: true,
    expectedExact: "No data",
    input: [],
  },
  {
    id: "MISSING-EVAL-POPULATION",
    isEvaluation: true,
    expectedExact: "Unavailable",
    input: null,
  },
  {
    id: "ALL-METADATA-MISSING",
    isEvaluation: true,
    expectedExact: "Unavailable",
    input: [{ value: null }],
  },
  {
    id: "MALFORMED-POSITIVE-NOT-FILTERED",
    isEvaluation: true,
    expectedExact: "Unavailable",
    input: [
      qualityPoint({}),
      without(qualityPoint({ count: 1, unscored_count: 1 }), "scored_count"),
    ],
  },
  {
    id: "MALFORMED-POSITIVE-FIRST",
    isEvaluation: true,
    expectedExact: "Unavailable",
    input: [
      without(qualityPoint({ count: 1, unscored_count: 1 }), "scored_count"),
      qualityPoint({}),
    ],
  },
  {
    id: "UNSAFE-AGGREGATE",
    isEvaluation: true,
    expectedExact: "Unavailable",
    input: [
      qualityPoint({
        count: 9007199254740991,
        unscored_count: 9007199254740991,
      }),
      qualityPoint({ count: 1, unscored_count: 1 }),
    ],
  },
  ...validationCases.map(({ id, point }) => ({
    id: `INVALID-${id}`,
    input: [point],
    isEvaluation: true,
    expectedExact: "Unavailable",
  })),
];
