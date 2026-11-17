import { describe, expect, it } from "vitest";

import { lineOf, sources } from "./source-scan";

const PLAIN_MUI_TABS =
  /import\s+Tabs\s+from\s+"@mui\/material\/Tabs"|import\s*\{[^}]*\bTabs\b[^}]*\}\s*from\s*"@mui\/material"/g;

// The gateway drew three tab styles. Navigation tabs are
// Future AGI's CustomTabs (src/components/tabs/tabs: purple 14 px, thin
// indicator), as on Guardrails, MCP, Providers and Gateway settings. A
// segmented control is only for switching a value (group-by, code language).
describe("gateway tabs", () => {
  it("no gateway page draws plain MUI Tabs", () => {
    const offenders = [];
    for (const [file, text] of sources(["sections/gateway"])) {
      for (const match of text.matchAll(PLAIN_MUI_TABS)) {
        offenders.push(`${file}:${lineOf(text, match.index)}`);
      }
    }
    expect(offenders).toEqual([]);
  });

  const all = Object.fromEntries(sources(["sections/gateway"]));
  it.each([
    ["Model detail", "sections/gateway/models/detail/ModelDetailPage.jsx"],
    ["Analytics", "sections/gateway/analytics/AnalyticsDashboardSection.jsx"],
    ["Alerts", "sections/gateway/monitoring/AlertsSection.jsx"],
    ["Log drawer", "sections/gateway/logs/RequestDetailDrawer.jsx"],
  ])("the %s tab list renders CustomTabs", (_page, file) => {
    const text = all[file];
    expect(text).toMatch(
      /import\s*\{[^}]*\bCustomTabs\b[^}]*\}\s*from\s*"src\/components\/tabs\/tabs"/,
    );
    expect(text).toMatch(/<CustomTabs\b/);
  });

  // The guardrail analytics range picks a value, as the Analytics page's
  // range does: the same control (analytics/RangeToggle, a toggle group),
  // not a second strip of tabs under the Guardrails tabs.
  it("guardrail analytics picks its range with the Analytics range control", () => {
    const guardrails =
      all["sections/gateway/guardrails/GuardrailAnalyticsTab.jsx"];
    const analytics =
      all["sections/gateway/analytics/AnalyticsDashboardSection.jsx"];
    expect(guardrails).not.toMatch(/SegmentedTabs|<Tabs\b|<CustomTabs\b/);
    expect(guardrails).toMatch(/<RangeToggle\b/);
    expect(analytics).toMatch(/<RangeToggle\b/);
    expect(all["sections/gateway/analytics/RangeToggle.jsx"]).toMatch(
      /<ToggleButtonGroup\b/,
    );
  });
});
