import type { Page } from '@playwright/test';

// frontend/src/sections/gateway/ at LOCATOR_SOURCE_REF (scope.ts).
// Catalogue identity/search/opt-in bindings already live in
// J31/catalogue-selectors.ts. Consumers should reuse those, not duplicate them.
export const modelDetailLocators = {
  // models/detail/ModelDetailPage.jsx:24,220-229: named CustomTabs + TAB_LABELS. The caller
  // must first assert the exact admitted model ID (not merely a page heading).
  views: (page: Page) => page.getByRole('tablist', { name: 'Model views', exact: true }),
  overview: (page: Page) => page.getByRole('tablist', { name: 'Model views', exact: true })
    .getByRole('tab', { name: 'Overview', exact: true }),
  tryIt: (page: Page) => page.getByRole('tablist', { name: 'Model views', exact: true })
    .getByRole('tab', { name: 'Try it', exact: true }),
  // models/detail/ModelOverview.jsx:88-122 -> Section.jsx:15-17 is a named
  // section. Exposes the actual price display; no asserted per-image or
  // per-generation unit is invented. Caller compares the entire exact text
  // against the admitted feed/model, preserving missing/extra-unit failures.
  pricing: (page: Page) => page.getByRole('region', { name: 'Pricing', exact: true }),
  // models/detail/ModelTryIt.jsx:251-258 refuses every non-chat type.
  // This is an absence-boundary locator, not an image/audio output locator.
  chatOnlyNotice: (page: Page) => page.getByText('Try it runs chat models', { exact: true }),
};

// OWED F18-P04 / F04: ModelTryIt Answer:69-90 renders only text. There is no
// native image result, audio player, clip/pro result identity or Retry action
// here (Run:381 is not a native-media Retry). J31/A07/A16 stay OWED; do not
// substitute a chat answer, generic <img> logo, catalog-load Retry, API-only
// media bytes or a browser mock response for the required render/play path.
