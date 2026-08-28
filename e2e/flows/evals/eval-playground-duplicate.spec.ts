import { test, expect } from '../../lib/fixtures';
import { flowAnnotation } from '../../lib/flow-meta';
import { JUDGE_MODEL, ensureJudgeModel, fillTestData } from '../../lib/eval-model';

// Routes the judge model through the mock LLM behind the real gateway — same
// setup as eval-create.spec.ts / eval-playground.spec.ts.

interface CreatedId { result: { id: string } }
interface EvalDetailResponse {
  result: { id: string; name: string; instructions: string; model: string; eval_type: string };
}

// Where the real "Duplicate" trigger lives, and why this isn't the drawer
// dialog a repo-wide grep for "duplicate-eval-template" first turns up:
//
// frontend/src/sections/common/EvaluationDrawer/DuplicateEvals.jsx is a
// named-copy *dialog* (asks for a new name, then POSTs), but it's wired up
// solely from EvalsActions.jsx (line ~188), which only renders inside the
// shared EvaluationDrawer's card grid (attaching evals to a dataset/task/
// experiment) — not on the standalone eval detail page this flow uses.
//
// frontend/src/sections/evals/components/EvalDetailPage.jsx has its own,
// simpler "Duplicate" — a MenuItem in the "..." header menu (line ~1261)
// that calls `useDuplicateEval` directly with no naming dialog at all: it
// names the copy itself via `buildCopyName` (frontend/src/sections/evals/
// hooks/useEvalDetail.js, `${baseName}_copy_${dd-MM-yyyy_HH-mm-ss}`) and
// fires the same POST /model-hub/duplicate-eval-template/ on click. That's
// the trigger this flow drives.
//
// The "..." trigger itself (IconButton wrapping only
// <Iconify icon="solar:menu-dots-bold" />) has no accessible name — Iconify
// renders every icon with aria-hidden:true (node_modules/@iconify/react
// svgDefaults), and MUI's IconButton adds none of its own — so it can't be
// targeted by role name like every other button in this suite. It's
// targeted instead as "the first button in <main> with no text label",
// which is deterministic here: reading EvalDetailPage.jsx's JSX confirms
// this header (and its menu button) is the first thing in the page's own
// return, ahead of the only other unlabeled, always-mounted button on this
// page — MessageEditor.jsx's Falcon-AI trigger, which lives inside the
// lower details panel and so renders later in document order. The
// post-click assertion that the Duplicate/Delete menu actually opened
// (rather than the Falcon AI prompt bar) is the safety net if that ordering
// assumption is ever wrong.
test('EVAL-E2E-019: duplicate an eval from its detail page and verify the copy runs independently', {
  tag: ['@flow'],
  annotation: flowAnnotation({
    id: 'EVAL-E2E-019', area: 'evals',
    userGoal: 'A developer duplicates an existing eval from its detail page to get an '
      + 'independent, editable copy with the same configuration',
    steps: ['open the source eval\'s detail page', 'open the "..." menu and click Duplicate',
            'land on the new copy\'s own detail page', 'confirm the copy carries the same '
              + 'instructions/model/type as the source', 'test the copy against custom input '
              + 'and read the Pass verdict', 'confirm the original eval is untouched'],
    backendChecks: ['POST /model-hub/duplicate-eval-template/ deep-copies every EvalTemplate field '
                      + 'except id/name/timestamps onto a brand-new row (DuplicateEvalTemplateView)',
                    'the copy gets an auto-generated "<name>_copy_<timestamp>" name '
                      + '(useDuplicateEval\'s buildCopyName) distinct from the source',
                    'the copy is a fully independent EvalTemplate — running it hits the mock LLM '
                      + 'through the gateway on its own, not by aliasing the source row',
                    'the source eval\'s own name and id are unchanged after duplicating'],
  }),
}, async ({ page, actor }, testInfo) => {
  const suffix = `${testInfo.workerIndex}-${Date.now().toString(36)}`;
  const evalName = `e2e-dup-source-${suffix}`;
  // Exists nowhere else, so finding it in the copy's test result proves the
  // copy's own config (not the source's) is what actually ran.
  const verdict = `e2e-verdict-${suffix}`;

  await ensureJudgeModel(actor);

  // Created directly via the API — this flow is about duplication, not
  // authoring (eval-create.spec.ts already covers that UI in full), so the
  // source eval is set up the same lightweight way eval-playground.spec.ts
  // sets up the eval it drives from the UI.
  const source = await actor.api.post<CreatedId>('/model-hub/eval-templates/create-v2/', {
    name: evalName, eval_type: 'llm',
    instructions: `Reply with exactly this JSON: {"result": "Pass", "explanation": "${verdict} saw {{output}}"}`,
    model: JUDGE_MODEL, output_type: 'pass_fail', pass_threshold: 0.5,
  });
  const sourceId = source.result.id;

  let copyId = '';

  await test.step('UI: open the source eval and duplicate it from the "..." menu', async () => {
    await page.goto(`/dashboard/evaluations/${sourceId}`);
    await expect(page.getByText(evalName)).toBeVisible({ timeout: 15_000 });

    const menuButton = page.locator('main button').filter({ hasNotText: /\S/ }).first();
    await menuButton.click();
    // Confirms the right popup opened before committing to the click below —
    // see the top-of-file note on why this button can't be targeted by name.
    await expect(page.getByRole('menuitem').filter({ hasText: 'Duplicate' })).toBeVisible();

    // Match the row's TEXT, not its accessible name. EvalDetailPage.jsx wraps
    // this MenuItem in a <CustomTooltip title="Create an editable copy of this
    // eval"> (:1282), and that title becomes the item's accessible name — so
    // `getByRole('menuitem', { name: 'Duplicate' })` matches nothing at all.
    await page.getByRole('menuitem').filter({ hasText: 'Duplicate' }).click();
    await expect(page.getByText('Evaluation duplicated')).toBeVisible({ timeout: 15_000 });
  });

  await test.step('UI: lands on the new copy\'s own detail page', async () => {
    // Already sitting on /dashboard/evaluations/<sourceId>, which matches the
    // same shape as the post-duplicate URL — wait for the id itself to
    // change, not just for the route pattern to match.
    await page.waitForURL(
      (url) => /\/dashboard\/evaluations\/[^/]+$/.test(url.pathname) && !url.pathname.endsWith(sourceId),
      { timeout: 15_000 },
    );
    // Pathname, not the raw URL: EvalDetailPage appends `?v=<version>` after
    // it loads (:417), and whether that has landed yet is a race — so this
    // read passes or captures "<id>?v=1" depending on timing.
    copyId = new URL(page.url()).pathname.split('/dashboard/evaluations/')[1];
    expect(copyId).toMatch(/.+/);
    expect(copyId).not.toBe(sourceId);
    await expect(page.getByText(`${evalName}_copy_`)).toBeVisible({ timeout: 15_000 });
  });

  await test.step('API lane: the copy is a deep, independent clone', async () => {
    const copy = await actor.api.get<EvalDetailResponse>(`/model-hub/eval-templates/${copyId}/detail/`);
    expect(copy.result.name).toMatch(new RegExp(`^${evalName}_copy_\\d{2}-\\d{2}-\\d{4}_\\d{2}-\\d{2}-\\d{2}$`));
    expect(copy.result.eval_type).toBe('llm');
    expect(copy.result.model).toBe(JUDGE_MODEL);
    expect(copy.result.instructions).toContain(verdict);

    const original = await actor.api.get<EvalDetailResponse>(`/model-hub/eval-templates/${sourceId}/detail/`);
    expect(original.result.name).toBe(evalName);
  });

  await test.step('UI: test the copy against custom input — it runs on its own', async () => {
    await fillTestData(page, '{"output": "world"}');
    await page.getByRole('button', { name: 'Test Evaluation' }).click();
    await expect(page.getByRole('button', { name: 'Test Evaluation' })).toBeVisible({ timeout: 30_000 });
    await expect(page.getByText('Pass', { exact: true })).toBeVisible();
    await expect(page.getByText(`${verdict} saw world`)).toBeVisible();
  });
});
