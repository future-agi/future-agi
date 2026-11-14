// The application-verification headline, in one sentence a responder can act on:
// "v2 passed all 3 cases and fixes Damaged mug refund, which fails on production v1. No regressions."
// Everything is read from the run the server returned. A regression count is stated only when the
// server reported one; a repair is called verified only when the server says so.
export const CHECKS = ['selection', 'delivery', 'tools', 'answer'];

const cellsFor = (run, caseId, variant) => (run.cells || []).filter((cell) => cell.caseId === caseId && cell.variant === variant);
const allPassed = (cells) => cells.length > 0 && cells.every((cell) => cell.status === 'completed' && CHECKS.every((key) => cell[key] === 'pass'));
const anyFailed = (cells) => cells.some((cell) => CHECKS.some((key) => cell[key] === 'fail'));
const plural = (count, one, many = `${one}s`) => `${count} ${count === 1 ? one : many}`;
const list = (items) => (items.length < 2 ? items.join('') : `${items.slice(0, -1).join(', ')} and ${items.at(-1)}`);

/** Case ids the candidate passed on every check, in run order. */
export function passedCaseIds(run) {
  return (run.cases || []).map((item) => item.id).filter((id) => allPassed(cellsFor(run, id, 'candidate')));
}

/** The comparison arm a fix is named against: production, else the recorded reference (plain runs only). */
function fixBaseline(run) {
  const production = (run.arms || []).find((arm) => arm.variant === 'production');
  if (production) return { variant: 'production', name: `production v${production.version}` };
  const reference = (run.arms || []).find((arm) => arm.variant === 'reference');
  const reproduction = run.result?.reproduction && run.result.reproduction !== 'not_requested';
  return reference && !reproduction ? { variant: 'reference', name: `recorded v${reference.version}` } : null;
}

export function applicationConclusion(run) {
  const candidate = (run.arms || []).find((arm) => arm.variant === 'candidate');
  const reference = (run.arms || []).find((arm) => arm.variant === 'reference');
  const ids = (run.cases || []).map((item) => item.id);
  const titles = Object.fromEntries((run.cases || []).map((item) => [item.id, item.title || 'Reviewed case']));
  const { result, decision } = run;
  if (!result) return decision?.state === 'release_ready' ? 'Application checks passed for these cases.' : 'Hold this release.';
  const passed = passedCaseIds(run);
  const total = ids.length;
  const lead = passed.length === total && total > 1 ? `v${candidate?.version} passed all ${total} cases` : `v${candidate?.version} passed ${passed.length} of ${plural(total, 'case')}`;
  const baseline = fixBaseline(run);
  const fixed = baseline ? passed.filter((id) => anyFailed(cellsFor(run, id, baseline.variant))).map((id) => titles[id]) : [];
  const fixes = fixed.length ? ` and fixes ${list(fixed)}, which ${fixed.length === 1 ? 'fails' : 'fail'} on ${baseline.name}` : '';
  const regressed = Number.isInteger(result.regressed_cases) ? result.regressed_cases : null;
  const regressions = regressed === null ? '' : regressed === 0 ? ' No regressions.' : ` ${plural(regressed, 'case')} regressed.`;
  if (!result.reproduction || result.reproduction === 'not_requested') return `${lead}${fixes}.${regressions}`;
  const reproduction = result.reproduction === 'reproduced'
    ? `the recorded failure reproduced${reference ? ` on v${reference.version}` : ''}`
    : `the recorded failure ${result.reproduction === 'not_reproduced' ? 'did not reproduce' : 'was not verified'}`;
  return `${result.repair_verified ? 'Repair verified' : 'Repair not verified'}: ${lead}${fixes}; ${reproduction}.${regressions}`;
}

/** "1 case tested (3 others not selected)" or "3 cases tested". */
export function coverageLine(tested, total) {
  const others = Math.max(0, total - tested);
  return `${plural(tested, 'case')} tested${others ? ` (${others} other${others === 1 ? '' : 's'} not selected)` : ''}`;
}
