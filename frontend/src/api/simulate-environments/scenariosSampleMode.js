// Dev-only sample mode for the Scenarios tab.
//
// With `?scnSample=1` in the URL, the list / coverage / amend calls in
// scenarios.js serve the in-repo captured 20-row suite from the fixtures
// emulator (scenariosFixtures) instead of the live endpoints — so the tab can
// be browsed, filtered, grouped, paged and edited before the backend is
// reachable. Off by default; it never affects the live path.
export function isScenarioSampleMode() {
  try {
    return new URLSearchParams(window.location.search).has("scnSample");
  } catch {
    // No window (SSR/tests) — sample mode is a browser-only dev switch.
    return false;
  }
}

// A smaller page in sample mode so the 20-row suite spans several pages and the
// pager is actually exercisable (the live default is 25 — one page for 20 rows).
export const SAMPLE_PAGE_SIZE = 5;
