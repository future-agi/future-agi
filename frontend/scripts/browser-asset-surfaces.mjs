// CLOUD ONLY. Real application routes with synthetic localhost API fixtures.
import fs from "node:fs";
import path from "node:path";
import {
  consentBindings,
  openOfficePreview,
  mountYoutubeConsent,
} from "./browser-asset-consent.mjs";

export function surfaceResponse(url) {
  const p = url.pathname;
  if (p.includes("/api/capabilities/"))
    return {
      features: Object.fromEntries(
        [
          "voice_sim",
          "synthetic_data",
          "optimization",
          "falcon_ai",
          "agentic_eval",
        ].map((id) => [id, { allowed: true }]),
      ),
    };
  if (p.includes("provider-status"))
    return {
      status: true,
      result: {
        providers: [
          {
            provider: "openai",
            display_name: "OpenAI",
            type: "text",
            has_key: true,
            masked_key: "fixture-redacted",
            logo_url:
              "https://fi-image-assets.s3.ap-south-1.amazonaws.com/provider-logos/openai-icon.png",
          },
          {
            provider: "unknown_fixture",
            display_name: "Unknown fixture provider",
            type: "text",
            has_key: false,
            logo_url:
              "https://fi-content-dev.s3.ap-south-1.amazonaws.com/untrusted-logo.png",
          },
        ],
      },
    };
  if (p.includes("models_list"))
    return {
      results: [
        {
          model_name: "gpt-5.5",
          providers: "openai",
          is_available: true,
          type: "chat",
          logo_url:
            "https://fi-image-assets.s3.ap-south-1.amazonaws.com/provider-logos/openai-icon.png",
        },
        {
          model_name: "fixture-local",
          providers: "unknown_fixture",
          is_available: true,
          type: "chat",
          logo_url:
            "https://fi-content-dev.s3.ap-south-1.amazonaws.com/untrusted-logo.png",
        },
      ],
      next: null,
      current_page: 1,
      count: 2,
    };
  if (p.includes("knowledge-base/get"))
    return { status: true, result: { table_data: [], total_rows: 0 } };
  if (
    p.endsWith("/model-hub/knowledge-base/") &&
    url.searchParams.get("type") === "create"
  )
    return {
      status: true,
      result: {
        code: "# Local asset fixture\nprint('Knowledge base SDK example')",
      },
    };
  return null;
}

export async function probeSurfaces(page, browser, out, dist) {
  const requests = [],
    errors = [],
    checks = [];
  let phase = "initial";
  const onRequest = (r) =>
    requests.push({ phase, url: r.url(), type: r.resourceType() });
  const onError = (e) => errors.push({ phase, message: e.message });
  page.on("request", onRequest);
  page.on("pageerror", onError);
  const external = (r) =>
    !["127.0.0.1", "localhost", "[::1]"].includes(new URL(r.url).hostname);
  async function check(name, action) {
    phase = name;
    const start = requests.length,
      errorStart = errors.length;
    const record = { name, pass: false };
    try {
      await action(record);
      record.pass = record.pass && errors.length === errorStart;
    } catch (e) {
      record.error = e.message;
    }
    record.finalUrl = page.url();
    record.requests = requests.slice(start);
    record.text = (
      await page
        .locator("body")
        .innerText()
        .catch(() => "")
    ).slice(0, 3000);
    record.screenshot = `${browser}-${name}.png`;
    await page
      .screenshot({ path: path.join(out, record.screenshot) })
      .catch(() => {});
    checks.push(record);
  }
  for (const [name, route, host] of [
    ["arcade-consent", "/dashboard/knowledge", "demo.arcade.software"],
    ["loom-consent", "/dashboard/simulate/scenarios", "www.loom.com"],
  ])
    await check(name, async (record) => {
      const start = requests.length;
      await page.goto(`http://127.0.0.1:5188${route}`, {
        waitUntil: "networkidle",
        timeout: 45000,
      });
      const button = page
        .getByRole("button", { name: "Load external content", exact: true })
        .filter({ visible: true })
        .first();
      await button.waitFor({ state: "visible", timeout: 10000 });
      record.automaticExternal = requests.slice(start).filter(external);
      record.beforeFrames = await page.locator('iframe[src^="http"]').count();
      const clickStart = requests.length;
      phase = `${name}:explicit-click`;
      await button.click();
      await page.waitForTimeout(800);
      record.explicitExternal = requests.slice(clickStart).filter(external);
      record.namedHost = host;
      record.pass =
        record.automaticExternal.length === 0 &&
        record.beforeFrames === 0 &&
        record.explicitExternal.length === 1 &&
        record.explicitExternal[0].type === "document" &&
        new URL(record.explicitExternal[0].url).hostname === host;
    });
  await check("provider-settings", async (record) => {
    const start = requests.length;
    await page.goto("http://127.0.0.1:5188/dashboard/settings/ai-providers", {
      waitUntil: "networkidle",
      timeout: 45000,
    });
    await page
      .getByText("Unknown fixture provider", { exact: true })
      .first()
      .waitFor({ timeout: 15000 });
    record.images = await page.locator("img").evaluateAll((nodes) =>
      nodes.map((n) => ({
        src: n.getAttribute("src"),
        loaded: n.complete && n.naturalWidth > 0,
      })),
    );
    record.external = requests.slice(start).filter(external);
    record.pass =
      record.external.length === 0 &&
      record.images.some(
        (i) => i.src === "/icons/settings/openai.png" && i.loaded,
      ) &&
      record.images.some(
        (i) => i.src === "/assets/icons/settings/AIProviders.svg" && i.loaded,
      );
  });
  await check("monaco-knowledge-sdk", async (record) => {
    const start = requests.length;
    await page.goto("http://127.0.0.1:5188/dashboard/knowledge", {
      waitUntil: "networkidle",
      timeout: 45000,
    });
    await page
      .getByRole("button", { name: "Create Knowledge Base", exact: true })
      .filter({ visible: true })
      .first()
      .click();
    await page
      .getByRole("tab", { name: "Import from SDK", exact: true })
      .click();
    await page
      .locator(".monaco-editor")
      .first()
      .waitFor({ state: "visible", timeout: 30000 });
    await page.waitForFunction(
      () =>
        window.monaco?.editor
          ?.getModels()
          .some((model) =>
            model.getValue().includes("Knowledge base SDK example"),
          ),
      null,
      { timeout: 15000 },
    );
    record.editor = await page.evaluate(async () => {
      const model = window.monaco.editor
        .getModels()
        .find((m) => m.getValue().includes("Knowledge base SDK example"));
      const worker = await window.monaco.editor.createWebWorker({
        moduleId: "vs/language/json/jsonWorker",
        createData: { languageSettings: {} },
      });
      try {
        await Promise.race([
          worker.getProxy(),
          new Promise((_, reject) =>
            setTimeout(
              () => reject(new Error("Local Monaco worker timeout")),
              15000,
            ),
          ),
        ]);
      } finally {
        worker.dispose();
      }
      return {
        value: model.getValue(),
        language: model.getLanguageId(),
        editors: document.querySelectorAll(".monaco-editor").length,
        typography: [
          ".monaco-editor .view-lines",
          ".monaco-editor .codicon",
        ].map((selector) => {
          const element = document.querySelector(selector);
          if (!element) return { selector, absent: true };
          const style = getComputedStyle(element);
          return {
            selector,
            fontFamily: style.fontFamily,
            fontSize: style.fontSize,
            fontWeight: style.fontWeight,
            lineHeight: style.lineHeight,
          };
        }),
      };
    });
    record.external = requests.slice(start).filter(external);
    record.assets = requests
      .slice(start)
      .filter((r) => r.url.includes("/assets/monaco/"));
    record.pass =
      record.external.length === 0 &&
      record.editor.value.length > 0 &&
      record.assets.length > 0;
  });
  for (const [name, host, setup, setupNote] of [
    [
      "office-consent",
      "view.officeapps.live.com",
      openOfficePreview,
      "Open the real mounted Office drawer through its production preview-store hook; not a full file-cell journey.",
    ],
    [
      "youtube-consent",
      "www.youtube.com",
      mountYoutubeConsent,
      "Mount the exact emitted consent component with the dormant onboarding YouTube configuration; not a reachable onboarding journey.",
    ],
  ])
    await check(name, async (record) => {
      const start = requests.length;
      record.setup = setupNote;
      record.bindings = consentBindings(dist);
      await page.goto(
        "http://127.0.0.1:5188/dashboard/develop/11111111-1111-4111-8111-111111111111?tab=data",
        { waitUntil: "networkidle", timeout: 45000 },
      );
      await setup(page, record.bindings);
      const button = page
        .getByRole("button", { name: "Load external content", exact: true })
        .filter({ visible: true })
        .first();
      await button.waitFor({ state: "visible", timeout: 15000 });
      record.automaticExternal = requests.slice(start).filter(external);
      record.beforeFrames = await page.locator('iframe[src^="http"]').count();
      const clickStart = requests.length;
      phase = `${name}:explicit-click`;
      await button.click();
      await page.waitForTimeout(800);
      record.explicitExternal = requests.slice(clickStart).filter(external);
      record.namedHost = host;
      record.pass =
        record.automaticExternal.length === 0 &&
        record.beforeFrames === 0 &&
        record.explicitExternal.length === 1 &&
        record.explicitExternal[0].type === "document" &&
        new URL(record.explicitExternal[0].url).hostname === host;
    });
  await check("login", async (record) => {
    const start = requests.length;
    await page.evaluate(() => localStorage.removeItem("accessToken"));
    await page.goto("http://127.0.0.1:5188/auth/jwt/login", {
      waitUntil: "networkidle",
      timeout: 45000,
    });
    record.external = requests.slice(start).filter(external);
    record.pass =
      record.external.length === 0 &&
      (await page.locator('input[name="email"]').count()) > 0;
  });
  page.off("request", onRequest);
  page.off("pageerror", onError);
  const result = {
    browser,
    purpose:
      "Actual app routes; synthetic logo-bearing API fixtures, not backend acceptance. Explicit embed requests are intercepted/aborted, never sent to providers.",
    checks,
    errors,
    requests,
    hosts: [...new Set(requests.map((r) => new URL(r.url).host))].sort(),
  };
  fs.writeFileSync(
    path.join(out, `${browser}-surfaces.json`),
    JSON.stringify(result, null, 2) + "\n",
  );
  return {
    browser,
    check: "supplemental changed surfaces",
    pass: checks.every((c) => c.pass) && errors.length === 0,
  };
}
