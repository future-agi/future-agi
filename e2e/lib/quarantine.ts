import { readFileSync } from 'node:fs';
import { fileURLToPath } from 'node:url';
import path from 'node:path';
import { mcpSubfeatureExclusionPattern } from '../stack/mcp-subfeature-selection.mjs';
import { walkGatewayProfile, NEW_ORG_DEFAULT_OFF_TAG, NEW_ORG_DEFAULT_TAG, newOrgDefaultDeclared } from './profile';

interface QuarantineEntry {
  // Matched as a substring of the composed test title — use the flow id (e.g. `AUTH-E2E-001`).
  id: string; reason: string; owner: string; issue?: string; added: string; expires: string;
}

const FILE = path.join(path.dirname(fileURLToPath(import.meta.url)), '..', '.quarantine.json');
const escape = (s: string) => s.replace(/[.*+?^${}()|[\]\\]/g, '\\$&');

// @live-llm is folded in here rather than passed via CLI --grep-invert: Playwright's CLI
// --grep-invert overrides the config's grepInvert entirely, so a CI-side flag would silently
// un-quarantine everything. Every exclusion has to come out of this one function.
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

  // A flow tagged @gw-profile-<name> runs only on a stack started with that
  // gateway profile (lib/profile.ts): every other profile's tag is left out. An
  // unknown E2E_GATEWAY_PROFILE throws here, at config load. The flow catalog
  // lists every profile's flows (E2E_ALL_GATEWAY_PROFILES=1, scripts/flow-catalog.mjs).
  if (!process.env.E2E_ALL_GATEWAY_PROFILES) {
    patterns.push(mcpSubfeatureExclusionPattern().source);
    patterns.push(`@gw-profile-(?!${escape(walkGatewayProfile())}(?![\\w-]))[\\w-]+`);
    // A flow tagged @new-org-default runs only on a stack started with the new-org
    // default declared on (E2E_MANAGED_MODELS_ON_FOR_NEW_ORGS=1, lib/profile.ts),
    // and a flow tagged @new-org-default-off, which needs a new org to start with
    // Future AGI models off, never runs on one.
    if (!newOrgDefaultDeclared()) patterns.push(`${escape(NEW_ORG_DEFAULT_TAG)}(?![\\w-])`);
    else patterns.push(`${escape(NEW_ORG_DEFAULT_OFF_TAG)}(?![\\w-])`);
  }

  return patterns.length ? new RegExp(patterns.join('|')) : undefined;
}
