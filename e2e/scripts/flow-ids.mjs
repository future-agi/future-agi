// Flow ids and areas read from spec source text, without Playwright. A flow id
// is a whole quoted string literal ('OBS-E2E-001'), which also covers specs
// that build several flows from a table of ids; an area is an `area:` literal.
// e2e-coverage reads them at the PR's base and head to prove `E2E: new <ID>`,
// and `yarn catalog:check` fails if this reading ever disagrees with the flows
// Playwright lists.
import { existsSync, readdirSync, readFileSync } from "node:fs";
import path from "node:path";

export const FLOW_ID = /['"]([A-Z]+-E2E-\d{3})['"]/g;
const FLOW_AREA = /\barea:\s*['"]([^'"\n]+)['"]/g;

const sortedUnique = (values) => [...new Set(values)].sort();

// `texts` are spec sources (or `git grep` output lines) from e2e/flows only.
export function flowIdsAndAreas(texts) {
  const ids = [];
  const areas = [];
  for (const text of texts) {
    for (const m of text.matchAll(FLOW_ID)) ids.push(m[1]);
    for (const m of text.matchAll(FLOW_AREA)) areas.push(m[1]);
  }
  return { ids: sortedUnique(ids), areas: sortedUnique(areas) };
}

export function readFlowSpecTexts(e2eDir) {
  const texts = [];
  const walk = (dir) => {
    for (const ent of readdirSync(dir, { withFileTypes: true })) {
      const p = path.join(dir, ent.name);
      if (ent.isDirectory()) walk(p);
      else if (ent.name.endsWith(".spec.ts"))
        texts.push(readFileSync(p, "utf8"));
    }
  };
  const flows = path.join(e2eDir, "flows");
  if (existsSync(flows)) walk(flows);
  return texts;
}
