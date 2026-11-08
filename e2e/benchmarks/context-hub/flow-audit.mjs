import { readFile, writeFile } from 'node:fs/promises';
import { pathToFileURL } from 'node:url';
import { JOBS } from './jobs.mjs';
import { requireValue, sha256 } from '../../lib/skills-r3/canonical.mjs';

const FAMILIES = { CH: 'flows:1', 'LS-S': 'flows:2', LF: 'flows:4', O: 'flows:5' };

/** Documentation rows are candidate routes, never a measured complete-job denominator. */
export function extractDocumentedBaselines(raw) {
  const audit = JSON.parse(raw);
  const flows = [...new Set(JOBS.flatMap((j) => j.audit_refs))].map((ref) => {
    const match = /^(LS-S|CH|LF|O)(\d+)$/.exec(ref);
    requireValue(match, 'Unknown audit alias');
    const family = FAMILIES[match[1]];
    const index = Number(match[2]) - 1;
    const entry = audit[family]?.flows[index];
    requireValue(entry && typeof entry.name === 'string' && Array.isArray(entry.competitor_steps) && entry.competitor_steps.length > 0, `Missing audited flow ${ref}`);
    requireValue(entry.competitor_steps.every((s) => typeof s === 'string' && s.length > 0), `Malformed steps in ${ref}`);
    const citations = entry.sources.filter((source) => typeof source === 'string' && /^https:\/\//.test(source));
    requireValue(citations.length > 0, `Missing public source for ${ref}`);
    return { ref, name: entry.name, source_pointer: `/${family}/flows/${index}`,
      documented_step_count: entry.competitor_steps.length, steps: entry.competitor_steps, citations,
      evidence_kind: 'documented_not_exercised', measured_operator_actions: null };
  });
  return { schema: 1, audit_date: '2026-11-07', source_file: 'flows-and-agent-contexts.json', source_sha256: sha256(raw),
    methodology: 'Count each competitor_steps array entry once, without splitting combined steps or summing overlapping/alternative routes. These are documentation bullets, not operator actions or authenticated transactions.',
    comparator_validation: 'not_conducted',
    jobs: JOBS.map((j) => ({ job: j.id, candidate_flow_refs: j.audit_refs,
      best_applicable_measured_actions: null, strongest_configured_route: 'requires_independent_customer_workflow_review' })), flows };
}

if (process.argv[1] && import.meta.url === pathToFileURL(process.argv[1]).href) {
  const [input, output] = process.argv.slice(2);
  requireValue(input && output, 'Usage: node flow-audit.mjs AUDIT.json OUTPUT.json');
  const document = extractDocumentedBaselines(await readFile(input, 'utf8'));
  await writeFile(output, `${JSON.stringify(document, null, 2)}\n`, { flag: 'wx', mode: 0o600 });
}
