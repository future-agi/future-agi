import { describe, expect, it } from "vitest";
import {
  literalSurfaceReads,
  resolveWiringSites,
} from "./registryWiringAdditions";

const FILE =
  "sections/develop-detail/DevelopBarRightSection/DevelopDataRightSection.jsx";
const NEW_FILE = "sections/develop/AdditionalGate.jsx";
const P0 = ["dataset.schema", "dataset.savedViews"];
const SITES = [[FILE, P0]];
const EXPECTED = [...P0, "dataset.export"];
const entry = (overrides = {}) => ({
  file: FILE,
  lane: "T-GRID",
  feature: "F47",
  expected: EXPECTED,
  ...overrides,
});
const source = (ids) => ids.map((id) => `isSurfaceShown("${id}")`).join(";\n");

// Also run with these rejections promoted to acceptance in a cloud scratch
// copy: every case must turn red, not just pass due to an unrelated exception.
const invalid = [
  [
    "P0 literal dropped",
    [entry({ expected: ["dataset.schema", "dataset.export"] })],
    "p0-order",
  ],
  [
    "P0 order changed",
    [
      entry({
        expected: ["dataset.savedViews", "dataset.schema", "dataset.export"],
      }),
    ],
    "p0-order",
  ],
  [
    "unknown id",
    [entry({ expected: [...P0, "dataset.notRegistered"] })],
    "unknown-id",
  ],
  [
    "retired id",
    [entry({ expected: [...P0, "dataset.collab"] })],
    "retired-id",
  ],
  [
    "Results id",
    [entry({ expected: [...P0, "exp.results.matrix"] })],
    "results-id",
  ],
  [
    "unknown Results id",
    [entry({ expected: [...P0, "exp.results.future"] })],
    "results-id",
  ],
  ["unknown file without newSite", [entry({ file: NEW_FILE })], "unknown-site"],
  ["newSite on a P0 file", [entry({ newSite: true })], "existing-site"],
  [
    "nonboolean newSite",
    [entry({ file: NEW_FILE, newSite: "true" })],
    "invalid-new-site",
  ],
  [
    "later entry drops earlier addition",
    [
      entry(),
      entry({
        lane: "T-GRIDB",
        feature: "F52",
        expected: [...P0, "dataset.splits"],
      }),
    ],
    "history-order",
  ],
  [
    "later entry reorders earlier addition",
    [entry(), entry({ expected: ["dataset.export", ...P0] })],
    "history-order",
  ],
  [
    "later new site entry drops earlier addition",
    [
      entry({ file: NEW_FILE, newSite: true }),
      entry({ file: NEW_FILE, newSite: true, expected: P0 }),
    ],
    "history-order",
  ],
  [
    "invalid earlier entry hidden by a valid latest entry",
    [entry({ expected: [...P0, "dataset.notRegistered"] }), entry()],
    "unknown-id",
  ],
  ["not a list", {}, "not-list"],
  ["null entry", [null], "not-entry"],
  ["array entry", [[]], "not-entry"],
  ["missing lane", [entry({ lane: undefined })], "invalid-lane"],
  ["blank feature", [entry({ feature: " " })], "invalid-feature"],
  ["unknown field", [entry({ active: true })], "unknown-field"],
  [
    "expected not a list",
    [entry({ expected: "dataset.export" })],
    "invalid-expected",
  ],
  [
    "expected non-string",
    [entry({ expected: [...P0, null] })],
    "invalid-expected",
  ],
  [
    "path traversal",
    [entry({ file: "../AdditionalGate.jsx", newSite: true })],
    "invalid-file",
  ],
  [
    "absolute path",
    [entry({ file: "/AdditionalGate.jsx", newSite: true })],
    "invalid-file",
  ],
  [
    "aliased path",
    [entry({ file: "sections/./AdditionalGate.jsx", newSite: true })],
    "invalid-file",
  ],
  [
    "backslash path",
    [entry({ file: "sections\\AdditionalGate.jsx", newSite: true })],
    "invalid-file",
  ],
];

describe("append-only wiring additions", () => {
  it("preserves the P0 pins when there are no declarations", () => {
    expect(resolveWiringSites(SITES, [])).toEqual(SITES);
  });

  it("accepts F47 without editing the P0 literal list", () => {
    const resolved = resolveWiringSites(SITES, [entry()]);
    expect(resolved).toEqual([[FILE, EXPECTED]]);
    expect(literalSurfaceReads(source(EXPECTED))).toEqual(resolved[0][1]);
    expect(SITES).toEqual([[FILE, ["dataset.schema", "dataset.savedViews"]]]);
  });

  it("allows registered literals before, between and after P0 occurrences", () => {
    const expected = [
      "dataset.export",
      P0[0],
      "dataset.splits",
      P0[1],
      "dataset.export",
    ];
    expect(resolveWiringSites(SITES, [entry({ expected })])).toEqual([
      [FILE, expected],
    ]);
  });

  it("allows a declared lane id before it is in the supplied registry", () => {
    const expected = [...P0, "dataset.laneExtra"];
    expect(
      resolveWiringSites(SITES, [entry({ expected })], {
        laneDeclarations: [{ id: "dataset.laneExtra" }],
      }),
    ).toEqual([[FILE, expected]]);
  });

  it("never lets a lane declaration admit retired or Results ids", () => {
    for (const [id, code] of [
      ["dataset.collab", "retired-id"],
      ["exp.results.extra", "results-id"],
    ]) {
      expect(() =>
        resolveWiringSites(SITES, [entry({ expected: [...P0, id] })], {
          laneDeclarations: [{ id }],
        }),
      ).toThrow(code);
    }
  });

  it("selects only the latest cumulative entry for each file", () => {
    const expected = [...EXPECTED, "dataset.splits"];
    const entries = [
      entry(),
      entry({ lane: "T-GRIDB", feature: "F52", expected }),
    ];
    expect(resolveWiringSites(SITES, entries)).toEqual([[FILE, expected]]);
    expect(entries[0].expected).toEqual(EXPECTED);
  });

  it("adds a separate newSite pin and preserves it across history", () => {
    const entries = [entry({ file: NEW_FILE, newSite: true }), entry()];
    const expected = [...EXPECTED, "dataset.splits"];
    entries.push(
      entry({
        file: NEW_FILE,
        newSite: true,
        lane: "T-GRIDB",
        feature: "F52",
        expected,
      }),
    );
    expect(resolveWiringSites(SITES, entries)).toEqual([
      [FILE, EXPECTED],
      [NEW_FILE, expected],
    ]);
  });

  it("preserves repeated P0 occurrences, not only unique ids", () => {
    const repeated = [[FILE, [P0[0], P0[1], P0[0]]]];
    expect(() => resolveWiringSites(repeated, [entry()])).toThrow("p0-order");
    const expected = [...P0, "dataset.export", P0[0]];
    expect(resolveWiringSites(repeated, [entry({ expected })])).toEqual([
      [FILE, expected],
    ]);
  });

  it("preserves repeated lane occurrences in later entries", () => {
    const earlier = entry({ expected: [...EXPECTED, "dataset.export"] });
    expect(() => resolveWiringSites(SITES, [earlier, entry()])).toThrow(
      "history-order",
    );
  });

  it.each(invalid)("rejects %s", (_name, entries, code) => {
    expect(() => resolveWiringSites(SITES, entries)).toThrow(code);
  });

  it("detects a source literal missing from the active entry list", () => {
    const resolved = resolveWiringSites(SITES, [entry()]);
    expect(
      literalSurfaceReads(source([...EXPECTED, "dataset.splits"])),
    ).not.toEqual(resolved[0][1]);
  });

  it("keeps source order, repetition and both quote styles", () => {
    expect(
      literalSurfaceReads(`isSurfaceShown('dataset.schema');
      isSurfaceShown( "dataset.savedViews" ); isSurfaceShown("dataset.schema")`),
    ).toEqual([P0[0], P0[1], P0[0]]);
  });
});
