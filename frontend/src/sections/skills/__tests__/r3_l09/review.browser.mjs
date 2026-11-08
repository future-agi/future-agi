/* eslint-env node */
// Cloud-only synthetic component qualification. This is not a backend E2E or
// evidence that a released SDK/profile exists. All network is loopback-only.
import assert from "node:assert/strict";
import fs from "node:fs/promises";
import path from "node:path";
import { createServer } from "vite";
import react from "@vitejs/plugin-react-swc";

const { chromium } = await import(
  process.env.L9_PLAYWRIGHT_MODULE || "playwright"
);
const output = path.resolve(process.argv[2] || "/tmp/l9-review-shots");
await fs.mkdir(output, { recursive: true });
const entry = `
import React, { useState } from 'react';
import { createRoot } from 'react-dom/client';
import { Box, Button, Typography } from '@mui/material';
import ThemeProvider from 'src/theme';
import { SettingsProvider } from 'src/components/settings/context/settings-provider';
import ReleaseInbox from 'src/sections/skills/review/ReleaseInbox';
import ContextReleaseReview from 'src/sections/skills/contexts/ContextReleaseReview';
import ContextUsePanel from 'src/sections/skills/contexts/ContextUsePanel';
import HumanCheckReview from 'src/sections/skills/review/HumanCheckReview';
import CaseImportDialog from 'src/sections/skills/review/CaseImportDialog';
import { capability, capture, changes, ID, inbox, packet, preview, qualification, request, snapshot } from 'src/sections/skills/__tests__/r3_l09/fixtures';
import '@fontsource/inter/400.css';
import '@fontsource/inter/500.css';
import '@fontsource/inter/600.css';
const params = new URLSearchParams(location.search);
try { localStorage.removeItem('settings'); } catch {}
function App() {
  const [screen, setScreen] = useState(params.get('screen') || 'inbox');
  const [status, setStatus] = useState('');
  return <Box sx={{ minWidth: 0 }}>
    <Typography variant="s3" component="p" sx={{ p: 2 }}>Synthetic component fixture · no backend release · no qualified package claim</Typography>
    <Button onClick={() => setScreen('inbox')}>Skills reviews</Button>
    {screen === 'inbox' && <ReleaseInbox scopeKey="fixture" state={params.get('state') || 'ready'} data={inbox} status={status} onStatusChange={setStatus} onPageChange={() => {}} onOpen={() => setScreen('review')} onSeen={async () => ({})} />}
    {screen === 'review' && <ContextReleaseReview scopeKey="fixture" state="ready" name="refund-context" preview={preview} request={request} permissions={{ canDecide: true }} review={{ complete: true, changes, capabilities: capability }} onDecide={async () => ({ id: ID })} />}
    {screen === 'use' && <Box sx={{ p: 2 }}><ContextUsePanel scopeKey="fixture" state="ready" snapshot={snapshot} origin="https://example.invalid" organizationId={ID} workspaceId={ID} qualification={qualification} /></Box>}
    {screen === 'human' && <Box sx={{ p: 2 }}><HumanCheckReview scopeKey="fixture" state="ready" reference={{ source: 'skills_text', sourceId: ID, annotationRevision: 1, checkId: ID, attemptId: ID }} machineVerdict="pass" canAnnotate onAppend={async () => ({ id: ID })} /></Box>}
    {screen === 'capture' && <CaseImportDialog open onClose={() => setScreen('inbox')} scopeKey="fixture" state="ready" preview={capture} suiteRevision={1} canSave onSave={async () => ({ revision: 2 })} />}
  </Box>;
}
createRoot(document.getElementById('root')).render(<SettingsProvider defaultSettings={{ themeMode: params.get('theme') || 'light', themeDirection: 'ltr', themeContrast: 'default', themeColorPresets: 'default' }}><ThemeProvider><App /></ThemeProvider></SettingsProvider>);
`;
const server = await createServer({
  configFile: false,
  root: process.cwd(),
  plugins: [
    react(),
    {
      name: "l9-fixture",
      resolveId: (id) => (id === "/__l9.jsx" ? id : null),
      load: (id) => (id === "/__l9.jsx" ? entry : null),
      configureServer(vite) {
        vite.middlewares.use((req, res, next) => {
          if (!req.url.startsWith("/l9-review")) {
            next();
            return;
          }
          res.setHeader("Content-Type", "text/html");
          res.end(
            '<!doctype html><html><head><meta name="viewport" content="width=device-width,initial-scale=1"><title>Review fixture</title><script type="module">import RefreshRuntime from "/@react-refresh";RefreshRuntime.injectIntoGlobalHook(window);window.$RefreshReg$=()=>{};window.$RefreshSig$=()=>type=>type;window.__vite_plugin_react_preamble_installed__=true;</script></head><body><div id="root"></div><script type="module" src="/@vite/client"></script><script type="module" src="/__l9.jsx"></script></body></html>',
          );
        });
      },
    },
  ],
  resolve: {
    alias: [{ find: /^src\/(.*)/, replacement: `${process.cwd()}/src/$1` }],
  },
  server: {
    host: "127.0.0.1",
    port: 0,
    fs: { allow: [process.cwd(), await fs.realpath("node_modules")] },
  },
});
await server.listen();
const origin = `http://127.0.0.1:${server.httpServer.address().port}`;
let browser;
const captures = [];
const errors = [];
try {
  browser = await chromium.launch({
    headless: true,
    args: ["--disable-dev-shm-usage"],
  });
  const page = await browser.newPage();
  page.on("pageerror", (error) => errors.push(error.message));
  await page.route("**/*", (route) =>
    route.request().url().startsWith(origin) ? route.continue() : route.abort(),
  );
  const visit = async (screen, theme, width, state = "") => {
    await page.setViewportSize({ width, height: 1000 });
    await page.goto(
      `${origin}/l9-review?${new URLSearchParams({ screen, theme, state })}`,
    );
    await page.getByText(/Synthetic component fixture/).waitFor();
    await page.evaluate(() => document.fonts.ready);
  };
  const captureShot = async (name) => {
    assert(
      await page.evaluate(
        () => document.documentElement.scrollWidth <= window.innerWidth,
      ),
      `Horizontal page overflow: ${name}`,
    );
    await page.screenshot({
      path: path.join(output, `${name}.png`),
      fullPage: true,
    });
    captures.push(name);
  };
  for (const theme of ["light", "dark"]) {
    for (const width of [1440, 390, 320]) {
      await visit("inbox", theme, width);
      await page
        .getByRole("button", { name: "refund-context", exact: true })
        .waitFor();
      await captureShot(`inbox-${theme}-${width}`);
      // Keyboard-only arrival from the real inbox control, not an injected URL.
      await page
        .getByRole("button", { name: "refund-context", exact: true })
        .focus();
      await page.keyboard.press("Enter");
      await page.getByText("Exact release", { exact: true }).waitFor();
      assert(
        await page
          .getByRole("button", { name: "Approve exact packet" })
          .isDisabled(),
      );
      if (width < 600)
        await page
          .getByRole("region", { name: "Unified file changes" })
          .first()
          .waitFor();
      await captureShot(`review-${theme}-${width}`);
      await visit("use", theme, width);
      await page.getByRole("button", { name: "Copy plan command" }).waitFor();
      await page.evaluate(() =>
        Object.defineProperty(navigator, "clipboard", {
          configurable: true,
          value: {
            writeText: () => Promise.reject(new Error("fixture denial")),
          },
        }),
      );
      await page.getByRole("button", { name: "Copy plan command" }).click();
      await page.getByText(/Could not copy/).waitFor();
      await captureShot(`use-${theme}-${width}`);
    }
    await visit("capture", theme, 390);
    await page.getByRole("textbox", { name: "Example title" }).waitFor();
    await captureShot(`capture-${theme}-390`);
    await page
      .getByRole("button", { name: "Close", exact: true })
      .first()
      .click();
    await page.getByText("Skills reviews", { exact: true }).first().waitFor();
  }
  for (const zoom of [2, 4]) {
    await visit("review", "light", 1280);
    await page.getByText("Exact release", { exact: true }).waitFor();
    await page.evaluate((value) => {
      document.documentElement.style.zoom = value;
    }, zoom);
    await captureShot(`review-zoom-${zoom * 100}`);
  }
  for (const state of [
    "loading",
    "error",
    "empty",
    "forbidden",
    "unavailable",
  ]) {
    await visit("inbox", "light", 390, state);
    await captureShot(`inbox-${state}`);
  }
  assert.deepEqual(errors, [], "No browser runtime errors");
  await fs.writeFile(
    path.join(output, "attestation.json"),
    JSON.stringify(
      {
        scope: "synthetic-component-only",
        captures,
        browser: await browser.version(),
        errors,
        backendE2E: "not run: shared routing and API controllers unavailable",
      },
      null,
      2,
    ),
  );
  process.stdout.write(
    `PASS ${captures.length} screenshots; keyboard inbox arrival; narrow unified diff; copy rejection; light/dark; zoom; state matrix\n`,
  );
} finally {
  await browser?.close();
  await server.close();
}
