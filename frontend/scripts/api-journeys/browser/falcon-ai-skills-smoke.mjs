/* eslint-disable no-console */
import { execFile } from "node:child_process";
import { createRequire } from "node:module";
import process from "node:process";
import { promisify } from "node:util";
import {
  assert,
  createAuthenticatedContext,
  currentUserId,
  isUuid,
} from "../lib/api-client.mjs";

const require = createRequire(import.meta.url);
const puppeteer = require("puppeteer-core");
const execFileAsync = promisify(execFile);

const APP_BASE = process.env.APP_BASE || "http://127.0.0.1:3032";
const SCREENSHOT_PATH =
  process.env.FALCON_AI_SKILLS_SCREENSHOT || "/tmp/falcon-ai-skills-smoke.png";
const FAILURE_SCREENSHOT_PATH =
  process.env.FALCON_AI_SKILLS_FAILURE_SCREENSHOT ||
  "/tmp/falcon-ai-skills-smoke-failure.png";

async function main() {
  const auth = await createAuthenticatedContext();
  const userId = currentUserId(auth.user);
  assert(isUuid(userId), "Falcon skills smoke requires current user id.");

  const suffix = auth.runId.replace(/[^a-z0-9]/gi, "").slice(0, 12);
  const marker = `falcon-ui-skill-${suffix}`;
  const skillName = marker;
  const skillDescription = `Browser skill lifecycle ${auth.runId}`;
  const updatedDescription = `Updated browser skill lifecycle ${auth.runId}`;
  const skillInstructions = `Use the ${marker} process carefully.`;
  const updatedInstructions = `Use the updated ${marker} process carefully.`;
  const triggerPhrase = `${marker} trigger`;

  let browser = null;
  let skillId = null;
  let caughtError = null;
  const apiFailures = [];
  const pageErrors = [];
  const falconRequests = [];
  const evidence = { skill_name: skillName, skill_slug: marker };

  try {
    await hardDeleteFalconSkillFixtures({
      marker,
      organizationId: auth.organizationId,
    });

    browser = await puppeteer.launch({
      executablePath: browserExecutablePath(),
      headless: process.env.HEADLESS !== "0",
      defaultViewport: { width: 1440, height: 950 },
      args: ["--no-sandbox"],
    });
    const page = await browser.newPage();
    await page.setBypassServiceWorker(true);
    await installRuntimeConfig(page, auth);
    await installAuthState(page, auth);
    await installBrowserHelpers(page);

    page.on("request", (request) => {
      if (isFalconApiUrl(request.url())) {
        falconRequests.push(`${request.method()} ${request.url()}`);
      }
    });
    page.on("response", (response) => {
      if (isFalconApiUrl(response.url()) && response.status() >= 400) {
        apiFailures.push(`${response.status()} ${response.url()}`);
      }
    });
    page.on("pageerror", (error) => pageErrors.push(error.message));

    await waitForResponseDuring(
      page,
      "Falcon page list load",
      (response) =>
        response.url().includes("/falcon-ai/conversations/") &&
        response.request().method() === "GET" &&
        response.status() < 400,
      () =>
        page.goto(`${APP_BASE}/dashboard/falcon-ai`, {
          waitUntil: "domcontentloaded",
        }),
    );
    await waitForVisibleText(page, "Falcon AI", { exact: true });
    await waitForVisibleText(page, "Customize", { exact: true });

    await waitForResponseDuring(
      page,
      "Falcon customize skills load",
      (response) =>
        response.url().includes("/falcon-ai/skills/") &&
        response.request().method() === "GET" &&
        response.status() < 400,
      () => clickDeepestVisibleText(page, "Customize"),
    );
    await waitForVisibleText(page, "Skills", { exact: true });
    await waitForVisibleText(page, "Create Skill", { exact: true });
    assert(
      await pageHasVisibleText(page, "Connectors"),
      "Falcon Customize panel did not render the Connectors tab.",
    );
    evidence.memory_ui_visible = await pageHasVisibleText(page, "Memory");

    await clickCreateSkill(page);
    await waitForVisibleText(page, "Create Skill", { exact: true });
    await typeByPlaceholder(page, "e.g. Build a Dataset", skillName);
    await typeByPlaceholder(page, "What does this skill do?", skillDescription);
    await typeByPlaceholder(
      page,
      "Write detailed instructions for how Falcon should behave when this skill is active...",
      skillInstructions,
    );
    await typeByPlaceholder(page, "e.g. /build-dataset", triggerPhrase);
    await page.keyboard.press("Enter");
    await waitForVisibleText(page, triggerPhrase);

    const createResponse = await waitForResponseDuring(
      page,
      "Falcon skill create",
      (response) =>
        response.url().includes("/falcon-ai/skills/") &&
        response.request().method() === "POST" &&
        response.status() < 400,
      () => clickVisibleButtonText(page, "Save"),
    );
    const createdSkill = unwrapApiEnvelope(await createResponse.json());
    skillId = createdSkill?.id;
    assert(isUuid(skillId), "Falcon skill create returned no skill id.");
    assert(
      createdSkill.name === skillName &&
        createdSkill.slug === marker &&
        createdSkill.description === skillDescription &&
        createdSkill.instructions === skillInstructions,
      `Falcon skill create response mismatch: ${JSON.stringify(createdSkill)}`,
    );
    evidence.created_skill_id = skillId;

    await waitForVisibleText(page, skillName, { exact: true });
    await waitForResponseDuring(
      page,
      "Falcon skill detail",
      (response) =>
        response.url().includes(`/falcon-ai/skills/${skillId}/`) &&
        response.request().method() === "GET" &&
        response.status() < 400,
      () => clickDeepestVisibleText(page, skillName),
    );
    await waitForVisibleText(page, skillDescription);
    await waitForVisibleText(page, skillInstructions);
    await waitForVisibleText(page, triggerPhrase);

    const createAudit = await loadFalconSkillDbAudit({
      skillId,
      marker,
      organizationId: auth.organizationId,
      workspaceId: auth.workspaceId,
      userId,
      description: skillDescription,
      instructions: skillInstructions,
      triggerPhrase,
    });
    assert(
      createAudit.skill_count === 1 &&
        createAudit.active_workspace_skill_count === 1 &&
        createAudit.user_skill_count === 1 &&
        createAudit.description_match_count === 1 &&
        createAudit.instructions_match_count === 1 &&
        createAudit.trigger_phrase_match_count === 1 &&
        createAudit.deleted_skill_count === 0,
      `Falcon skill create DB audit mismatch: ${JSON.stringify(createAudit)}`,
    );
    evidence.create_db_audit = createAudit;

    await clickVisibleButtonText(page, "Edit skill");
    await waitForVisibleText(page, "Edit Skill", { exact: true });
    await replaceByPlaceholder(
      page,
      "What does this skill do?",
      updatedDescription,
    );
    await replaceByPlaceholder(
      page,
      "Write detailed instructions for how Falcon should behave when this skill is active...",
      updatedInstructions,
    );

    const updateResponse = await waitForResponseDuring(
      page,
      "Falcon skill update",
      (response) =>
        response.url().includes(`/falcon-ai/skills/${skillId}/`) &&
        response.request().method() === "PATCH" &&
        response.status() < 400,
      () => clickVisibleButtonText(page, "Save"),
    );
    const updatedSkill = unwrapApiEnvelope(await updateResponse.json());
    assert(
      updatedSkill?.id === skillId &&
        updatedSkill.description === updatedDescription &&
        updatedSkill.instructions === updatedInstructions,
      `Falcon skill update response mismatch: ${JSON.stringify(updatedSkill)}`,
    );
    await waitForVisibleText(page, skillName, { exact: true });
    await waitForResponseDuring(
      page,
      "Falcon updated skill detail",
      (response) =>
        response.url().includes(`/falcon-ai/skills/${skillId}/`) &&
        response.request().method() === "GET" &&
        response.status() < 400,
      () => clickDeepestVisibleText(page, skillName),
    );
    await waitForVisibleText(page, updatedDescription);
    await waitForVisibleText(page, updatedInstructions);

    const updateAudit = await loadFalconSkillDbAudit({
      skillId,
      marker,
      organizationId: auth.organizationId,
      workspaceId: auth.workspaceId,
      userId,
      description: updatedDescription,
      instructions: updatedInstructions,
      triggerPhrase,
    });
    assert(
      updateAudit.skill_count === 1 &&
        updateAudit.description_match_count === 1 &&
        updateAudit.instructions_match_count === 1 &&
        updateAudit.deleted_skill_count === 0,
      `Falcon skill update DB audit mismatch: ${JSON.stringify(updateAudit)}`,
    );
    evidence.update_db_audit = updateAudit;

    await page.screenshot({ path: SCREENSHOT_PATH, fullPage: true });
    evidence.screenshot = SCREENSHOT_PATH;

    await clickVisibleButtonText(page, "Edit skill");
    await waitForVisibleText(page, "Edit Skill", { exact: true });
    await waitForResponseDuring(
      page,
      "Falcon skill delete",
      (response) =>
        response.url().includes(`/falcon-ai/skills/${skillId}/`) &&
        response.request().method() === "DELETE" &&
        response.status() < 400,
      () => clickVisibleButtonText(page, "Delete"),
    );
    await waitForNoVisibleText(page, skillName, { timeout: 15000 });

    const deleteAudit = await loadFalconSkillDbAudit({
      skillId,
      marker,
      organizationId: auth.organizationId,
      workspaceId: auth.workspaceId,
      userId,
      description: updatedDescription,
      instructions: updatedInstructions,
      triggerPhrase,
    });
    assert(
      deleteAudit.skill_count === 1 &&
        deleteAudit.deleted_skill_count === 1 &&
        deleteAudit.deleted_at_count === 1,
      `Falcon skill delete DB audit mismatch: ${JSON.stringify(deleteAudit)}`,
    );
    evidence.delete_db_audit = deleteAudit;

    assert(apiFailures.length === 0, `API failures: ${apiFailures.join("; ")}`);
    assert(pageErrors.length === 0, `Page errors: ${pageErrors.join("; ")}`);
    await waitForNoVisibleText(page, "Invalid Date");
    await waitForNoVisibleText(page, "undefined");

    const cleanup = await hardDeleteFalconSkillFixtures({
      marker,
      organizationId: auth.organizationId,
    });
    assert(
      cleanup.remaining_skill_count === 0,
      `Falcon skill hard cleanup left DB rows behind: ${JSON.stringify(cleanup)}`,
    );
    evidence.cleanup = cleanup;

    console.log(
      JSON.stringify(
        {
          status: "passed",
          app_base: APP_BASE,
          api_base: auth.apiBase,
          organization_id: auth.organizationId,
          workspace_id: auth.workspaceId,
          evidence,
          falcon_request_count: falconRequests.length,
        },
        null,
        2,
      ),
    );
  } catch (error) {
    caughtError = error;
    console.error(
      JSON.stringify(
        {
          status: "failed",
          app_base: APP_BASE,
          api_base: auth.apiBase,
          organization_id: auth.organizationId,
          workspace_id: auth.workspaceId,
          evidence,
          api_failures: apiFailures,
          page_errors: pageErrors,
          falcon_requests: falconRequests,
        },
        null,
        2,
      ),
    );
    if (browser) {
      const pages = await browser.pages();
      await pages
        .at(-1)
        ?.screenshot({ path: FAILURE_SCREENSHOT_PATH, fullPage: true })
        .catch(() => null);
    }
  } finally {
    if (browser) await browser.close();
    await hardDeleteFalconSkillFixtures({
      marker,
      organizationId: auth.organizationId,
    }).catch((error) => {
      caughtError = appendCleanupError(caughtError, error);
    });
  }

  if (caughtError) throw caughtError;
}

async function installRuntimeConfig(page, auth) {
  await page.setRequestInterception(true);
  page.on("request", (request) => {
    const url = new URL(request.url());
    if (url.pathname === "/config.js") {
      request.respond({
        status: 200,
        contentType: "application/javascript",
        body: `window.__FUTURE_AGI_CONFIG__ = ${JSON.stringify({
          VITE_HOST_API: auth.apiBase,
          VITE_ASSETS_API: APP_BASE,
        })};`,
      });
      return;
    }
    request.continue();
  });
}

async function installAuthState(page, auth) {
  await page.evaluateOnNewDocument(
    ({ tokens, organizationId, workspaceId, user }) => {
      localStorage.setItem("accessToken", tokens.access);
      localStorage.setItem("refreshToken", tokens.refresh || "");
      localStorage.setItem("rememberMe", "true");
      localStorage.setItem("initial-render", "done");
      if (organizationId)
        sessionStorage.setItem("organizationId", organizationId);
      if (workspaceId) sessionStorage.setItem("workspaceId", workspaceId);
      if (user?.id)
        sessionStorage.setItem("futureagi-current-user-id", user.id);
    },
    {
      tokens: auth.tokens,
      organizationId: auth.organizationId,
      workspaceId: auth.workspaceId,
      user: auth.user,
    },
  );
}

async function installBrowserHelpers(page) {
  await page.evaluateOnNewDocument(() => {
    window.isVisibleElement = (element) => {
      const style = window.getComputedStyle(element);
      const rect = element.getBoundingClientRect();
      return (
        style.visibility !== "hidden" &&
        style.display !== "none" &&
        rect.width > 0 &&
        rect.height > 0
      );
    };
    window.normalizedText = (value) => String(value || "").trim();
  });
}

async function waitForResponseDuring(page, label, predicate, action) {
  const waiter = page.waitForResponse(predicate, { timeout: 60000 });
  await action();
  try {
    return await waiter;
  } catch (error) {
    throw new Error(
      `${label} did not observe expected response: ${error.message}`,
    );
  }
}

async function waitForVisibleText(
  page,
  text,
  { exact = false, timeout = 30000 } = {},
) {
  await page.waitForFunction(
    ({ text: expectedText, exact: exactMatch }) =>
      Array.from(document.querySelectorAll("body *")).some((element) => {
        if (!window.isVisibleElement(element)) return false;
        const textContent = window.normalizedText(element.textContent);
        if (exactMatch) return textContent === expectedText;
        return textContent.includes(expectedText);
      }),
    { timeout },
    { text, exact },
  );
}

async function waitForNoVisibleText(page, text, { timeout = 5000 } = {}) {
  await page.waitForFunction(
    (expectedText) =>
      !Array.from(document.querySelectorAll("body *")).some(
        (element) =>
          window.isVisibleElement(element) &&
          String(element.textContent || "").includes(expectedText),
      ),
    { timeout },
    text,
  );
}

async function pageHasVisibleText(page, text) {
  return page.evaluate(
    (expectedText) =>
      Array.from(document.querySelectorAll("body *")).some(
        (element) =>
          window.isVisibleElement(element) &&
          String(element.textContent || "").includes(expectedText),
      ),
    text,
  );
}

async function clickDeepestVisibleText(page, text) {
  await waitForVisibleText(page, text);
  await page.evaluate((expectedText) => {
    const containsText = (element) =>
      String(element.textContent || "").includes(expectedText);
    const candidates = Array.from(document.querySelectorAll("body *"))
      .filter(
        (element) => window.isVisibleElement(element) && containsText(element),
      )
      .filter(
        (element) =>
          !Array.from(element.children).some(
            (child) => window.isVisibleElement(child) && containsText(child),
          ),
      )
      .sort(
        (a, b) => a.getBoundingClientRect().top - b.getBoundingClientRect().top,
      );
    const target = candidates[0];
    if (!target)
      throw new Error(`Could not click visible text: ${expectedText}`);
    target.click();
  }, text);
}

async function clickVisibleButtonText(page, text) {
  await page.evaluate((expectedText) => {
    const button = Array.from(document.querySelectorAll("button")).find(
      (element) =>
        window.isVisibleElement(element) &&
        window.normalizedText(element.textContent) === expectedText,
    );
    if (!button) throw new Error(`Could not find button: ${expectedText}`);
    button.click();
  }, text);
}

async function clickCreateSkill(page) {
  await page.evaluate(() => {
    const titled = Array.from(document.querySelectorAll("button")).find(
      (element) =>
        window.isVisibleElement(element) &&
        element.getAttribute("title") === "Create skill",
    );
    if (titled) {
      titled.click();
      return;
    }
    const textButton = Array.from(document.querySelectorAll("body *")).find(
      (element) =>
        window.isVisibleElement(element) &&
        window.normalizedText(element.textContent) === "Create Skill",
    );
    if (!textButton) throw new Error("Could not find Create Skill control");
    textButton.click();
  });
}

async function typeByPlaceholder(page, placeholder, value) {
  const selector = `[placeholder="${cssEscape(placeholder)}"]`;
  await page.waitForSelector(selector, { visible: true, timeout: 30000 });
  await page.click(selector);
  await page.type(selector, value, { delay: 2 });
}

async function replaceByPlaceholder(page, placeholder, value) {
  const selector = `[placeholder="${cssEscape(placeholder)}"]`;
  await page.waitForSelector(selector, { visible: true, timeout: 30000 });
  await page.click(selector, { clickCount: 3 });
  await page.keyboard.press("Backspace");
  await page.type(selector, value, { delay: 2 });
}

function unwrapApiEnvelope(value) {
  return value?.result || value?.data?.result || value?.results || value;
}

function isFalconApiUrl(url) {
  return url.includes("/falcon-ai/");
}

async function loadFalconSkillDbAudit({
  skillId,
  marker,
  organizationId,
  workspaceId,
  userId,
  description,
  instructions,
  triggerPhrase,
}) {
  const sql = `
WITH skill_rows AS (
  SELECT *
  FROM falcon_ai_skill
  WHERE id = ${sqlUuid(skillId)}
    AND slug LIKE ${sqlTextLiteral(`${marker}%`)}
    AND organization_id = ${sqlUuid(organizationId)}
)
SELECT json_build_object(
  'skill_count', (SELECT count(*) FROM skill_rows),
  'active_workspace_skill_count',
    (SELECT count(*) FROM skill_rows WHERE workspace_id = ${sqlUuid(workspaceId)}),
  'user_skill_count',
    (SELECT count(*) FROM skill_rows WHERE created_by_id = ${sqlUuid(userId)}),
  'description_match_count',
    (SELECT count(*) FROM skill_rows WHERE description = ${sqlTextLiteral(description)}),
  'instructions_match_count',
    (SELECT count(*) FROM skill_rows WHERE instructions = ${sqlTextLiteral(instructions)}),
  'trigger_phrase_match_count',
    (SELECT count(*) FROM skill_rows WHERE trigger_phrases::text LIKE ${sqlTextLiteral(`%${triggerPhrase}%`)}),
  'deleted_skill_count',
    (SELECT count(*) FROM skill_rows WHERE deleted = true),
  'deleted_at_count',
    (SELECT count(*) FROM skill_rows WHERE deleted = true AND deleted_at IS NOT NULL)
);
`;
  return runPostgresJson(sql);
}

async function hardDeleteFalconSkillFixtures({ marker, organizationId }) {
  const sql = `
WITH target_skills AS (
  SELECT id
  FROM falcon_ai_skill
  WHERE slug LIKE ${sqlTextLiteral(`${marker}%`)}
    AND organization_id = ${sqlUuid(organizationId)}
),
deleted_skills AS (
  DELETE FROM falcon_ai_skill skill
  USING target_skills target
  WHERE skill.id = target.id
  RETURNING skill.id
)
SELECT json_build_object(
  'deleted_skill_count', (SELECT count(*) FROM deleted_skills),
  'remaining_skill_count',
    (SELECT count(*) FROM target_skills) - (SELECT count(*) FROM deleted_skills)
);
`;
  return runPostgresJson(sql);
}

async function runPostgresJson(sql) {
  const container = process.env.API_JOURNEY_DB_CONTAINER || "ws2-postgres";
  const user = process.env.API_JOURNEY_DB_USER || "user";
  const database = process.env.API_JOURNEY_DB_NAME || "tfc";
  const { stdout } = await execFileAsync(
    "docker",
    ["exec", container, "psql", "-U", user, "-d", database, "-At", "-c", sql],
    { maxBuffer: 10 * 1024 * 1024 },
  );
  const text = stdout.trim();
  assert(text, "Postgres DB command returned no JSON output.");
  return JSON.parse(text);
}

function sqlUuid(value) {
  assert(isUuid(value), "SQL UUID value must be a UUID.");
  return `'${value}'::uuid`;
}

function sqlTextLiteral(value) {
  return `'${String(value ?? "").replaceAll("'", "''")}'`;
}

function cssEscape(value) {
  return String(value).replaceAll("\\", "\\\\").replaceAll('"', '\\"');
}

function browserExecutablePath() {
  return (
    process.env.PUPPETEER_EXECUTABLE_PATH ||
    process.env.CHROME_BIN ||
    "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome"
  );
}

function appendCleanupError(originalError, cleanupError) {
  if (!originalError) return cleanupError;
  originalError.message = `${originalError.message}; cleanup: ${cleanupError.message}`;
  return originalError;
}

main().catch((error) => {
  console.error(error);
  process.exitCode = 1;
});
