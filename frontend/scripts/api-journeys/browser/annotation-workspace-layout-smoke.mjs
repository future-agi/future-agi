import { createRequire } from "node:module";
import process from "node:process";
import {
  apiPath,
  asArray,
  assert,
  createAuthenticatedContext,
  currentUserId,
  skip,
} from "../lib/api-client.mjs";

const require = createRequire(import.meta.url);
const puppeteer = require("puppeteer-core");

const APP_BASE = process.env.APP_BASE || "http://127.0.0.1:3032";
const SCREENSHOT_PATH =
  process.env.ANNOTATION_WORKSPACE_LAYOUT_SCREENSHOT ||
  "/tmp/annotation-workspace-layout-smoke.png";
const MAX_FOOTER_BOTTOM_GAP = 4;
const MAX_FOOTER_HEIGHT = 52;

async function main() {
  const auth = await createAuthenticatedContext();
  const sample = await resolveAnnotationSample(auth.client, auth.user);
  const pageErrors = [];
  const apiFailures = [];

  const browser = await puppeteer.launch({
    executablePath: browserExecutablePath(),
    headless: process.env.HEADLESS !== "0",
    defaultViewport: { width: 1440, height: 950 },
    args: ["--no-sandbox"],
  });
  const page = await browser.newPage();
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
  page.on("pageerror", (error) => pageErrors.push(error.message));
  page.on("response", (response) => {
    const url = response.url();
    if (
      url.includes("/model-hub/annotation-queues/") &&
      response.status() >= 400
    ) {
      apiFailures.push(`${response.status()} ${url}`);
    }
  });

  try {
    await page.goto(
      `${APP_BASE}/dashboard/annotations/queues/${sample.queue.id}/annotate?itemId=${sample.item.id}&mode=annotate`,
      { waitUntil: "domcontentloaded" },
    );
    await page.waitForSelector('[data-testid="annotation-footer"]', {
      timeout: 60000,
    });
    await sleep(500);

    const layout = await page.evaluate(() => {
      const boxFor = (selector) => {
        const element = document.querySelector(selector);
        const rect = element?.getBoundingClientRect();
        return rect
          ? {
              top: rect.top,
              right: rect.right,
              bottom: rect.bottom,
              left: rect.left,
              width: rect.width,
              height: rect.height,
            }
          : null;
      };
      const footerBox = boxFor('[data-testid="annotation-footer"]');
      const rootBox = boxFor('[data-testid="annotation-workspace-root"]');
      const viewport = {
        width: window.innerWidth,
        height: window.innerHeight,
      };
      return {
        footerBox,
        rootBox,
        viewport,
        footerBottomGap: footerBox ? viewport.height - footerBox.bottom : null,
        rootBottomGap: rootBox ? viewport.height - rootBox.bottom : null,
      };
    });

    assert(layout.footerBox, "Annotation footer was not rendered.");
    assert(layout.rootBox, "Annotation workspace root was not rendered.");
    assert(
      layout.footerBox.height <= MAX_FOOTER_HEIGHT,
      `Annotation footer is too tall: ${JSON.stringify(layout)}`,
    );
    assert(
      Math.abs(layout.footerBottomGap) <= MAX_FOOTER_BOTTOM_GAP,
      `Annotation footer left a bottom dead band: ${JSON.stringify(layout)}`,
    );
    assert(
      Math.abs(layout.rootBottomGap) <= MAX_FOOTER_BOTTOM_GAP,
      `Annotation workspace root does not fill the viewport: ${JSON.stringify(
        layout,
      )}`,
    );

    await page.screenshot({ path: SCREENSHOT_PATH, fullPage: false });

    assert(apiFailures.length === 0, `API failures: ${apiFailures.join("; ")}`);
    assert(pageErrors.length === 0, `Page errors: ${pageErrors.join("; ")}`);

    console.log(
      JSON.stringify(
        {
          status: "passed",
          app_base: APP_BASE,
          api_base: auth.apiBase,
          screenshot: SCREENSHOT_PATH,
          evidence: {
            queue_id: sample.queue.id,
            item_id: sample.item.id,
            layout,
          },
        },
        null,
        2,
      ),
    );
  } catch (error) {
    await page
      .screenshot({
        path: SCREENSHOT_PATH.replace(/\.png$/, "-failure.png"),
        fullPage: true,
      })
      .catch(() => null);
    throw error;
  } finally {
    await browser.close();
  }
}

async function resolveAnnotationSample(client, user) {
  const explicitQueueId = process.env.ANNOTATION_QUEUE_ID;
  const explicitItemId = process.env.ANNOTATION_ITEM_ID;
  if (explicitQueueId && explicitItemId) {
    const queue = await client.get(
      apiPath("/model-hub/annotation-queues/{id}/", { id: explicitQueueId }),
    );
    const item = await client.get(
      apiPath("/model-hub/annotation-queues/{queue_id}/items/{id}/", {
        queue_id: explicitQueueId,
        id: explicitItemId,
      }),
    );
    return { queue, item };
  }

  const me = String(currentUserId(user) || "");
  const queues = asArray(
    await client.get(apiPath("/model-hub/annotation-queues/"), {
      query: { status: "active" },
    }),
  );

  for (const queue of queues) {
    if (!queue?.id || !asArray(queue.labels).length) continue;
    const items = asArray(
      await client.get(
        apiPath("/model-hub/annotation-queues/{queue_id}/items/", {
          queue_id: queue.id,
        }),
      ),
    );
    const item = items.find((candidate) => {
      if (!["pending", "in_progress"].includes(candidate?.status)) {
        return false;
      }
      const assignedUsers = asArray(candidate.assigned_users);
      if (!assignedUsers.length) return true;
      return assignedUsers.some((assigned) => String(assigned.id) === me);
    });
    if (item?.id) return { queue, item };
  }

  skip(
    "No active annotation queue with an accessible pending item was found. Set ANNOTATION_QUEUE_ID and ANNOTATION_ITEM_ID to run this smoke.",
  );
}

function sleep(ms) {
  return new Promise((resolve) => {
    setTimeout(resolve, ms);
  });
}

function browserExecutablePath() {
  if (process.env.PUPPETEER_EXECUTABLE_PATH) {
    return process.env.PUPPETEER_EXECUTABLE_PATH;
  }
  if (process.env.CHROME_PATH) return process.env.CHROME_PATH;
  if (process.platform === "darwin") {
    return "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome";
  }
  if (process.platform === "linux") {
    return "/usr/bin/google-chrome";
  }
  return undefined;
}

main().catch((error) => {
  if (error?.name === "SkipJourney") {
    console.log(JSON.stringify({ status: "skipped", reason: error.reason }));
    return;
  }
  console.error(error);
  process.exitCode = 1;
});
