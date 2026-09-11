import { createRequire } from "node:module";
import process from "node:process";
import {
  apiPath,
  asArray,
  createAuthenticatedContext,
} from "../lib/api-client.mjs";

const require = createRequire(import.meta.url);
const { chromium } = require("playwright");

const APP_BASE = process.env.APP_BASE || "http://127.0.0.1:3032";

async function main() {
  const auth = await createAuthenticatedContext();
  const sample = await resolveObserveFilterSample(auth.client);
  const apiFailures = [];
  const pageErrors = [];
  const evidence = {
    project_id: sample.project.id,
    project_name: sample.project.name || null,
    custom_attribute: sample.customAttribute.name,
    custom_attribute_value: valueOfOption(sample.customAttributeValues[0]),
    annotation_metric: metricLabel(sample.annotationMetric),
    annotation_value:
      valueOfOption(sample.annotationValues[0]) ||
      valueOfOption(asArray(sample.annotationMetric.choices)[0]),
  };

  const browser = await chromium.launch({
    channel: process.env.PLAYWRIGHT_CHANNEL || "chrome",
    headless: process.env.HEADLESS !== "0",
  });
  const context = await browser.newContext({
    viewport: { width: 1440, height: 950 },
  });
  await context.addInitScript(
    ({ tokens, organizationId, workspaceId, user }) => {
      localStorage.setItem("accessToken", tokens.access);
      localStorage.setItem("refreshToken", tokens.refresh || "");
      localStorage.setItem("rememberMe", "true");
      localStorage.setItem("initial-render", "done");
      if (organizationId) sessionStorage.setItem("organizationId", organizationId);
      if (workspaceId) sessionStorage.setItem("workspaceId", workspaceId);
      if (user?.id) sessionStorage.setItem("futureagi-current-user-id", user.id);
    },
    {
      tokens: auth.tokens,
      organizationId: auth.organizationId,
      workspaceId: auth.workspaceId,
      user: auth.user,
    },
  );

  const page = await context.newPage();
  page.on("response", (response) => {
    const url = response.url();
    if (
      (url.includes("/tracer/dashboard/metrics/") ||
        url.includes("/tracer/dashboard/filter_values/") ||
        url.includes("/tracer/trace/list_traces_of_session/")) &&
      response.status() >= 400
    ) {
      apiFailures.push(`${response.status()} ${url}`);
    }
  });
  page.on("pageerror", (error) => pageErrors.push(error.message));

  try {
    const listResponse = page.waitForResponse(
      (response) =>
        response.url().includes("/tracer/trace/list_traces_of_session/") &&
        response.status() < 400,
      { timeout: 60000 },
    );
    await page.goto(`${APP_BASE}/dashboard/observe/${sample.project.id}/llm-tracing`, {
      waitUntil: "domcontentloaded",
    });
    await listResponse;

    const metricsResponse = page.waitForResponse(
      (response) =>
        response.url().includes("/tracer/dashboard/metrics/") &&
        response.status() < 400,
      { timeout: 60000 },
    );
    await page.getByRole("button", { name: "Filter" }).click();
    await metricsResponse.catch(() => null);
    await page.getByText("Basic").waitFor({ state: "visible", timeout: 30000 });

    await selectProperty(page, sample.customAttribute.name);

    const valueResponse = page.waitForResponse(
      (response) =>
        response.url().includes("/tracer/dashboard/filter_values/") &&
        response.url().includes(encodeURIComponent(sample.customAttribute.name)) &&
        response.status() < 400,
      { timeout: 60000 },
    );
    const customValue = valueOfOption(sample.customAttributeValues[0]);
    await page.getByText("Select values...").last().click();
    await valueResponse.catch(() => null);
    await page.getByText(customValue, { exact: true }).first().click();
    await page.keyboard.press("Escape");
    await page
      .getByRole("button", { name: "Apply" })
      .waitFor({ state: "visible", timeout: 30000 });

    const filteredListResponse = page
      .waitForResponse(
        (response) =>
          response.url().includes("/tracer/trace/list_traces_of_session/") &&
          response.url().includes("filters=") &&
          response.status() < 400,
        { timeout: 60000 },
      )
      .catch((error) => error);
    await page.getByRole("button", { name: "Apply" }).click();

    const filteredResponse = await filteredListResponse;
    if (filteredResponse instanceof Error) {
      throw new Error(
        `Filtered trace list response did not arrive: ${filteredResponse.message}`,
      );
    }

    await page.screenshot({
      path: "/tmp/observe-filters-custom-attribute-smoke.png",
      fullPage: true,
    });
    evidence.custom_attribute_screenshot =
      "/tmp/observe-filters-custom-attribute-smoke.png";

    const resetListResponse = page
      .waitForResponse(
        (response) =>
          response.url().includes("/tracer/trace/list_traces_of_session/") &&
          response.status() < 400,
        { timeout: 60000 },
      )
      .catch(() => null);
    await page.goto(`${APP_BASE}/dashboard/observe/${sample.project.id}/llm-tracing`, {
      waitUntil: "domcontentloaded",
    });
    await resetListResponse;

    await page.getByRole("button", { name: "Filter" }).click();
    await page.getByText("Basic").waitFor({ state: "visible", timeout: 30000 });
    await selectProperty(page, metricLabel(sample.annotationMetric));
    await page.getByText("Select values...").last().click();
    const annotationValue = evidence.annotation_value;
    await page.getByText(annotationValue, { exact: true }).first().waitFor({
      state: "visible",
      timeout: 30000,
    });

    await page.screenshot({
      path: "/tmp/observe-filters-annotation-choice-smoke.png",
      fullPage: true,
    });
    evidence.annotation_screenshot =
      "/tmp/observe-filters-annotation-choice-smoke.png";

    assert(apiFailures.length === 0, `API failures: ${apiFailures.join("; ")}`);
    assert(pageErrors.length === 0, `Page errors: ${pageErrors.join("; ")}`);

    console.log(
      JSON.stringify(
        {
          status: "passed",
          app_base: APP_BASE,
          api_base: auth.apiBase,
          organization_id: auth.organizationId,
          workspace_id: auth.workspaceId,
          evidence,
        },
        null,
        2,
      ),
    );
  } catch (error) {
    await page
      .screenshot({
        path: "/tmp/observe-filters-smoke-failure.png",
        fullPage: true,
      })
      .catch(() => null);
    throw error;
  } finally {
    await context.close();
    await browser.close();
  }
}

async function selectProperty(page, propertyName) {
  await page.getByRole("button", { name: "Property" }).first().click();
  await page
    .getByPlaceholder("Search properties...")
    .waitFor({ state: "visible", timeout: 30000 });
  await page.getByPlaceholder("Search properties...").fill(propertyName);
  await page.getByText(propertyName, { exact: true }).first().click();
}

async function resolveObserveFilterSample(client) {
  const preferredProjectId =
    process.env.OBSERVE_FILTERS_PROJECT_ID || process.env.OBSERVE_PROJECT_ID;
  const projects = preferredProjectId
    ? [{ id: preferredProjectId, name: "env observe filters project" }]
    : asArray(
        await client.get(apiPath("/tracer/project/list_projects/"), {
          query: { page_number: 0, page_size: 100 },
        }),
      );

  for (const project of projects) {
    if (!project?.id) continue;
    const metrics = asArray(
      (
        await client.get(apiPath("/tracer/dashboard/metrics/"), {
          query: { project_ids: project.id },
        })
      ).metrics,
    );
    if (!metrics.length) continue;

    const customAttribute = await firstMetricWithValues(client, project.id, metrics, {
      category: "custom_attribute",
      metricType: "custom_attribute",
      preferName: "fi.trace.source",
    });
    if (!customAttribute) continue;

    const annotationMetric = await firstMetricWithValues(
      client,
      project.id,
      metrics,
      {
        category: "annotation_metric",
        metricType: "annotation_metric",
      },
    );
    if (!annotationMetric) continue;

    return {
      project,
      customAttribute,
      customAttributeValues: customAttribute.values,
      annotationMetric,
      annotationValues: annotationMetric.values,
    };
  }

  throw new Error(
    "No observe project with custom attribute and annotation filter values was found.",
  );
}

async function firstMetricWithValues(
  client,
  projectId,
  metrics,
  { category, metricType, preferName } = {},
) {
  const candidates = metrics.filter((metric) => metric?.category === category);
  const ordered = [
    ...candidates.filter((metric) => metric.name === preferName),
    ...candidates.filter((metric) => metric.name !== preferName),
  ];

  for (const metric of ordered.slice(0, 25)) {
    if (!metric?.name) continue;
    const values = asArray(
      (
        await client.get(apiPath("/tracer/dashboard/filter_values/"), {
          query: {
            metric_name: metric.name,
            metric_type: metricType,
            project_ids: projectId,
            source: "traces",
          },
        })
      ).values,
    );
    if (values.length > 0 || asArray(metric.choices).length > 0) {
      return { ...metric, values };
    }
  }

  return null;
}

function valueOfOption(option) {
  if (option && typeof option === "object") {
    return String(option.value ?? option.label ?? option.name ?? "");
  }
  return String(option ?? "");
}

function metricLabel(metric) {
  return String(metric?.display_name || metric?.displayName || metric?.name || "");
}

function assert(condition, message) {
  if (!condition) throw new Error(message);
}

main().catch((error) => {
  console.error(error);
  process.exitCode = 1;
});
