/* eslint-env node */
// Cloud-only component smoke check. Uses the real design system with synthetic
// API fixtures, not the authenticated app or backend. Install Playwright outside
// the repository and pass its absolute entry path in PLAYWRIGHT_MODULE.
import assert from "node:assert/strict";
import { mkdir, mkdtemp, rm } from "node:fs/promises";
import path from "node:path";
import { pathToFileURL } from "node:url";
import { createServer } from "vite";

const { chromium } = await import(pathToFileURL(process.env.PLAYWRIGHT_MODULE).href);
const root = process.cwd();
const output = path.join(root, "src/sections/skills/library/.screenshots");
await mkdir(output, { recursive: true });
// node_modules is shared across VM lanes; keep Vite's mutable cache private.
const cacheDir = await mkdtemp(path.join(root, "src/sections/skills/library/.browser-cache-"));

const fixtures = `
const makeSkill = (name, index) => ({
  id: 'skill-' + index, name, description: 'Use when reviewing ' + name.replaceAll('-', ' ') + ' for your team.',
  tags: ['team'], latestVersion: index === 3 ? null : index + 1,
  labels: index === 3 ? [] : [{name: 'production', version: index}],
  hasDraft: true, fetches7d: index * 12, updatedAt: '2026-11-06T12:00:00Z', updatedBy: {id: 'user-1', name: 'Sam'},
});
const skills = ['support-policy', 'code-review', 'research-brief'].map((name, index) => makeSkill(name, index + 1));
const taggedSkills = Array.from({length: 26}, (_, index) => ({...makeSkill('tagged-skill-' + index, index + 1), tags: [index === 25 ? 'finance' : 'support']}));
export const useSkillsList = ({tag, search, label, page, pageSize}) => {
  const workspaceSkills = new URLSearchParams(location.search).get('fixture') === 'tags' ? taggedSkills : skills;
  const matches = workspaceSkills.filter(skill => (!tag || skill.tags.includes(tag)) && (!search || skill.name.includes(search)) && (!label || skill.labels.some(item => item.name === label)));
  return {data: {results: matches.slice((page - 1) * pageSize, page * pageSize), count: matches.length, page, pageSize, totalPages: Math.ceil(matches.length / pageSize), stats: {total: workspaceSkills.length, inProduction: 2, fetches7d: 72, myDrafts: 1}}, refetch: () => {}};
};
export const useSkillTemplates = () => ({data: ['support-policy','code-review','incident-triage','research-brief','trace-debugging','eval-writer'].map(id => ({id, title: id.replaceAll('-', ' '), description: 'Use when reviewing ' + id.replaceAll('-', ' ') + ' with your team.'}))});
export const useCreateSkill = () => ({mutateAsync: async () => ({id: 'new-skill'})});
export const useArchiveSkill = () => ({mutateAsync: async () => {}});
export const downloadSkillsZip = async () => {};
export const getSkillErrorCode = error => error.code;
export const useImportPreview = () => ({mutateAsync: async () => ({
  bundles: ['new','changed','unchanged','invalid'].map((status, index) => ({key: status, name: 'skill-' + status, root: 'skills/' + status, status, fileCount: 3, totalBytes: 1024,
    checks: {summary: {errors: status === 'invalid' ? 1 : 0, warnings: status === 'changed' ? 1 : 0, infos: 0},
      items: status === 'changed' ? [{code: 'description_short', severity: 'warning', message: 'Describe when to use this skill.', path: 'SKILL.md'}] : []}})),
  rejected: [], skipped: ['.DS_Store']
})});
export const useImportSkills = () => ({mutateAsync: async () => ({created: [{}], updated: [{}], unchanged: [{}], failed: []})});
`;
const entry = `
import React from 'react';
import { createRoot } from 'react-dom/client';
import { BrowserRouter } from 'react-router-dom';
import { CssBaseline } from '@mui/material';
import { ThemeProvider, createTheme } from '@mui/material/styles';
import { palette } from 'src/theme/palette';
import { typography } from 'src/theme/typography';
import { dsShape } from 'src/theme/ds-tokens';
import { shadows } from 'src/theme/shadows';
import { customShadows } from 'src/theme/custom-shadows';
import { componentsOverrides } from 'src/theme/overrides';
import SkillsLibraryView from 'src/sections/skills/library/SkillsLibraryView';
import NewSkillDialog from 'src/sections/skills/library/NewSkillDialog';
import ImportSkillsDialog from 'src/sections/skills/library/ImportSkillsDialog';
const params = new URLSearchParams(location.search);
const mode = params.get('theme') || 'light';
const theme = createTheme({palette: palette(mode), typography, shape: dsShape, shadows: shadows(mode), customShadows: customShadows(mode)});
theme.components = componentsOverrides(theme);
const View = {new: NewSkillDialog, import: ImportSkillsDialog}[params.get('screen')] || SkillsLibraryView;
createRoot(document.getElementById('root')).render(React.createElement(BrowserRouter, null,
  React.createElement(ThemeProvider, {theme}, React.createElement(CssBaseline), React.createElement(View, {open: true, onClose: () => {}}))));
`;
const server = await createServer({
  configFile: false, root, cacheDir,
  server: { host: "127.0.0.1", port: 0 },
  esbuild: { jsx: "automatic" },
  optimizeDeps: { entries: ["src/sections/skills/library/SkillsLibraryView.jsx"] },
  resolve: { alias: [
    { find: "src/api/skills", replacement: "virtual:f1-api" },
    { find: /^src\//, replacement: `${root}/src/` },
  ] },
  plugins: [{
    name: "f1-browser-fixture",
    resolveId(id) { if (["virtual:f1-api", "/__f1_entry.js"].includes(id)) return id; return null; },
    load(id) { if (id === "virtual:f1-api") return fixtures; if (id === "/__f1_entry.js") return entry; return null; },
    configureServer(vite) {
      vite.middlewares.use(async (request, response, next) => {
        if (!request.url.startsWith("/dashboard/skills")) return next();
        response.setHeader("Content-Type", "text/html");
        response.end(await vite.transformIndexHtml(request.url, '<!doctype html><html><head><meta name="viewport" content="width=device-width,initial-scale=1"></head><body><div id="root"></div><script type="module" src="/__f1_entry.js"></script></body></html>'));
      });
    },
  }],
});
await server.listen();
const origin = `http://127.0.0.1:${server.httpServer.address().port}`;
let browser;
try {
  browser = await chromium.launch({ headless: true, executablePath: process.env.SKILLS_CHROME_PATH });
  const page = await browser.newPage();
  const errors = [];
  page.on("pageerror", (error) => {
    errors.push(error.message);
    process.stderr.write(`Browser error: ${error.message}\n`);
  });
  for (const theme of ["light", "dark"]) {
    for (const width of [1280, 390]) {
      await page.setViewportSize({ width, height: 900 });
      for (const state of ["rows", "empty", "loading", "error", "new", "import"]) {
        const params = new URLSearchParams({ theme });
        if (["new", "import"].includes(state)) params.set("screen", state);
        else if (state !== "rows") params.set("state", state);
        await page.goto(`${origin}/dashboard/skills?${params}`);
        if (state === "rows") {
          await page.getByRole("link", { name: "support-policy" }).waitFor();
          const tagFilter = page.getByRole("combobox", { name: "Tag", exact: true });
          assert.equal(await tagFilter.inputValue(), "");
          assert.equal(await tagFilter.getAttribute("placeholder"), "Any tag");
          assert.equal(await page.getByRole("combobox", { name: /^Label/ }).innerText(), "Any label");
          if (width === 1280) await page.getByRole("button", { name: "Actions for support-policy" }).locator("svg").waitFor();
        }
        if (state === "empty") {
          await page.getByRole("button", { name: "Write from scratch" }).waitFor();
          const tilesFit = await page.locator("#skill-template-gallery button").evaluateAll((tiles) => tiles.every((tile) => {
            const bounds = tile.getBoundingClientRect();
            return Array.from(tile.querySelectorAll("p, span")).every((text) => text.getBoundingClientRect().bottom <= bounds.bottom);
          }));
          assert.equal(tilesFit, true, `${theme}/${width}: template text must fit its tile`);
        }
        if (state === "loading") await page.getByLabel("Loading skills").waitFor();
        if (state === "error") await page.getByRole("button", { name: "Retry" }).waitFor();
        if (state === "new") {
          await page.getByRole("textbox", { name: "Name", exact: true }).fill("Invalid_Name");
          assert.equal(await page.getByRole("button", { name: "Create skill" }).isEnabled(), false);
        }
        if (state === "import") {
          const chooseFolder = page.getByRole("button", { name: "Choose folder", exact: true });
          for (const key of ["Enter", "Space"]) {
            await chooseFolder.focus();
            const [chooser] = await Promise.all([
              page.waitForEvent("filechooser", { timeout: 3000 }),
              chooseFolder.press(key),
            ]);
            assert.equal(await chooser.element().getAttribute("webkitdirectory"), "", `${key} opens the directory picker`);
          }
          await page.locator('input[type="file"]').first().setInputFiles({ name: "SKILL.md", mimeType: "text/markdown", buffer: Buffer.from("Synthetic skill") });
          await page.getByRole("button", { name: "Preview import" }).click();
          await page.getByRole("checkbox", { name: "Select skill-invalid" }).waitFor();
          assert.equal(await page.getByRole("combobox", { name: /^Also label as/ }).innerText(), "None");
          assert.equal(await page.getByRole("checkbox", { name: "Select skill-invalid" }).isEnabled(), false);
          assert.equal(await page.getByRole("button", { name: "Import", exact: true }).isEnabled(), false);
          await page.getByRole("checkbox", { name: "I reviewed the warnings for the selected skills." }).check();
          assert.equal(await page.getByRole("button", { name: "Import", exact: true }).isEnabled(), true);
        }
        assert.equal(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth), true, `${theme}/${width}/${state}: page overflow`);
        await page.screenshot({ path: path.join(output, `${theme}-${width}-${state}.png`), fullPage: true, animations: "disabled" });
      }
      await page.goto(`${origin}/dashboard/skills?fixture=tags&theme=${theme}`);
      await page.getByRole("link", { name: "tagged-skill-0", exact: true }).waitFor();
      assert.equal(await page.getByRole("link", { name: "tagged-skill-25", exact: true }).count(), 0);
      const tagFilter = page.getByRole("combobox", { name: "Tag", exact: true });
      await tagFilter.fill("finance");
      await tagFilter.press("Enter");
      await page.getByRole("link", { name: "tagged-skill-25", exact: true }).waitFor();
      assert.equal(new URL(page.url()).searchParams.get("tag"), "finance");
      await tagFilter.fill("support");
      await tagFilter.press("Enter");
      await page.getByRole("link", { name: "tagged-skill-0", exact: true }).waitFor();
      assert.equal(await page.getByRole("link", { name: "tagged-skill-25", exact: true }).count(), 0);
      assert.equal(new URL(page.url()).searchParams.get("tag"), "support");
      await tagFilter.fill("missing-tag");
      await tagFilter.press("Tab");
      await page.getByRole("heading", { name: "No skills found" }).waitFor();
      assert.equal(new URL(page.url()).searchParams.get("tag"), "missing-tag");
      await tagFilter.focus();
      const clearTag = page.getByRole("button", { name: "Clear", exact: true });
      assert.equal(await clearTag.isVisible(), true);
      await clearTag.click();
      assert.equal(new URL(page.url()).searchParams.has("tag"), false);
      assert.equal(await tagFilter.inputValue(), "");
      await page.getByRole("link", { name: "tagged-skill-0", exact: true }).waitFor();
      assert.equal(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth), true, `${theme}/${width}/tag filter: page overflow`);
      await page.screenshot({ path: path.join(output, `${theme}-${width}-tag-filter.png`), fullPage: true, animations: "disabled" });
    }
  }
  assert.deepEqual(errors, [], "Browser runtime errors");
  process.stdout.write(`PASS: 24 light/dark desktop/mobile component-state checks, Enter/Space folder pickers, and 4 off-page/disjoint tag-filter checks. Screenshots: ${output}\n`);
} finally {
  await browser?.close();
  await server.close();
  await rm(cacheDir, { recursive: true, force: true });
}
