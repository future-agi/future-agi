/**
 * Step 8, capabilities and disclosure facts (SPEC §4.2 row 8, §6.7): what the reviewer checks the
 * disclosure against, and a `disclosure-gap` flag for each part the draft leaves empty ("None.")
 * while the scan observed something for it.
 */
import { flag } from "./findings.mjs";

export const OBSERVED_FOR_PART = {
  network: ["hosts", "vendors", "rawNetwork"],
  files: ["filesOutside", "writesCode"],
  shell: ["shell"],
  background: ["keepsRunning", "workers"],
  credentials: ["keys", "envEnumeration"],
};
const EMPTY = /^\s*(none\.?)?\s*$/i;

export function observedFacts(facts, manifest) {
  const live = Array.isArray(manifest.models?.live) ? manifest.models.live : [];
  const keys = Array.isArray(manifest.keys) ? manifest.keys : [];
  const uniq = (list) => [...new Set(list)].sort();
  return {
    hosts: uniq(facts.hosts),
    vendors: uniq(live.map((item) => String(item?.model ?? "").split(":")[0]).filter(Boolean)),
    shell: uniq(facts.shell),
    workers: uniq(facts.workers),
    rawNetwork: uniq(facts.rawNetwork),
    filesOutside: uniq(facts.filesOutside),
    envEnumeration: uniq(facts.envEnumeration),
    writesCode: uniq(facts.writesCode),
    keepsRunning: uniq(facts.keepsRunning),
    keys: uniq(keys.map((key) => key?.name).filter(Boolean)),
  };
}

export function disclosureGaps(disclosure, observed) {
  if (!disclosure || typeof disclosure !== "object") return [];
  const out = [];
  for (const [part, keys] of Object.entries(OBSERVED_FOR_PART)) {
    const facts = keys.flatMap((key) => observed[key] ?? []);
    const value = disclosure[part];
    if (facts.length > 0 && (typeof value !== "string" || EMPTY.test(value))) {
      out.push(flag("capabilities", "disclosure-gap", "omega.hub.json", `disclosure.${part} says nothing, but the checks observed: ${facts.slice(0, 6).join(", ")}`, { fix: `describe it in disclosure.${part}` }));
    }
  }
  return out;
}

/** The Use this flags (SPEC §7.4). `consoleTemplate` is false in v2. */
export function capabilities(entry, manifest) {
  let openInStudio = false;
  const raw = entry.get("omega.studio.json");
  if (raw && ["agent", "team", "app", "workflow"].includes(manifest.kind)) {
    try {
      const studio = JSON.parse(raw.toString("utf8"));
      const modules = Array.isArray(studio.entries) ? studio.entries : Object.values(studio.entries ?? {});
      const paths = modules.map((item) => (typeof item === "string" ? item : item?.module ?? item?.path)).filter(Boolean);
      openInStudio = paths.length > 0 && paths.every((rel) => entry.has(String(rel).replace(/^\.\//, "")));
    } catch {
      openInStudio = false;
    }
  }
  return { cli: true, openInStudio, deploy: openInStudio, consoleTemplate: false };
}
