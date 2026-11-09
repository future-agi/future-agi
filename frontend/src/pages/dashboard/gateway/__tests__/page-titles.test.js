import { describe, expect, it } from "vitest";

// Each AI Gateway page on the shared page frame gets its browser-tab title
// from GatewayPageFrame (its title or documentTitle). A second <title> in the
// route wrapper ("Gateway Keys | Future AGI" next to the frame's "API keys")
// is one page with two names: the wrapper sets none.
const wrappers = import.meta.glob("../*.jsx", {
  query: "?raw",
  import: "default",
  eager: true,
});
const read = (file) => wrappers[`../${file}`];
const reExport = import.meta.glob(
  "../../../../sections/gateway/GatewayOverviewSection.jsx",
);

const ON_THE_FRAME = [
  "GatewayAnalytics.jsx",
  "GatewayBudgets.jsx",
  "GatewayCustomProperties.jsx",
  "GatewayFallbacks.jsx",
  "GatewayGuardrails.jsx",
  "GatewayIntegrations.jsx",
  "GatewayKeys.jsx",
  "GatewayLogs.jsx",
  "GatewayMCP.jsx",
  "GatewayModels.jsx",
  "GatewayMonitoring.jsx",
  "GatewayOverview.jsx",
  "GatewayProviders.jsx",
  "GatewayRunDetail.jsx",
  "GatewayRuns.jsx",
  "GatewaySessions.jsx",
  "GatewaySettings.jsx",
  "GatewayWebhooks.jsx",
];

describe("AI Gateway page wrappers", () => {
  it.each(ON_THE_FRAME)("%s leaves the title to the page frame", (file) => {
    const source = read(file);
    expect(source, file).toEqual(expect.any(String));
    expect(source).not.toMatch(/<title>/);
    expect(source).not.toMatch(/react-helmet-async/);
  });

  it("opens the Overview page directly, not through a re-export", () => {
    expect(read("GatewayOverview.jsx")).toMatch(
      /from "src\/sections\/gateway\/overview\/GatewayOverviewPage"/,
    );
    expect(Object.keys(reExport)).toEqual([]);
  });
});
