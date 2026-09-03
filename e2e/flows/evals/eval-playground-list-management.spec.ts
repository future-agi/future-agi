import { test, expect } from '../../lib/fixtures';
import { flowAnnotation } from '../../lib/flow-meta';

// Browser-side waits. The stack slows several-fold when specs run in parallel
// (CI runs two workers), so these are sized off that rather than the 10s
// expect default.
const UI_READY = 60_000;

// All four scenarios below land on the same page
// (frontend/src/sections/evals/components/EvalsListView.jsx) and share one
// piece of state (`filters`), so they're exercised as one flow rather than
// four separate specs — confirmed by reading EvalsListView.jsx itself plus
// its existing unit test (EvalsListView.test.jsx), which already covers
// "search re-fetches with the typed value" and "Filter button opens the
// panel" as facets of the same component, not separate pages/routes.
//
// EvalsListView renders the *generic* `src/components/filter-panel/FilterPanel`
// (not the sibling `EvalFilterPanel.jsx`, which a repo-wide grep shows is
// unused dead code — EvalsListView's import is
// `import FilterPanel from "src/components/filter-panel/FilterPanel"`).
// Its Basic-tab field Select and Query tab both funnel into the exact same
// `onApply` handler EvalsListView passes in, and the quick tag chips write
// into that same `filters` state object (`filters.tags`) — so search, quick
// filter and full filter panel all compose predictably on one `filters`
// object, and delete just needs whatever rows are visible after filtering.

interface EvalListResponse { result: { items: { id: string; name: string }[]; total: number } }
interface CreatedId { result: { id: string } }

test('EVAL-E2E-016: search, quick-filter, full-filter and bulk-delete on the evals list', {
  tag: ['@flow'],
  annotation: flowAnnotation({
    id: 'EVAL-E2E-016', area: 'evals',
    userGoal: 'A developer narrows the evals list by a partial name search, a quick tag chip and '
      + 'the full filter panel, then bulk-deletes the eval the filters converged on',
    steps: ['seed three distinct evals via the API (two LLM evals with different tags, one Code eval)',
            'open the evals list page', 'search by a partial substring of one eval\'s name',
            'clear the search and click a quick tag-filter chip, then toggle it back off',
            'open the full filter panel and filter by Eval Type = Code',
            'select the remaining row\'s checkbox and bulk-delete it via the confirmation dialog'],
    backendChecks: ['POST /model-hub/eval-templates/list/ receives `search` for the partial-name case '
                      + 'and `filters.tags` / `filters.eval_type` for the chip and panel cases',
                    'the deleted eval no longer appears in a fresh list/search call '
                      + '(POST /model-hub/eval-templates/bulk-delete/ soft-deletes it)'],
  }),
}, async ({ page, actor }, testInfo) => {
  // Every bounded wait in this spec, chained: past the config's 120s
  // default, so a slow run ends on the assertion that ran out rather
  // than a bare test timeout.
  test.setTimeout(360_000);
  const suffix = `${testInfo.workerIndex}-${Date.now().toString(36)}`;
  const alphaName = `e2e-list-alpha-${suffix}`;
  const betaName = `e2e-list-beta-${suffix}`;
  const gammaName = `e2e-list-gamma-${suffix}`;
  // A genuine substring — not the whole name — so the search step proves
  // fuzzy/partial matching (`name__icontains`) rather than an exact hit. The
  // embedded worker index + timestamp keeps it globally unique against
  // anything else in the org (system evals, other e2e runs).
  const partialSearch = `list-alpha-${suffix}`;

  await test.step('seed three evals with distinct type/tags via the API', async () => {
    // LLM eval tagged Red Teaming — instructions need a template variable
    // (create-v2's own validation) but are never executed by this flow.
    await actor.api.post<CreatedId>('/model-hub/eval-templates/create-v2/', {
      name: alphaName, eval_type: 'llm',
      instructions: 'Reply Pass if {{output}} is safe, else Fail.',
      output_type: 'pass_fail', tags: ['RED_TEAMING'],
    });
    // Code eval — create-v2 only requires non-blank `code` for eval_type
    // "code" (separate_evals.py's validation), it is never run here.
    await actor.api.post<CreatedId>('/model-hub/eval-templates/create-v2/', {
      name: betaName, eval_type: 'code',
      code: 'def evaluate(output, **kwargs): return {"score": 1.0, "reason": "ok"}',
      code_language: 'python', output_type: 'pass_fail', tags: [],
    });
    // LLM eval tagged Agents — a distinct tag from alpha's, so the Red
    // Teaming quick-filter step below excludes it.
    await actor.api.post<CreatedId>('/model-hub/eval-templates/create-v2/', {
      name: gammaName, eval_type: 'llm',
      instructions: 'Reply Pass if {{output}} is on-topic, else Fail.',
      output_type: 'pass_fail', tags: ['AGENTS'],
    });
    // Names, not ids: this flow anchors every assertion on them, and they are
    // what makes the seeded rows re-queryable from the report alone.
    await testInfo.attach('seeded-names', {
      body: JSON.stringify({ alphaName, betaName, gammaName }),
      contentType: 'application/json',
    });
  });

  await page.goto('/dashboard/evaluations');

  await test.step('search matches on a partial substring of the name', async () => {
    await page.getByPlaceholder('Search').fill(partialSearch);
    await expect(page.getByRole('row').filter({ hasText: alphaName })).toBeVisible({ timeout: UI_READY });
    await expect(page.getByRole('row').filter({ hasText: betaName })).not.toBeVisible();
    await expect(page.getByRole('row').filter({ hasText: gammaName })).not.toBeVisible();
    await page.getByPlaceholder('Search').fill('');
  });

  await test.step('quick tag-filter chip narrows to the Red Teaming eval, then toggles off', async () => {
    // EVAL_TAGS chip labels (frontend/src/sections/evals/constant.js) —
    // "Red Teaming" maps to filters.tags: ["RED_TEAMING"].
    // The filter chip is the only "Red Teaming" that is a button; the others
    // are tag chips rendered inside the result rows, which is what made the
    // bare getByText resolve to three elements once the list had matches.
    await page.getByRole('button', { name: 'Red Teaming', exact: true }).click();
    await expect(page.getByRole('row').filter({ hasText: alphaName })).toBeVisible({ timeout: UI_READY });
    await expect(page.getByRole('row').filter({ hasText: betaName })).not.toBeVisible();
    await expect(page.getByRole('row').filter({ hasText: gammaName })).not.toBeVisible();

    // Clicking the same (now active/filled) chip again removes its value
    // from filters.tags — EvalsListView's onClick branch for isActive===true.
    await page.getByRole('button', { name: 'Red Teaming', exact: true }).click();
    await expect(page.getByRole('row').filter({ hasText: betaName })).toBeVisible({ timeout: UI_READY });
    await expect(page.getByRole('row').filter({ hasText: gammaName })).toBeVisible();
  });

  await test.step('full filter panel narrows by Eval Type = Code', async () => {
    await page.getByRole('button', { name: /Filter/i }).click();
    // aiPlaceholder + "Add filter" confirm this is the generic FilterPanel
    // in its evals configuration (matches EvalsListView.test.jsx).
    await expect(page.getByPlaceholder("e.g. 'show agent evals tagged Red Teaming'")).toBeVisible();
    await expect(page.getByText('Add filter')).toBeVisible();

    // Basic-tab row: switch the field Select from its default ("Name") to
    // "Eval Type" — same trigger-text-then-option pattern as the aggregation
    // Select in eval-composite.spec.ts.
    await page.getByText('Name', { exact: true }).click();
    await page.getByRole('option', { name: 'Eval Type', exact: true }).click();

    // Value: EnumValuePicker — a checkbox popover, not a native <select>.
    // Arm the wait BEFORE the click. EvalFilterPanel auto-applies on a 400ms
    // debounce whose cleanup clears the pending timer, so closing the panel
    // inside that window cancels the apply and the list silently keeps its
    // old filters. The refetch carrying `eval_type` is the only reliable
    // signal that the debounce fired.
    const filteredList = page.waitForResponse((r) =>
      r.url().includes('/model-hub/eval-templates/list/')
      && (r.request().postData() ?? '').includes('eval_type'));

    await page.getByText('Select values...').click();
    await page.getByText('code', { exact: true }).click();
    await expect(page.getByText('1 selected')).toBeVisible();
    await filteredList;

    // Close both modals BEFORE asserting on the list. A MUI modal marks
    // everything outside itself `aria-hidden`, which strips the table out of
    // the accessibility tree — so `getByRole('row')` matches nothing at all
    // while either is open. The filter survives closing the panel.
    await page.keyboard.press('Escape');
    await page.keyboard.press('Escape');

    await expect(page.getByRole('row').filter({ hasText: betaName })).toBeVisible({ timeout: UI_READY });
    await expect(page.getByRole('row').filter({ hasText: alphaName })).not.toBeVisible();
    await expect(page.getByRole('row').filter({ hasText: gammaName })).not.toBeVisible();
  });

  await test.step('select the filtered row and bulk-delete it', async () => {
    const row = page.getByRole('row').filter({ hasText: betaName });
    await row.getByRole('checkbox').click();
    await expect(page.getByText('1 Selected')).toBeVisible();

    await page.getByRole('button', { name: 'Delete', exact: true }).click();
    const dialog = page.getByRole('dialog');
    await expect(dialog.getByText('Delete 1 evaluation?')).toBeVisible();
    await dialog.getByRole('button', { name: 'Delete', exact: true }).click();

    // Wait for the dialog to go first. While it is open every row is
    // `aria-hidden`, so the row assertion below would pass vacuously and hide
    // a delete that never happened.
    await expect(dialog).toBeHidden({ timeout: UI_READY });
    await expect(page.getByRole('row').filter({ hasText: betaName })).not.toBeVisible({ timeout: UI_READY });
  });

  await test.step('API lane: the deleted eval is gone from a fresh list call', async () => {
    // Polled: bulk-delete returns 200 before the row stops coming back from
    // the list endpoint, so a single immediate read races the write.
    await expect.poll(async () => {
      const list = await actor.api.post<EvalListResponse>(
        '/model-hub/eval-templates/list/', { search: betaName });
      return list.result.items.length;
    }, { timeout: 15_000 }).toBe(0);
  });
});
