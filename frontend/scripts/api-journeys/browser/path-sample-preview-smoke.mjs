/* eslint-disable no-console */
import { Buffer } from "node:buffer";
import { createRequire } from "node:module";
import process from "node:process";
import { existsSync } from "node:fs";
import { getActivationStateFixture } from "../../../src/sections/onboarding-home/fixtures/activation-state.fixtures.js";

const require = createRequire(import.meta.url);
const puppeteer = require("puppeteer-core");

const APP_BASE = process.env.APP_BASE || "http://127.0.0.1:3034";
const PATH = process.env.PATH_SAMPLE_PREVIEW_PATH || "prompt";
const SCREENSHOT_PATH =
  process.env.PATH_SAMPLE_PREVIEW_SCREENSHOT ||
  `/tmp/path-sample-preview-${PATH}.png`;

const USER_ID = "00000000-0000-4000-8000-000000000101";
const ORG_ID = "00000000-0000-4000-8000-000000000201";
const WORKSPACE_ID = "00000000-0000-4000-8000-000000000301";

// Per-path first-run profile: stage 1 of the path-focus plan plus the genuine
// recommended-action setup href (the existing out-to-builder route).
const PROFILES = {
  prompt: {
    flag: "onboarding_prompt_path",
    goal: "improve_prompts",
    stage: "start_prompt",
    actionId: "start_prompt",
    actionHref:
      "/dashboard/workbench/all?source=onboarding&action=create-prompt",
    expectText: ["Refund eligibility question", "Improved", "Regressed"],
  },
  evals: {
    flag: "onboarding_eval_path",
    goal: "evaluate_quality",
    stage: "create_eval_dataset",
    actionId: "create_eval_dataset",
    actionHref: "/dashboard/evaluations/create?source=onboarding&step=data",
    expectText: ["7 pass / 3 fail", "Hallucinated refund amount"],
  },
};

function fakeJwt() {
  const header = base64Url({ alg: "none", typ: "JWT" });
  const payload = base64Url({
    exp: Math.floor(Date.now() / 1000) + 3600,
    sub: USER_ID,
    user_id: USER_ID,
  });
  return `${header}.${payload}.signature`;
}

function base64Url(value) {
  return Buffer.from(JSON.stringify(value))
    .toString("base64")
    .replace(/\+/g, "-")
    .replace(/\//g, "_")
    .replace(/=+$/g, "");
}

function browserExecutablePath() {
  const candidates = [
    "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
    "/usr/bin/google-chrome",
    "/usr/bin/chromium",
  ];
  return candidates.find((p) => existsSync(p)) || candidates[0];
}

function slashPath(path) {
  return path.endsWith("/") ? path : `${path}/`;
}

function corsHeaders() {
  return {
    "Access-Control-Allow-Origin": "*",
    "Access-Control-Allow-Methods": "GET,POST,PUT,PATCH,DELETE,OPTIONS",
    "Access-Control-Allow-Headers": "*",
  };
}

async function respondJson(request, body) {
  await request.respond({
    status: 200,
    headers: { "Content-Type": "application/json", ...corsHeaders() },
    body: JSON.stringify(body),
  });
}

async function respondJavascript(request, body) {
  await request.respond({
    status: 200,
    headers: { "Content-Type": "application/javascript", ...corsHeaders() },
    body,
  });
}

function pathFocusActivationState(profile) {
  const base = getActivationStateFixture("observeNoSetup");
  return {
    ...base,
    request_id: `path_sample_preview_${PATH}`,
    organization_id: ORG_ID,
    workspace_id: WORKSPACE_ID,
    user_id: USER_ID,
    goal: profile.goal,
    primary_path: PATH,
    stage: profile.stage,
    progress: {
      build: "complete",
      test: "selected",
      observe: "not_started",
      ship: "not_started",
      improve: "not_started",
    },
    recommended_action: {
      id: profile.actionId,
      kind: "setup",
      title: "Setup",
      description: "Setup",
      href: profile.actionHref,
      cta_label: "Setup",
      estimated_minutes: 3,
      priority: 100,
      blocked: false,
      blocked_reason: null,
      requires_permission: null,
      completion_event: null,
      is_sample: false,
      route_available: true,
      fallback_href: `/dashboard/home?path=${PATH}`,
      analytics: {
        event_name: "onboarding_recommended_action_clicked",
        source: "home",
        target_path: PATH,
      },
    },
    available_paths: [
      {
        id: PATH,
        label: PATH,
        description: PATH,
        status: "selected",
        href: `/dashboard/home?path=${PATH}`,
        is_available: true,
        blocked_reason: null,
        requires_permission: null,
        first_action_id: profile.actionId,
      },
    ],
    feature_flags: { ...base.feature_flags, [profile.flag]: true },
    route_availability: {
      ...base.route_availability,
      [`path_${PATH}`]: {
        href: `/dashboard/home?path=${PATH}`,
        is_available: true,
        reason: null,
      },
      [profile.actionId]: {
        href: profile.actionHref,
        is_available: true,
        reason: null,
      },
    },
    sample_project: { ...base.sample_project, available: false },
  };
}

async function main() {
  const profile = PROFILES[PATH];
  if (!profile) throw new Error(`Unsupported path: ${PATH}`);

  const pageErrors = [];
  const consoleErrors = [];
  const activationEventPosts = [];

  const browser = await puppeteer.launch({
    executablePath: browserExecutablePath(),
    headless: process.env.HEADLESS !== "0",
    defaultViewport: { width: 1440, height: 1000 },
    args: ["--no-sandbox"],
  });
  const page = await browser.newPage();
  await page.setCacheEnabled(false);
  await page.setBypassServiceWorker(true);

  await page.evaluateOnNewDocument(
    ({ token, orgId, wsId, userId }) => {
      localStorage.setItem("accessToken", token);
      localStorage.setItem("refreshToken", "");
      localStorage.setItem("rememberMe", "true");
      localStorage.setItem("initial-render", "done");
      sessionStorage.setItem("organizationId", orgId);
      sessionStorage.setItem("organizationName", "Preview org");
      sessionStorage.setItem("organizationDisplayName", "Preview org");
      sessionStorage.setItem("organizationRole", "Owner");
      sessionStorage.setItem("workspaceId", wsId);
      sessionStorage.setItem("workspaceName", "Preview workspace");
      sessionStorage.setItem("workspaceDisplayName", "Preview workspace");
      sessionStorage.setItem("workspaceRole", "Owner");
      sessionStorage.setItem("currentUserId", userId);
      sessionStorage.setItem("futureagi-current-user-id", userId);
    },
    { token: fakeJwt(), orgId: ORG_ID, wsId: WORKSPACE_ID, userId: USER_ID },
  );

  page.on("console", (m) => {
    if (m.type() === "error") consoleErrors.push(m.text());
  });
  page.on("pageerror", (e) => pageErrors.push(e.message));

  await page.setRequestInterception(true);
  page.on("request", async (request) => {
    const url = new URL(request.url());
    const path = slashPath(url.pathname);

    if (path === "/config.js/") {
      await respondJavascript(
        request,
        `window.__FUTURE_AGI_CONFIG__ = ${JSON.stringify({
          VITE_HOST_API: APP_BASE,
          VITE_ASSETS_API: APP_BASE,
        })};`,
      );
      return;
    }
    if (request.method() === "OPTIONS") {
      await request.respond({ status: 204, headers: corsHeaders() });
      return;
    }
    if (path === "/accounts/user-info/") {
      await respondJson(request, {
        id: USER_ID,
        email: "preview@example.com",
        name: "Preview",
        organization_id: ORG_ID,
        organization_role: "Owner",
        org_level: 4,
        default_workspace_id: WORKSPACE_ID,
        default_workspace_role: "Owner",
        ws_level: 4,
        onboarding_completed: true,
        requires_org_setup: false,
      });
      return;
    }
    if (path === "/accounts/organization/list/") {
      await respondJson(request, {
        status: true,
        result: {
          organizations: [
            { id: ORG_ID, name: "Preview org", display_name: "Preview org" },
          ],
        },
      });
      return;
    }
    if (path === "/accounts/workspace/list/") {
      await respondJson(request, {
        status: true,
        result: [
          {
            id: WORKSPACE_ID,
            name: "Preview workspace",
            display_name: "Preview workspace",
            organization_id: ORG_ID,
          },
        ],
      });
      return;
    }
    if (path === "/accounts/activation-state/") {
      await respondJson(request, {
        status: true,
        result: pathFocusActivationState(profile),
      });
      return;
    }
    if (path === "/accounts/activation-events/") {
      const body = request.postData() ? JSON.parse(request.postData()) : {};
      activationEventPosts.push(body);
      await respondJson(request, {
        status: true,
        result: {
          event_id: "00000000-0000-4000-8000-000000000188",
          event_name: body?.event_name || "onboarding_home_viewed",
          activation_state: pathFocusActivationState(profile),
        },
      });
      return;
    }
    await request.continue();
  });

  const evidence = { path: PATH };
  const homeRoute =
    process.env.PATH_SAMPLE_PREVIEW_ROUTE ||
    "/dashboard/home?source=onboarding";
  try {
    await page.goto(`${APP_BASE}${homeRoute}`, {
      waitUntil: "domcontentloaded",
    });
    await page.waitForFunction(
      () => window.location.pathname === "/dashboard/home",
      { timeout: 30000 },
    );

    const previewSelector = `[data-testid="path-sample-preview-panel-${PATH}"]`;
    await page.waitForSelector(previewSelector, { timeout: 30000 });

    // The sample content must be visible BEFORE any navigation.
    const urlBefore = page.url();
    for (const text of profile.expectText) {
      const found = await page.evaluate(
        ({ sel, t }) => {
          const el = document.querySelector(sel);
          return Boolean(el && el.textContent.includes(t));
        },
        { sel: previewSelector, t: text },
      );
      if (!found) throw new Error(`Preview text not visible: "${text}"`);
    }

    // The "Now do it with your data" CTA reuses the real setup href.
    const ctaHref = await page.evaluate((sel) => {
      const link = [...document.querySelectorAll(`${sel} a`)].find((a) =>
        /now do it with your data/i.test(a.textContent),
      );
      return link?.getAttribute("href") || null;
    }, previewSelector);
    const ctaMatches = process.env.PATH_SAMPLE_PREVIEW_ROUTE
      ? Boolean(ctaHref && ctaHref.startsWith(profile.actionHref))
      : ctaHref === profile.actionHref;
    if (!ctaMatches) {
      throw new Error(`CTA href ${ctaHref} != expected ${profile.actionHref}`);
    }
    evidence.cta_href = ctaHref;

    // The preview is above the path-focus panel.
    const order = await page.evaluate((p) => {
      const preview = document.querySelector(
        `[data-testid="path-sample-preview-panel-${p}"]`,
      );
      const focus = document.querySelector(
        `[data-testid="path-focus-panel-${p}"]`,
      );
      if (!preview || !focus) return "missing";
      return preview.compareDocumentPosition(focus) &
        Node.DOCUMENT_POSITION_FOLLOWING
        ? "above"
        : "below";
    }, PATH);
    if (order !== "above") {
      throw new Error(`Preview is ${order} the path-focus panel`);
    }
    evidence.preview_position = order;

    await page.screenshot({ path: SCREENSHOT_PATH, fullPage: true });
    evidence.screenshot = SCREENSHOT_PATH;

    // Hide the sample: must be local-only, no navigation, no activation event.
    const eventsBeforeHide = activationEventPosts.length;
    await page.evaluate((sel) => {
      const btn = [...document.querySelectorAll(`${sel} button`)].find((b) =>
        /hide sample/i.test(b.textContent),
      );
      btn?.click();
    }, previewSelector);
    await page.waitForFunction(
      (sel) => !document.querySelector(sel),
      { timeout: 10000 },
      previewSelector,
    );
    const urlAfter = page.url();
    if (urlBefore !== urlAfter) {
      throw new Error(`URL changed on hide: ${urlBefore} -> ${urlAfter}`);
    }
    if (activationEventPosts.length !== eventsBeforeHide) {
      throw new Error(
        `Activation event posted on hide (before=${eventsBeforeHide}, after=${activationEventPosts.length})`,
      );
    }
    evidence.url_unchanged_on_hide = true;
    evidence.activation_events_on_hide = 0;

    if (pageErrors.length) {
      throw new Error(`Page errors: ${pageErrors.join("; ")}`);
    }

    console.log(
      JSON.stringify(
        { status: "passed", app_base: APP_BASE, evidence },
        null,
        2,
      ),
    );
  } catch (error) {
    try {
      await page.screenshot({
        path: SCREENSHOT_PATH.replace(/\.png$/, "-failure.png"),
        fullPage: true,
      });
    } catch {
      /* ignore */
    }
    console.error(
      JSON.stringify(
        {
          status: "failed",
          app_base: APP_BASE,
          error: error.message,
          page_errors: pageErrors,
          console_errors: consoleErrors.slice(-10),
        },
        null,
        2,
      ),
    );
    throw error;
  } finally {
    await browser.close();
  }
}

main().catch(() => process.exit(1));
