#!/usr/bin/env node
/* eslint-env node */
/* eslint-disable no-console -- a command-line walk: its report is what it prints */
// The code panels in real browsers, one at a time (parity P11 acceptance 5):
//   PLAYWRIGHT_CORE=<a playwright-core dir> node walk.mjs [chromium,webkit,firefox] [out dir]
// Each browser opens every Sandboxes and Deployments page of the harness
// (main.jsx, served here by Vite) at 1280 and 390 wide, light and dark, and:
//   - the Code button sits inside the viewport and the page never scrolls
//     sideways; the drawer's snippet is curl to this origin's /omega/api,
//     scrolls inside its own box, and Copy (in view) copies exactly what is
//     shown, the key read from the shell;
//   - a key created through the panel (Future AGI's reply answered here) is
//     masked, then revealed only on the explicit Reveal, copied in full only
//     then, and masked again on Hide;
//   - the sandbox create drawer's and the deployment wizard's Show as code
//     follow the form; the wizard's stays open from Source to Settings;
//   - a workspace member (EE refuses them every deployment write) is offered
//     reads only in a deployment's Code drawer, and the wizard's Show as code
//     says who can deploy instead of showing a request;
//   - the Python and TypeScript tabs are absent until their parts are
//     released, and then lead with the exact install lines.
// Writes receipt.json and screenshots to the out dir; exits 1 on any failed
// check. Nothing leaves this machine: every request outside the harness's own
// origin is refused.
import { mkdirSync, writeFileSync } from "node:fs";
import os from "node:os";
import path from "node:path";
import { fileURLToPath, pathToFileURL } from "node:url";
import { createServer } from "vite";

const HERE = path.dirname(fileURLToPath(import.meta.url));
const CORE = process.env.PLAYWRIGHT_CORE;
if (!CORE) {
  console.error(
    "Set PLAYWRIGHT_CORE to a playwright-core directory matching PLAYWRIGHT_BROWSERS_PATH.",
  );
  process.exit(2);
}
const playwright = await import(
  pathToFileURL(path.join(CORE, "index.mjs")).href
);
const BROWSERS = (process.argv[2] || "chromium,webkit,firefox").split(",");
const OUT = path.resolve(
  process.argv[3] || path.join(os.tmpdir(), "code-panels-walk"),
);
mkdirSync(OUT, { recursive: true });

const KEY = {
  key_id: "55555555-5555-4555-8555-555555555555",
  key_name: "Code snippets 2026-10-22 12:00:00",
  api_key: "e2e2e2e2e2e2e2e2e2e2e2e2e2e2ghij",
  secret_key: "f3f3f3f3f3f3f3f3f3f3f3f3f3f3klmn",
};
const WIDTHS = [1280, 390];
const MODES = ["light", "dark"];
const ALL_SCENES = [
  "sandboxes-list",
  "sandbox-detail",
  "sandbox-detail-member",
  "sandbox-settings",
  "sandbox-create",
  "deployments-list",
  "deployment-detail",
  "deployment-secrets",
  "deployment-new",
  "deployment-detail-member",
  "deployment-new-member",
];
const SCENES = process.env.WALK_SCENES
  ? process.env.WALK_SCENES.split(",")
  : ALL_SCENES;
const SDK_PARTS = "sdk_python,sdk_typescript";

const receipt = {
  schemaVersion: "code-panels-walk/v1",
  startedAt: new Date().toISOString(),
  host: {
    loadAverage: os.loadavg().map((n) => Number(n.toFixed(2))),
    cpus: os.cpus().length,
  },
  playwright: JSON.parse(
    await (
      await import("node:fs/promises")
    ).readFile(path.join(CORE, "package.json"), "utf8"),
  ).version,
  browsers: {},
  checks: [],
};
function check(where, id, pass, seen) {
  receipt.checks.push({ ...where, id, pass: Boolean(pass), seen });
  if (!pass)
    console.log(
      `FAIL ${where.browser} ${where.mode} ${where.width} ${where.scene} ${id} ${JSON.stringify(seen)}`,
    );
  return Boolean(pass);
}

const server = await createServer({
  configFile: path.join(HERE, "vite.config.mjs"),
  cacheDir: path.join(OUT, ".vite"),
  server: { port: 0 },
});
await server.listen();
const origin = server.resolvedUrls.local[0].replace(/\/$/, "");

/** The document never scrolls sideways (1 px of subpixel rounding allowed). */
const sideways = (page) =>
  page.evaluate(
    () =>
      document.documentElement.scrollWidth -
      document.documentElement.clientWidth,
  );

async function inView(page, testId, width) {
  const box = await page.getByTestId(testId).first().boundingBox();
  return {
    box,
    ok: Boolean(box) && box.x >= 0 && box.x + box.width <= width + 1,
  };
}

async function newPage(browser, mode, width) {
  const context = await browser.newContext({
    viewport: { width, height: width < 600 ? 844 : 900 },
    colorScheme: mode,
  });
  // Copy writes here, in every engine (only Chromium grants clipboard reads).
  await context.addInitScript(() => {
    window.__copied = [];
    Object.defineProperty(navigator, "clipboard", {
      configurable: true,
      value: { writeText: async (text) => window.__copied.push(text) },
    });
  });
  const page = await context.newPage();
  await page.route("**/*", (route) => {
    const url = new URL(route.request().url());
    if (url.origin !== origin) return route.abort();
    if (url.pathname === "/accounts/key/generate_secret_key/") {
      return route.fulfill({
        status: 200,
        contentType: "application/json",
        body: JSON.stringify({ status: true, result: KEY }),
      });
    }
    if (
      url.pathname.startsWith("/accounts/") ||
      url.pathname.startsWith("/omega/")
    )
      return route.abort();
    return route.continue();
  });
  return { context, page };
}

const copied = (page) => page.evaluate(() => window.__copied.at(-1) ?? null);
const snippet = (page) =>
  page.getByTestId("code-snippet").first().textContent();

/** Wait until the Code drawer has slid all the way in (MUI leaves no transform). */
const drawerIn = (page) =>
  page.waitForFunction(() => {
    const paper = document
      .querySelector("[data-testid=code-drawer]")
      ?.closest(".MuiDrawer-paper");
    return (
      Boolean(paper) && ["none", ""].includes(getComputedStyle(paper).transform)
    );
  });

async function checkSnippet(page, where, { prefix = "curl -sS" } = {}) {
  const text = (await snippet(page)) ?? "";
  check(
    where,
    "snippet is curl to this origin",
    text.startsWith(prefix) && text.includes(`'${origin}/omega/api/workspace/`),
    text.split("\n")[0],
  );
  const pre = await page
    .getByTestId("code-snippet")
    .first()
    .evaluate((el) => ({
      scrolls:
        el.scrollWidth > el.clientWidth
          ? getComputedStyle(el).overflowX
          : "fits",
      right: el.getBoundingClientRect().right,
    }));
  check(
    where,
    "snippet scrolls inside its own box",
    pre.scrolls === "fits" || pre.scrolls === "auto",
    pre,
  );
  const copy = await inView(page, "code-copy", where.width);
  check(where, "Copy is in view", copy.ok, copy.box);
  await page.getByTestId("code-copy").first().click();
  check(
    where,
    "Copy copies what is shown",
    (await copied(page)) === text,
    null,
  );
  return text;
}

async function keyFlow(page, where) {
  await page.getByTestId("code-create-key").click();
  await page.getByTestId("code-reveal-key").waitFor();
  let text = await snippet(page);
  check(
    where,
    "a new key is masked",
    !text.includes(KEY.api_key) &&
      !text.includes(KEY.secret_key) &&
      text.includes(KEY.api_key.slice(-4)) &&
      !text.includes(KEY.secret_key.slice(-4)),
    null,
  );
  await page.getByTestId("code-copy").click();
  check(
    where,
    "Copy before Reveal reads the shell",
    (await copied(page)).includes('"X-Secret-Key: $FI_SECRET_KEY"'),
    null,
  );
  await page.screenshot({
    path: path.join(
      OUT,
      `${where.browser}-${where.mode}-${where.width}-${where.scene}-masked.png`,
    ),
  });
  // A copy the browser refuses while the key is masked on screen: the message
  // says to reveal it first, since the masked command would not run (fix
  // round 2), and stays in view.
  await page.evaluate(() => {
    window.__clipboard = navigator.clipboard;
    window.__execCommand = document.execCommand;
    Object.defineProperty(navigator, "clipboard", {
      configurable: true,
      value: undefined,
    });
    document.execCommand = () => false;
  });
  await page.getByTestId("code-copy").click();
  await page.getByTestId("code-copy-failed").waitFor();
  const failed = await inView(page, "code-copy-failed", where.width);
  check(
    where,
    "a refused copy of a masked key says to reveal it first",
    failed.ok &&
      /Reveal the key, then select the command/.test(
        await page.getByTestId("code-copy-failed").textContent(),
      ),
    failed.box,
  );
  await page.screenshot({
    path: path.join(
      OUT,
      `${where.browser}-${where.mode}-${where.width}-${where.scene}-copy-failed.png`,
    ),
  });
  await page.evaluate(() => {
    Object.defineProperty(navigator, "clipboard", {
      configurable: true,
      value: window.__clipboard,
    });
    document.execCommand = window.__execCommand;
  });
  await page.getByTestId("code-reveal-key").click();
  text = await snippet(page);
  check(
    where,
    "Reveal shows the key in headers",
    text.includes(`X-Secret-Key: ${KEY.secret_key}`) &&
      !text.split("\n")[0].includes(KEY.secret_key),
    null,
  );
  await page.getByTestId("code-copy").click();
  check(
    where,
    "Copy after Reveal carries the key",
    (await copied(page)).includes(`-H 'X-Secret-Key: ${KEY.secret_key}'`),
    null,
  );
  await page.getByTestId("code-reveal-key").click();
  check(
    where,
    "Hide masks it again",
    !(await snippet(page)).includes(KEY.secret_key),
    null,
  );
  const stored = await page.evaluate(() =>
    JSON.stringify({ ...localStorage, ...sessionStorage }),
  );
  check(
    where,
    "the key is in no browser storage",
    !stored.includes(KEY.secret_key),
    null,
  );
  await page.getByTestId("code-forget-key").click();
}

async function walkScene(browser, where) {
  const { context, page } = await newPage(browser, where.mode, where.width);
  try {
    await page.goto(`${origin}/?scene=${where.scene}&mode=${where.mode}`);
    const shot = (name) =>
      page.screenshot({
        path: path.join(
          OUT,
          `${where.browser}-${where.mode}-${where.width}-${where.scene}${name}.png`,
        ),
      });
    if (where.scene === "sandbox-create") {
      await page.getByTestId("new-sandbox").first().click();
      const drawer = page.getByTestId("create-sandbox-drawer");
      await drawer.waitFor();
      await drawer.getByTestId("show-as-code").click();
      const before = await checkSnippet(page, where);
      check(
        where,
        "the drawer's request is a create",
        before.includes("-X POST") &&
          before.includes("Idempotency-Key: fa-sbx-"),
        null,
      );
      await drawer.getByLabel(/name/i).first().fill("walk-sandbox");
      check(
        where,
        "Show as code follows the name",
        (await snippet(page)).includes('"name":"walk-sandbox"'),
        null,
      );
      const overflow = await drawer.evaluate(
        (el) => el.scrollWidth - el.clientWidth,
      );
      check(
        where,
        "the drawer never scrolls sideways",
        overflow <= 1,
        overflow,
      );
      await drawer.getByTestId("show-as-code-panel").scrollIntoViewIfNeeded();
      await shot("");
      return;
    }
    if (where.scene === "deployment-new-member") {
      await page.getByTestId("show-as-code").click();
      await page.getByTestId("sample-use").click();
      await page.getByTestId("bundle-summary").waitFor();
      const blocked = page.getByTestId("show-as-code-blocked");
      check(
        where,
        "a member sees who can deploy, not a request",
        (await blocked.isVisible()) &&
          (await blocked.textContent()).startsWith(
            "Only workspace owners and admins can deploy",
          ) &&
          (await page.getByTestId("code-snippet").count()) === 0,
        null,
      );
      const side = await sideways(page);
      check(where, "the page never scrolls sideways", side <= 1, side);
      await shot("");
      return;
    }
    if (where.scene === "deployment-new") {
      await page.getByTestId("show-as-code").click();
      check(
        where,
        "no request before a project",
        await page.getByTestId("show-as-code-blocked").isVisible(),
        null,
      );
      await page.getByTestId("sample-use").click();
      await page.getByTestId("bundle-summary").waitFor();
      const text = await checkSnippet(page, where);
      check(
        where,
        "the deploy reads its body from the downloaded file",
        text.includes("--data-binary '@omega-deploy-request.json'"),
        null,
      );
      const download = await inView(page, "code-download-body", where.width);
      check(where, "the body download is in view", download.ok, download.box);
      await page.getByTestId("wizard-continue").click();
      await page.getByTestId("deployment-name").waitFor();
      check(
        where,
        "Show as code stays open on Settings",
        await page.getByTestId("code-snippet").isVisible(),
        null,
      );
      check(
        where,
        "Continue is enabled on Settings",
        await page.getByTestId("wizard-continue").isEnabled(),
        null,
      );
      const side = await sideways(page);
      check(where, "the page never scrolls sideways", side <= 1, side);
      await shot("");
      return;
    }
    const open = await page.getByTestId("code-open").first();
    await open.waitFor();
    const button = await inView(page, "code-open", where.width);
    check(where, "the Code button is in view", button.ok, button.box);
    const side = await sideways(page);
    check(where, "the page never scrolls sideways", side <= 1, side);
    await shot("-page");
    await open.click();
    await page.getByTestId("code-drawer").waitFor();
    await drawerIn(page);
    await checkSnippet(page, where);
    check(
      where,
      "no SDK tab before publication",
      (await page.getByTestId("code-tab-python").count()) === 0 &&
        (await page.getByTestId("code-tab-typescript").count()) === 0,
      null,
    );
    if (where.scene === "deployment-detail-member") {
      await page.getByTestId("code-panel").getByRole("combobox").click();
      const options = await page.getByRole("option").allTextContents();
      check(
        where,
        "a member is offered reads only",
        JSON.stringify(options) ===
          JSON.stringify([
            "Read this deployment",
            "Check its health",
            "Build log of revision 2 (streamed)",
          ]),
        options,
      );
      await page.keyboard.press("Escape");
    }
    if (where.scene === "sandbox-detail") {
      // The Owner (POLISH-FA): may not be the creator, so no creator-only call.
      const note = await inView(page, "code-note", where.width);
      check(
        where,
        "an owner is told why the creator-only calls are not listed",
        note.ok &&
          (await page.getByTestId("code-note").textContent()).startsWith(
            "Only the member who created a sandbox can run commands in it",
          ),
        note.box,
      );
      await page.getByTestId("code-panel").getByRole("combobox").click();
      const options = await page.getByRole("option").allTextContents();
      check(
        where,
        "an owner is offered reads and delete only",
        JSON.stringify(options) ===
          JSON.stringify([
            "Read this sandbox",
            "List files in /workspace",
            "List snapshots",
            "Read the activity",
            "Delete this sandbox",
          ]),
        options,
      );
      await page.keyboard.press("Escape");
    }
    if (where.scene === "sandbox-detail-member") {
      check(
        where,
        "the creator is told nothing is left out",
        (await page.getByTestId("code-note").count()) === 0,
        null,
      );
      await page.getByTestId("code-panel").getByRole("combobox").click();
      const options = await page.getByRole("option").allTextContents();
      check(
        where,
        "the creator is offered every call the page offers, exec first on Run",
        JSON.stringify(options) ===
          JSON.stringify([
            "Run a command (streamed)",
            "Read this sandbox",
            "List files in /workspace",
            "List snapshots",
            "Take a snapshot",
            "Read the activity",
            "Hibernate",
            "Delete this sandbox",
          ]),
        options,
      );
      await page.keyboard.press("Escape");
    }
    if (where.scene === "sandbox-detail") {
      await page.getByTestId("code-panel").getByRole("combobox").click();
      await page.getByRole("option", { name: "Delete this sandbox" }).click();
      check(
        where,
        "a delete says it acts at once",
        await page.getByTestId("code-destructive").isVisible(),
        null,
      );
      await keyFlow(page, where);
    }
    const drawerSide = await sideways(page);
    check(
      where,
      "the drawer never makes the page scroll sideways",
      drawerSide <= 1,
      drawerSide,
    );
    await shot("-drawer");
  } catch (error) {
    check(
      where,
      "the scene ran",
      false,
      String(error?.message ?? error).split("\n")[0],
    );
  } finally {
    await context.close();
  }
}

async function walkSdkTabs(browser, where) {
  const { context, page } = await newPage(browser, where.mode, where.width);
  try {
    await page.goto(
      `${origin}/?scene=sandboxes-list&mode=${where.mode}&parts=${SDK_PARTS}`,
    );
    await page.getByTestId("code-open").first().click();
    await drawerIn(page);
    await page.getByTestId("code-tab-python").click();
    check(
      where,
      "the Python tab leads with its install line",
      (await snippet(page)).split("\n")[0] === "pip install futureagi",
      null,
    );
    await page.getByTestId("code-tab-typescript").click();
    check(
      where,
      "the TypeScript tab leads with its install line",
      (await snippet(page)).split("\n")[0] === "npm i @future-agi/sdk",
      null,
    );
    const side = await sideways(page);
    check(where, "the page never scrolls sideways", side <= 1, side);
    await page.screenshot({
      path: path.join(
        OUT,
        `${where.browser}-${where.mode}-${where.width}-sdk-tabs.png`,
      ),
    });
  } catch (error) {
    check(
      where,
      "the scene ran",
      false,
      String(error?.message ?? error).split("\n")[0],
    );
  } finally {
    await context.close();
  }
}

try {
  for (const name of BROWSERS) {
    const browser = await playwright[name].launch({ headless: true });
    receipt.browsers[name] = browser.version();
    try {
      for (const mode of MODES) {
        for (const width of WIDTHS) {
          for (const scene of SCENES)
            await walkScene(browser, { browser: name, mode, width, scene });
          await walkSdkTabs(browser, {
            browser: name,
            mode,
            width,
            scene: "sdk-tabs (released)",
          });
        }
      }
    } finally {
      await browser.close();
    }
    const mine = receipt.checks.filter((c) => c.browser === name);
    console.log(
      `WALK code-panels browser=${name} ${mine.filter((c) => c.pass).length}/${mine.length} checks`,
    );
  }
} finally {
  await server.close();
}
receipt.finishedAt = new Date().toISOString();
const failed = receipt.checks.filter((c) => !c.pass);
receipt.verdict = failed.length === 0 ? "pass" : "fail";
writeFileSync(
  path.join(OUT, "receipt.json"),
  `${JSON.stringify(receipt, null, 1)}\n`,
);
console.log(
  `WALK code-panels ${receipt.verdict} checks=${receipt.checks.length} failed=${failed.length} out=${OUT}`,
);
process.exit(failed.length === 0 ? 0 : 1);
