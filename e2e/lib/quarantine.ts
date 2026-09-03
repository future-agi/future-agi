import { readFileSync } from 'node:fs';
import { fileURLToPath } from 'node:url';
import path from 'node:path';

interface QuarantineEntry {
  // Matched as a substring of the composed test title — use the flow id (e.g. `AUTH-E2E-001`).
  id: string; reason: string; owner: string; issue?: string; added: string; expires: string;
}

const FILE = path.join(path.dirname(fileURLToPath(import.meta.url)), '..', '.quarantine.json');
const escape = (s: string) => s.replace(/[.*+?^${}()|[\]\\]/g, '\\$&');

// @live-llm is folded in here rather than passed via CLI --grep-invert: Playwright's CLI
// --grep-invert overrides the config's grepInvert entirely, so a CI-side flag would silently
// un-quarantine everything. Both exclusions have to come out of this one function.
export function grepInvertPattern(): RegExp | undefined {
  const patterns: string[] = [];

  if (!process.env.E2E_INCLUDE_QUARANTINED) {
    let entries: QuarantineEntry[] = [];
    try {
      entries = JSON.parse(readFileSync(FILE, 'utf8')) as QuarantineEntry[];
    } catch (err) {
      // Fail open, mirroring the backend's quarantine loader: a broken/unparseable
      // quarantine file must not be able to take out the entire suite's listing.
      console.warn(`quarantine: failed to read/parse ${FILE}: ${(err as Error).message}`);
      entries = [];
    }
    const today = new Date().toISOString().slice(0, 10);
    const active = entries.filter(e => e.expires >= today);   // expired entries run again (and fail loudly)
    if (active.length) patterns.push(...active.map(e => escape(e.id)));
  }

  if (!process.env.E2E_LIVE_LLM) patterns.push('@live-llm');

  return patterns.length ? new RegExp(patterns.join('|')) : undefined;
}
