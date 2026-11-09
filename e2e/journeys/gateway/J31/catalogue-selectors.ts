import type { Page } from '@playwright/test';
import { GatewayGridReader } from '../_shared/selectors';

// All product pins are relative to this repo at foundation head
// 7543e2d995fe58b620745aa81ce1a76c8855ef0e, not to an unmerged CAT2 UI lane.
// routes/paths.js:paths.dashboard.gateway.models.
export const MODEL_CATALOGUE_PATH = '/dashboard/gateway/models';

// sections/gateway/models/modelLinks.js:modelPath splits only the FIRST slash.
// Exact admitted model IDs must be supplied by the H5 fixture owner. No launch
// IDs or router aliases are synthesized in this selector module.
export function modelDetailPath(id: string): string {
  const slash = id.indexOf('/');
  if (id.length > 255 || slash <= 0 || slash === id.length - 1 || /[\s\u0000-\u001f]/.test(id)) {
    throw new Error('J31 model detail needs an exact provider/model ID');
  }
  return `${MODEL_CATALOGUE_PATH}/${encodeURIComponent(id.slice(0, slash))}/${encodeURIComponent(id.slice(slash + 1))}`;
}

export const catalogueSelectors = {
  // ModelCatalogPage.jsx:GatewayFilterBar searchPlaceholder; the shared
  // GatewayFilterBar.jsx passes it as both placeholder and aria-label.
  search: (page: Page) => page.getByRole('textbox', { name: 'Search models', exact: true }),
  // ManagedModelsCard.jsx at 3c20e189ea supersedes the foundation pin above:
  // only enabled orgs have this switch; OFF has no console opt-in controls.
  // Exact matching excludes the catalog's "Future AGI credits (<count>)" facet.
  creditsToggle: (page: Page) => page.getByLabel('Future AGI credits', { exact: true }),
  // ModelCatalogPage.jsx: catalog.error and statusFailed GatewayEmptyState arms.
  // This is a CATALOG read error, not the A16 modality-request error/Retry proof.
  loadError: (page: Page) => page.getByText("Couldn't load the model catalog", { exact: true }),
  // ModelCatalogPage.jsx:no-results arm. A product read failure is NOT no-results.
  noMatches: (page: Page) => page.getByText('No models match', { exact: true }),
  // models/catalog/cells.jsx:ModelCell -> RouterLink href=modelPath(data.id).
  modelLink: (page: Page, id: string) => GatewayGridReader.in(page).root
    .locator(`a[href=${JSON.stringify(modelDetailPath(id))}]`),
  // ModelDetailPage.jsx:CustomTabs aria-label + TAB_LABELS; the exact model ID
  // still has to be asserted by the calling step before reading any detail.
  overview: (page: Page) => page.getByRole('tablist', { name: 'Model views', exact: true })
    .getByRole('tab', { name: 'Overview', exact: true }),
  tryIt: (page: Page) => page.getByRole('tablist', { name: 'Model views', exact: true })
    .getByRole('tab', { name: 'Try it', exact: true }),
};

/** Read only a completely rendered, already-loaded grid. This helper never
 * mistakes a virtualized subset for the whole launch set. A future accepted
 * invocation may use a pinned filter/small cohort or a separately reviewed
 * traversal; changing page size or using a count as the expected set is invalid.
 * Row identity pin: ModelCatalogPage.jsx:getRowId=(p)=>p.data.id; row container
 * and display ordering pin: e2e/lib/gateway-grid.ts:rows/column.
 */
export async function readFullyRenderedCatalogueIds(page: Page): Promise<string[]> {
  const grid = GatewayGridReader.in(page);
  const total = await grid.displayedRowCount();
  const rows = await grid.rows.evaluateAll(elements => elements.map(element => ({
    id: element.getAttribute('row-id'), index: Number(element.getAttribute('row-index')),
  })));
  if (!Number.isSafeInteger(total) || total <= 0 || rows.length !== total ||
      rows.some(row => !row.id || !Number.isSafeInteger(row.index)) ||
      new Set(rows.map(row => row.index)).size !== total) {
    throw new Error('NOT_WALKED J31: complete rendered catalogue is unavailable; no subset pass');
  }
  rows.sort((a, b) => a.index - b.index);
  if (rows.some((row, index) => row.index !== index)) {
    throw new Error('NOT_WALKED J31: catalogue display inventory is incomplete');
  }
  return rows.map(row => row.id as string); // intentionally retains duplicate IDs
}

// No invented image/audio testid or generic Retry selector. ModelTryIt.jsx
// explicitly restricts this surface to chat and renders text-only Answer.
// A07/A16 need a product-owned image/clip/pro render/play path and a genuine
// local backend-error/recovery hook (AUTHOR.json), not browser route.fulfill.
