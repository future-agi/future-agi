/* eslint-env node */
// Cloud-only, real authenticated app/API regression. Use the VM's existing
// Context Hub E2E harness and a frontend serving this worktree, never production.
// CTXHUB_E2E_DIR=/path/to/e2e node src/sections/skills/detail/detail.browser.mjs /tmp/detail-shots
import assert from "node:assert/strict";
import { mkdir } from "node:fs/promises";
import { createRequire } from "node:module";
import path from "node:path";
import { pathToFileURL } from "node:url";
import { DETAIL_BOUNDARY_TAG } from "./detail.browser-fixtures.js";

const harness = process.env.CTXHUB_E2E_DIR;
assert(harness, "Set CTXHUB_E2E_DIR to the cloud E2E harness directory.");
const require = createRequire(path.join(harness, "package.json"));
const { chromium, expect } = require("@playwright/test");
const { APP, provisionActor } = await import(pathToFileURL(path.join(harness, "provision.mjs")).href);
const output = path.resolve(process.argv[2] || "/tmp/skills-detail-shots");
await mkdir(output, { recursive: true });
const actor = await provisionActor();
const name = `support-policy-${"a".repeat(36)}-${Date.now().toString(36)}`;
const content = `---\nname: ${name}\ndescription: Use when reviewing a synthetic support request against the refund policy.\n---\n# Review a support request\n\nRead the [refund policy](references/policy.md) before responding.\n`;
const first = await actor.api("/model-hub/skills/publish/", { method: "POST", body: { files: [{ path: "SKILL.md", content }, { path: "references/policy.md", content: "# Refund policy\nReview the purchase date and eligibility.\n" }], labels: [] } });
const id = first.skill.id;
const skillUrl = `/model-hub/skills/${id}/`;
await actor.api(skillUrl, { method: "PATCH", body: { tags: [DETAIL_BOUNDARY_TAG] } });
const browser = await chromium.launch();
const context = await browser.newContext({ baseURL: APP, viewport: { width: 1440, height: 900 }, permissions: ["clipboard-read", "clipboard-write"] });
await context.addInitScript(({ tokens, organizationId, workspaceId }) => {
  localStorage.setItem("accessToken", tokens.access);
  localStorage.setItem("refreshToken", tokens.refresh);
  localStorage.setItem("rememberMe", "true");
  localStorage.setItem("initial-render", "done");
  sessionStorage.setItem("organizationId", organizationId);
  sessionStorage.setItem("workspaceId", workspaceId);
}, { tokens: actor.tokens, organizationId: actor.organizationId, workspaceId: actor.workspaceId });
const page = await context.newPage();
page.setDefaultTimeout(60_000);
const errors = [];
page.on("pageerror", (error) => errors.push(error.message));
const screenshot = async (file) => {
  await page.evaluate(() => document.fonts.ready);
  await page.screenshot({ path: path.join(output, `${file}.png`), animations: "disabled", fullPage: true });
};
try {
  for (const themeMode of ["light", "dark"]) {
    await page.goto(`/dashboard/skills/${id}?tab=files`);
    await page.evaluate((mode) => {
      const settings = JSON.parse(localStorage.getItem("settings") || "{}");
      localStorage.setItem("settings", JSON.stringify({ ...settings, themeMode: mode }));
    }, themeMode);
    await page.emulateMedia({ colorScheme: themeMode });
    await page.reload();
    await page.setViewportSize({ width: 1440, height: 900 });
    const selector = page.getByRole("combobox", { name: /^Version/ });
    await expect(selector).toBeVisible();
    const bounds = await selector.boundingBox();
    const note = await page.getByText("Viewing immutable version v1", { exact: true }).boundingBox();
    assert(bounds.width < 250, "Files version selector should fit its content, not a 320px column");
    assert(Math.abs(note.y - bounds.y) < 32 && note.x > bounds.x + bounds.width, "Version note stays inline with the compact selector");
    const folder = page.getByRole("treeitem", { name: "references", exact: true });
    await folder.focus();
    await folder.press("Space");
    await expect(folder).toHaveAttribute("aria-expanded", "false");
    await folder.press("Space");
    await expect(folder).toHaveAttribute("aria-expanded", "true");
    await screenshot(`files-${themeMode}`);
    await page.getByRole("button", { name: "Skill actions" }).click();
    await expect(page.getByRole("menuitem", { name: "Copy install command" })).toBeDisabled();
    await expect(page.getByText(/Assign production, or open Use skill/)).toBeVisible();
    await screenshot(`unlabelled-menu-${themeMode}`);
    await page.keyboard.press("Escape");
    await page.getByRole("button", { name: "Use skill", exact: true }).click();
    await page.getByRole("combobox", { name: /^Use label or version/ }).click();
    await page.getByRole("option", { name: "latest", exact: true }).click();
    for (const width of [1440, 390]) {
      await page.setViewportSize({ width, height: 900 });
      await expect(page.locator("pre")).toHaveCount(2);
      for (let index = 0; index < 2; index += 1) {
        const block = page.locator("pre").nth(index);
        const text = await block.textContent();
        assert(!text.includes("\n") && text.includes("&& rm"), "Visual wraps preserve one complete shell command");
        const geometry = await block.evaluate((pre) => {
          const style = getComputedStyle(pre);
          const copy = pre.parentElement.querySelector('button[aria-label="Copy code"]').getBoundingClientRect();
          const range = document.createRange();
          range.selectNodeContents(pre);
          return { whiteSpace: style.whiteSpace, overflowWrap: style.overflowWrap, fits: pre.scrollWidth <= pre.clientWidth, clearOfCopy: [...range.getClientRects()].every((rect) => rect.right <= copy.left) };
        });
        assert.deepEqual(geometry, { whiteSpace: "pre-wrap", overflowWrap: "anywhere", fits: true, clearOfCopy: true });
        await page.getByRole("button", { name: "Copy code" }).nth(index).click();
        assert.equal(await page.evaluate(() => navigator.clipboard.readText()), text);
      }
      assert(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth), "No horizontal page overflow");
      await screenshot(`install-${themeMode}-${width}`);
    }
  }

  await actor.api("/model-hub/skills/publish/", { method: "POST", body: { files: [{ path: "SKILL.md", content: `${content}\nEscalate exceptional requests.\n` }, { path: "references/policy.md", content: "# Refund policy\nReview the purchase date and eligibility.\n" }] } });
  for (let index = 0; index < 25; index += 1) {
    await actor.api(`${skillUrl}labels/production/`, { method: "PUT", body: { version: index % 2 + 1, note: index === 0 ? "Initial promotion" : `Reviewed change ${index}` } });
  }
  await actor.api(`${skillUrl}labels/production/`, { method: "DELETE" });
  for (const themeMode of ["light", "dark"]) {
    await page.evaluate((mode) => {
      const settings = JSON.parse(localStorage.getItem("settings") || "{}");
      localStorage.setItem("settings", JSON.stringify({ ...settings, themeMode: mode }));
    }, themeMode);
    await page.setViewportSize({ width: 1440, height: 900 });
    await page.goto(`/dashboard/skills/${id}?tab=versions`);
    await expect(page.getByRole("button", { name: "History for production" })).toHaveCount(0);
    await page.getByRole("button", { name: "Label history", exact: true }).click();
    const drawer = page.getByRole("dialog", { name: "Label history", exact: true });
    await expect(drawer.getByText("production: v1 → Removed", { exact: true })).toBeVisible();
    await screenshot(`history-removal-${themeMode}`);
    await drawer.getByRole("button", { name: "Older changes" }).click();
    await expect(drawer.getByText("Initial promotion", { exact: true })).toBeVisible();
    await expect(drawer.getByText("Page 2 of 2", { exact: true })).toBeVisible();
    await screenshot(`history-older-${themeMode}`);
    await page.setViewportSize({ width: 390, height: 900 });
    await expect(drawer.getByRole("button", { name: "Newer changes" })).toBeVisible();
    await screenshot(`history-older-${themeMode}-390`);
  }
  assert.deepEqual(errors, [], "No browser runtime exceptions");
  process.stdout.write(`PASS: production guard, complete Space activation, compact Files selector, wrapped copyable commands, removed-label history and event 26; light/dark screenshots in ${output}\n`);
} finally {
  await browser.close();
  await actor.api(skillUrl, { method: "DELETE" });
}
