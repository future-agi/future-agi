/* eslint-env node */
// Cloud-only component visual check. Synthetic fixtures, NOT authenticated UAT.
import { writeFile, rm } from "node:fs/promises";
import { createRequire } from "node:module";
import { join } from "node:path";
import { createServer } from "vite";
import react from "@vitejs/plugin-react-swc";
const require = createRequire(import.meta.url);
const {
  chromium,
  expect,
} = require("../../../../../e2e/node_modules/@playwright/test");
const root = process.cwd();
const entry = join(root, "src/__metricPageReview.jsx");
const fixtureImport =
  "/src/sections/derived-metrics/__tests__/metricPageFixtures.js";
const mockTransport = `import {metric,kpi,trend,driver,field,percentileMetrics} from '${fixtureImport}';
const selectedMetric=percentileMetrics.find(item=>location.pathname.endsWith('/'+item.id))||metric;
export const endpoints={derivedMetrics:Object.fromEntries(['detail','kpi','trend','drivers','fields','history','refresh'].map(n=>[n,id=>'/metrics/'+id+'/'+n]))};
const records={detail:selectedMetric,kpi,trend,fields:{results:[field]},drivers:{provenance:kpi.provenance,effective_window:kpi.effective_window,results:[driver,{...driver,unit_id:'session-b',influence:-0.027}],next_cursor:null},history:{results:[{kind:'version',created_at:'2026-11-01T00:00:00Z',status:'complete',definition:metric.definition,snapshot:null},{kind:'recalculation',created_at:'2026-11-01T02:00:00Z',status:'committed',definition:null,snapshot:null}],next_cursor:null}};
export default {get:async url=>({data:structuredClone(records[url.split('/').at(-1)])}),post:async()=>({data:{status:'queued'}})};
export const readQuery=async()=>{throw Error('Unexpected read-POST in visual fixture')};`;
const page = `import React from 'react';import{createRoot}from'react-dom/client';import{BrowserRouter,Route,Routes}from'react-router-dom';import{QueryClient,QueryClientProvider}from'@tanstack/react-query';import{ThemeProvider,createTheme}from'@mui/material/styles';import{CssBaseline,Box,Typography}from'@mui/material';import{palette}from'src/theme/palette';import{typography}from'src/theme/typography';import{customShadows}from'src/theme/custom-shadows';import{shadows}from'src/theme/shadows';import{dsShape}from'src/theme/ds-tokens';import{componentsOverrides}from'src/theme/overrides';import MetricDetailPage from 'src/sections/derived-metrics/MetricDetailPage';
const mode=new URLSearchParams(location.search).get('mode')||'light';const theme=createTheme({palette:palette(mode),typography,shape:dsShape,shadows:shadows(mode),customShadows:customShadows(mode)});theme.components=componentsOverrides(theme);const client=new QueryClient({defaultOptions:{queries:{retry:false}}});
createRoot(document.getElementById('root')).render(<ThemeProvider theme={theme}><CssBaseline/><Typography sx={{px:4,py:1,bgcolor:'background.neutral'}} variant="caption" component="div">Component visual test · synthetic fixtures · not live customer data</Typography><Box sx={{maxWidth:1400,mx:'auto'}}><QueryClientProvider client={client}><BrowserRouter><Routes><Route path="/metric-review/:metricId" element={<MetricDetailPage/>}/></Routes></BrowserRouter></QueryClientProvider></Box></ThemeProvider>);`;
await writeFile(entry, page);
const server = await createServer({
  configFile: false,
  root,
  resolve: { alias: { src: join(root, "src") } },
  plugins: [
    {
      name: "metrics-visual-fixtures",
      enforce: "pre",
      load(id) {
        if (id.endsWith("/utils/axios.js")) return mockTransport;
        if (id.endsWith("/contexts/OrganizationContext.jsx"))
          return "export const useOrganization=()=>({currentOrganizationId:'org-a',isReady:true});";
        if (id.endsWith("/contexts/WorkspaceContext.jsx"))
          return "export const useWorkspace=()=>({currentWorkspaceId:'ws-a',currentWorkspaceRole:'workspace_member',isReady:true});";
        if (id.endsWith("/derived-metrics/useMetricsEnabled.js"))
          return "export const useMetricsEnabled=()=>({enabled:true});";
      },
      configureServer(s) {
        s.middlewares.use(async (req, res, next) => {
          if (!req.url.startsWith("/metric-review/")) return next();
          try {
            res.setHeader("content-type", "text/html");
            res.end(
              await s.transformIndexHtml(
                req.url,
                '<!doctype html><html><head><title>Metric Page Test</title><meta name="viewport" content="width=device-width,initial-scale=1"></head><body><div id="root"></div><script type="module" src="/src/__metricPageReview.jsx"></script></body></html>',
              ),
            );
          } catch (error) {
            next(error);
          }
        });
      },
    },
    react(),
  ],
  server: { host: "127.0.0.1", port: 4198, strictPort: true },
});
let browser;
try {
  await server.listen();
  browser = await chromium.launch({ headless: true, args: ["--no-sandbox"] });
  const errors = [];
  const results = [];
  for (const mode of ["light", "dark"]) {
    const page = await browser.newPage({
      viewport: { width: 1440, height: 1000 },
    });
    page.on("pageerror", (error) => errors.push(error.message));
    await page.goto(
      `http://127.0.0.1:4198/metric-review/metric-a?mode=${mode}`,
    );
    await page.getByTestId("metric-hero").waitFor();
    await page.locator(".apexcharts-svg").waitFor();
    await expect(page.locator(".apexcharts-marker:visible")).toHaveCount(2);
    results.push(
      `${mode}/Trend: both isolated values visible; missing bucket has no marker`,
    );
    for (const tab of ["Overview", "Drivers", "History", "Definition"]) {
      await page.getByRole("tab", { name: tab, exact: true }).click();
      if (tab === "Drivers")
        await page.getByRole("table", { name: "Metric drivers" }).waitFor();
      if (tab === "History")
        await page.getByRole("table", { name: "Metric history" }).waitFor();
      const overflow = await page.evaluate(
        () => document.documentElement.scrollWidth > window.innerWidth,
      );
      if (overflow) throw new Error(`Horizontal overflow in ${mode}/${tab}`);
      await page.screenshot({
        path: `/opt/regen/logs/page-${mode}-${tab.toLowerCase()}.png`,
        fullPage: true,
      });
      results.push(`${mode}/${tab}: rendered without horizontal overflow`);
    }
    for (const percentile of [50, 95]) {
      await page.goto(
        `http://127.0.0.1:4198/metric-review/metric-p${percentile}?mode=${mode}`,
      );
      await page.getByRole("tab", { name: "Definition", exact: true }).click();
      await expect(
        page.locator("dd").getByText(`P${percentile}`, { exact: true }),
      ).toBeVisible();
      await expect(
        page.getByText("mean(duration)", { exact: true }),
      ).toHaveCount(0);
      await expect(
        page.getByText(`Estimates P${percentile} of duration`, {
          exact: false,
        }),
      ).toBeVisible();
      const overflow = await page.evaluate(
        () => document.documentElement.scrollWidth > window.innerWidth,
      );
      if (overflow)
        throw new Error(`Horizontal overflow in ${mode}/P${percentile}`);
      await page.screenshot({
        path: `/opt/regen/logs/page-${mode}-definition-p${percentile}.png`,
        fullPage: true,
      });
      results.push(
        `${mode}/P${percentile}: selected percentile and calculation visible without helper formula or overflow`,
      );
    }
    await page.close();
  }
  if (errors.length) throw new Error(errors.join("\n"));
  await writeFile(
    "/opt/regen/logs/page-visual.json",
    JSON.stringify(
      { synthetic_fixture: true, checks: results, page_errors: errors },
      null,
      2,
    ),
  );
} finally {
  await browser?.close();
  await server.close();
  await rm(entry, { force: true });
}
