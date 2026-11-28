/* eslint-env node */
// Cloud-only real-component/Monaco qualification, not a full-app or backend E2E.
// Run from frontend with lane-private Playwright and Monaco installations.
import assert from "node:assert/strict";
import fs from "node:fs/promises";
import path from "node:path";
import { createServer } from "vite";
import react from "@vitejs/plugin-react-swc";

const { chromium } = await import(
  process.env.L8_PLAYWRIGHT_MODULE || "playwright"
);
const monacoRoot = path.resolve(process.env.L8_MONACO_ROOT);
const output = path.resolve(process.argv[2] || "/tmp/l8-document-shots");
await fs.mkdir(output, { recursive: true });
const content =
  '---\r\n# Retain this source comment\r\nname: refund-policy\r\ndescription: "Use when answering refund eligibility questions." # routing\r\nmetadata:\r\n  owner: Policy team\r\nallowed-tools: Read\r\n---\r\n# Refund policy\r\n\r\nApprove refunds requested within 30 days of purchase.\r\n\r\n## Exceptions\r\nEscalate incomplete purchase records for review.\r\n';
const entry = `
import React, { useState } from 'react';
import { createRoot } from 'react-dom/client';
import { Box, Button, Typography } from '@mui/material';
import { SnackbarProvider } from 'notistack';
import { loader } from '@monaco-editor/react';
import ThemeProvider from 'src/theme';
import { SettingsProvider } from 'src/components/settings/context/settings-provider';
import SkillDocumentEditor from 'src/sections/skills/editor/SkillDocumentEditor';
import '@fontsource/inter/400.css';
import '@fontsource/inter/500.css';
import '@fontsource/inter/600.css';
loader.config({ paths: { vs: '/__monaco/vs' } });
const params = new URLSearchParams(location.search);
localStorage.removeItem('settings');
const original = params.has('unsupported') ? '---\\nname: refund-policy\\ndescription: |\\n  Use this preserved multiline value.\\n---\\nOriginal policy' : ${JSON.stringify(content)};
function App() {
  const [value, setValue] = useState(original);
  const [target, setTarget] = useState(null);
  window.l8Canonical = value;
  loader.init().then((monaco) => { window.l8Monaco = monaco; });
  window.l8Changes ??= 0;
  return <Box sx={{ p: 2, minWidth: 0, minHeight: '100dvh', display: 'flex', flexDirection: 'column', gap: 2 }}>
    <Typography variant='m1'>Edit refund-policy</Typography>
    <Typography variant='s3' color='text.secondary'>Synthetic component fixture · no backend or publication</Typography>
    <Button onClick={() => setTarget({ line: 11 })}>Open check line 11</Button>
    <Box sx={{ flex: 1, minHeight: 640, minWidth: 0, display: 'flex' }}>
      <SkillDocumentEditor file={{ path: 'SKILL.md', content: value }} target={target} onChange={(next) => { window.l8Changes += 1; setValue(next); }} readOnly={params.has('readonly')} sampleContent />
    </Box>
  </Box>;
}
createRoot(document.getElementById('root')).render(<SettingsProvider defaultSettings={{ themeMode: params.get('theme') || 'light', themeDirection: 'ltr', themeContrast: 'default', themeColorPresets: 'default' }}><ThemeProvider><SnackbarProvider><App /></SnackbarProvider></ThemeProvider></SettingsProvider>);
`;
const server = await createServer({
  configFile: false,
  root: process.cwd(),
  plugins: [
    react(),
    {
      name: "l8-document-fixture",
      resolveId(id) {
        return id === "/__l8.jsx" ? id : null;
      },
      load(id) {
        return id === "/__l8.jsx" ? entry : null;
      },
      configureServer(vite) {
        vite.middlewares.use(async (request, response, next) => {
          if (request.url.startsWith("/__monaco/")) {
            const filename = path.resolve(
              monacoRoot,
              request.url.slice("/__monaco/".length).split("?")[0],
            );
            if (!filename.startsWith(`${monacoRoot}/`)) {
              response.statusCode = 404;
              response.end();
              return;
            }
            try {
              response.setHeader(
                "Content-Type",
                filename.endsWith(".css")
                  ? "text/css"
                  : filename.endsWith(".ttf")
                    ? "font/ttf"
                    : "application/javascript",
              );
              response.end(await fs.readFile(filename));
            } catch {
              response.statusCode = 404;
              response.end();
            }
            return;
          }
          if (!request.url.startsWith("/l8-document")) {
            next();
            return;
          }
          response.setHeader("Content-Type", "text/html");
          response.end(
            '<!doctype html><html><head><meta name="viewport" content="width=device-width,initial-scale=1"><title>Document fixture</title><script type="module">import RefreshRuntime from "/@react-refresh";RefreshRuntime.injectIntoGlobalHook(window);window.$RefreshReg$=()=>{};window.$RefreshSig$=()=>type=>type;window.__vite_plugin_react_preamble_installed__=true;</script></head><body><div id="root"></div><script type="module" src="/@vite/client"></script><script type="module" src="/__l8.jsx"></script></body></html>',
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
const address = server.httpServer.address();
const origin = `http://127.0.0.1:${address.port}`;
let browser;
let captures = 0;
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
  const capture = async (name) => {
    await page.evaluate(() => document.fonts.ready);
    assert(
      await page.evaluate(
        () => document.documentElement.scrollWidth <= window.innerWidth,
      ),
      `No horizontal page scroll: ${name}`,
    );
    await page.screenshot({
      path: path.join(output, `${name}.png`),
      fullPage: true,
    });
    captures += 1;
  };
  for (const theme of ["light", "dark"]) {
    // 640/320 CSS px additionally cover reflow equivalents of 200%/400% at
    // 1280 physical pixels. This is not a claim of full-app browser zoom proof.
    for (const width of [1280, 640, 390, 320]) {
      const suffix = `${theme}-${width}`;
      await page.setViewportSize({ width, height: 960 });
      await page.goto(`${origin}/l8-document?theme=${theme}`);
      const body = page.getByRole("textbox", {
        name: "Instructions",
        exact: true,
      });
      await body.waitFor();
      await capture(`document-${suffix}`);
      assert.equal(await page.evaluate(() => window.l8Changes), 0);
      await page.getByRole("tab", { name: "Source", exact: true }).click();
      await page.locator(".monaco-editor").waitFor();
      await capture(`source-${suffix}`);
      await page.getByRole("tab", { name: "Preview", exact: true }).click();
      await page
        .getByRole("heading", { name: "Refund policy", exact: true })
        .waitFor();
      await capture(`preview-${suffix}`);
      await page.getByRole("tab", { name: "Document", exact: true }).click();
      assert.equal(
        await page.evaluate(() => window.l8Canonical),
        content,
        "Mode changes preserve exact bytes",
      );
      assert.equal(
        await page.evaluate(() => window.l8Changes),
        0,
        "Mode changes do not save",
      );
      await page
        .getByRole("textbox", {
          name: "When should the agent use this skill?",
          exact: true,
        })
        .fill("Use for approved refund requests.");
      assert.equal(
        await page.evaluate(() => window.l8Canonical),
        content.replace(
          '"Use when answering refund eligibility questions."',
          '"Use for approved refund requests."',
        ),
      );
      await body.click();
      await body.press("Control+End");
      await body.pressSequentially("New clause.");
      assert(
        (await page.evaluate(() => window.l8Canonical)).endsWith("New clause."),
      );
      const beforeTyping = content.replace(
        '"Use when answering refund eligibility questions."',
        '"Use for approved refund requests."',
      );
      // Chromium may group typing into several undo units. Require every typed
      // character to be reversible without ever undoing the description edit.
      for (
        let undo = 0;
        undo < "New clause.".length &&
        (await page.evaluate(() => window.l8Canonical)) !== beforeTyping;
        undo += 1
      ) {
        await body.press("Control+z");
      }
      assert.equal(
        await page.evaluate(() => window.l8Canonical),
        beforeTyping,
        "Native body undo preserves canonical bytes",
      );
      // Keyboard-only mode navigation uses MUI's actual roving tab stop.
      await page.getByRole("tab", { name: "Document", exact: true }).focus();
      await page.keyboard.press("ArrowRight");
      await page.keyboard.press("Enter");
      assert.equal(
        await page
          .getByRole("tab", { name: "Source", exact: true })
          .getAttribute("aria-selected"),
        "true",
      );
      // UX-003: a retained Monaco pane must focus only after it is visible.
      await page.getByRole("tab", { name: "Document", exact: true }).click();
      await page.getByRole("button", { name: "Open check line 11" }).click();
      await page.waitForFunction(() => {
        const editor = window.l8Monaco?.editor.getEditors()[0];
        return editor?.hasTextFocus() && editor.getPosition()?.lineNumber === 11;
      });
      await capture(`check-focus-${suffix}`);
      await page.getByRole("button", { name: "Open check line 11" }).focus();
      await page.setViewportSize({ width, height: 980 });
      await page.evaluate(() => new Promise((resolve) => requestAnimationFrame(() => requestAnimationFrame(resolve))));
      assert.equal(await page.getByRole("button", { name: "Open check line 11" }).evaluate((element) => document.activeElement === element), true, "Later layout does not steal keyboard focus");
      await page.goto(`${origin}/l8-document?theme=${theme}&unsupported=1`);
      await page
        .getByText(
          "This frontmatter needs Source editing. Your original text has not been changed.",
        )
        .waitFor();
      await page.locator(".monaco-editor").waitFor();
      await capture(`fallback-${suffix}`);
      assert.equal(await page.evaluate(() => window.l8Changes), 0);
    }
  }
  await page.goto(`${origin}/l8-document?readonly=1`);
  await page
    .getByRole("textbox", { name: "Instructions", exact: true })
    .waitFor();
  assert(
    (await page
      .getByRole("textbox", { name: "Instructions", exact: true })
      .getAttribute("readonly")) !== null,
  );
  assert(
    (await page
      .getByRole("textbox", {
        name: "When should the agent use this skill?",
        exact: true,
      })
      .getAttribute("readonly")) !== null,
  );
  assert.deepEqual(errors, [], "No uncaught browser exceptions");
  process.stdout.write(
    `PASS ${captures} component captures: real Monaco offline, light/dark, 1280/640/390/320 CSS px, exact scalar edits, byte-preserving view changes, keyboard tabs, native body undo, read-only and unsupported-source fallback. Browser ${browser.version()}.\n`,
  );
} finally {
  await browser?.close();
  await server.close();
}
