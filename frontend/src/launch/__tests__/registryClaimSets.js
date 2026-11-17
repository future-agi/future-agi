// Fixture claim-set injection for registry-705-claims.test.js (the contract
// itself lives in registryClaims.js). A claim set is
// `{claims: {SLOT: [{id, comment}]}, paths: {SLOT: [{id, comment}]}, flows,
// moduleOnly: {SLOT: [id]}, leak: [id], sourceExtra: [line]}`. It replaces
// every WORKS slot, so the real slots never leak into a case. `moduleOnly`
// puts claims in the module but not in the marked source; `leak` adds ids to
// the set the switch reads that no slot claims; `sourceExtra` appends lines
// to the source.
import { LEGACY_SLOTS, NEW_SLOTS } from "./registryClaims";

const KINDS = Object.freeze({ claims: "MADE_REAL", paths: "MADE_REAL_PATHS" });
const lineOf = ({ id, comment }) =>
  `    "${id}",${comment ? ` // ${comment}` : ""}`;

/** madeReal.js source with each WORKS block holding exactly the set's lines. */
export function sourceWithClaims(source, set) {
  const lines = source.split("\n");
  for (const kind of Object.keys(KINDS)) {
    for (const slot of NEW_SLOTS) {
      const begin = lines.findIndex((l) =>
        l.includes(`// BEGIN ${slot} ${kind} `),
      );
      const open = begin + 1;
      const close = lines.findIndex(
        (l, i) => i > open && /^\s*\]\),\s*$/.test(l),
      );
      if (
        begin < 0 ||
        close < 0 ||
        !lines[open].includes(`${slot}: Object.freeze([`)
      )
        throw new Error(`madeReal.js: no ${slot} ${kind} block to inject`);
      lines.splice(
        open + 1,
        close - open - 1,
        ...(set[kind]?.[slot] ?? []).map(lineOf),
      );
    }
  }
  return [...lines, ...(set.sourceExtra ?? [])].join("\n");
}

/** The madeReal module with the set's WORKS slots and the real legacy ones. */
export function moduleWithClaims(actual, set) {
  const slotsOf = (kind) =>
    Object.freeze(
      Object.fromEntries([
        ...LEGACY_SLOTS.map((slot) => [slot, actual[KINDS[kind]][slot]]),
        ...NEW_SLOTS.map((slot) => [
          slot,
          Object.freeze([
            ...(set[kind]?.[slot] ?? []).map((e) => e.id),
            ...((kind === "claims" && set.moduleOnly?.[slot]) || []),
          ]),
        ]),
      ]),
    );
  const MADE_REAL = slotsOf("claims");
  const MADE_REAL_PATHS = slotsOf("paths");
  return {
    MADE_REAL,
    MADE_REAL_PATHS,
    madeRealSurfaceIds: (slots = MADE_REAL) =>
      new Set([
        ...actual.madeRealSurfaceIds(slots),
        ...(slots === MADE_REAL ? set.leak ?? [] : []),
      ]),
    madeRealPathTemplates: (slots = MADE_REAL_PATHS) =>
      actual.madeRealPathTemplates(slots),
  };
}

/** e2e/FLOWS.md with the set's walk headings appended. */
export const flowsWithWalks = (flows, set) =>
  [flows, ...(set.flows ?? [])].join("\n");
