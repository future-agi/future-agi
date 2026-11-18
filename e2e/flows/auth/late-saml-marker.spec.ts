import { test, expect } from "../../lib/fixtures";
import { E2E } from "../../lib/env";
import { flowAnnotation } from "../../lib/flow-meta";

// frontend/src/routes/paths.js and sections/project/ObserveListView.jsx.
const OBSERVE = "/dashboard/observe";
// tracer/serializers/project.py and auth/default-workspace-switch.spec.ts.
const PROJECTS = "/tracer/project/list_projects/";
const PROJECT_QUERY = {
  project_type: "observe",
  page_number: "0",
  page_size: "25",
};
const UI_READY = 60_000; // README: browser action/first-paint budget.

interface ProjectPage {
  result: { table: { id: string; name: string }[] };
}

test(
  "AUTH-E2E-017: late SAML markers do not replace an existing session",
  {
    tag: ["@flow"],
    annotation: flowAnnotation({
      id: "AUTH-E2E-017",
      area: "auth",
      userGoal:
        "Keep the same authorized workspace and session after an invalid late SAML code URL, while ordinary navigation remains usable",
      steps: [
        "open Observe with an existing session and a uniquely named project",
        "reject late hash, pushState, replaceState, object URL and history-restoration markers",
        "preserve native ordinary-anchor events and history errors",
        "reload Observe and read the same authorized project without obtaining new credentials",
      ],
      backendChecks: [
        "The exact active token row IDs for the actor are unchanged after rejected markers",
        "The same authenticated project-list API and UI still expose the seeded project with the unchanged credential pair",
      ],
    }),
  },
  async ({ page, actor, probe }, testInfo) => {
    test.setTimeout(300_000); // Provision 60s + 3*UI_READY + checks/teardown 60s.
    page.setDefaultTimeout(UI_READY);
    const name = `e2e-late-saml-${testInfo.workerIndex}-${Date.now().toString(36)}`;
    const created = await actor.api.post<{ result: { project_id: string } }>(
      "/tracer/project/",
      {
        name,
        trace_type: "observe",
        model_type: "GenerativeLLM",
      },
    );
    const project = { id: created.result.project_id, name };
    await testInfo.attach("seeded-ids", {
      contentType: "application/json",
      body: JSON.stringify({
        project,
        email: actor.email,
        organizationId: actor.organizationId,
        workspaceId: actor.workspaceId,
      }),
    });
    // Capture native history only to plant a historical entry; production wrappers remain untouched.
    await page.addInitScript(() => {
      (window as any).__lateTestNativePush = history.pushState.bind(history);
    });
    const tokenRows = () =>
      probe.pg<{ id: string }>(
        `SELECT t.id FROM accounts_auth_token t JOIN accounts_user u ON t.user_id = u.id
     WHERE u.email = $1 AND t.is_active ORDER BY t.id`,
        [actor.email],
      );
    const before = await tokenRows();
    expect(before.length).toBeGreaterThan(0);
    await page.goto(OBSERVE);
    const rows = page.locator(".MuiDataGrid-row[data-id]");
    const namedRows = rows.filter({ hasText: name });
    await expect(namedRows.locator('[data-field="name"]')).toHaveText([name], {
      timeout: UI_READY,
    });
    const credentials = await page.evaluate(() => ({
      access: localStorage.getItem("accessToken"),
      refresh: localStorage.getItem("refreshToken"),
    }));
    expect(credentials).toEqual({
      access: actor.tokens.access,
      refresh: actor.tokens.refresh,
    });
    const exchanges: string[] = [];
    page.on("request", (request) => {
      const path = new URL(request.url()).pathname;
      if (/\/saml2_auth\/|\/accounts\/token\//.test(path)) exchanges.push(path);
    });
    await page.evaluate(() => {
      (window as any).__lateTestEvents = [];
      window.addEventListener("hashchange", (event) =>
        (window as any).__lateTestEvents.push({
          type: event.type,
          url: event.newURL,
        }),
      );
      window.addEventListener("popstate", (event) =>
        (window as any).__lateTestEvents.push({
          type: event.type,
          url: location.href,
        }),
      );
    });
    const marker = "q".repeat(43); // Rejected synthetic input, not a server-issued credential.
    for (const method of ["pushState", "replaceState"] as const) {
      await page.evaluate(
        ({ method, marker }) => {
          history[method](
            { preserved: method },
            "",
            `${location.pathname}?keep=1#saml_code=${marker}`,
          );
        },
        { method, marker },
      );
      expect(new URL(page.url()).hash).toBe("");
      expect(new URL(page.url()).search).toBe("?keep=1");
      expect(await page.evaluate(() => history.state)).toEqual({
        preserved: method,
      });
    }
    const nativeErrors = await page.evaluate((marker) => {
      let conversions = 0;
      history.pushState({}, "", {
        toString() {
          conversions++;
          return `#saml_code=${marker}`;
        },
      } as any);
      const result: Record<string, string | number> = {
        conversions,
        hash: location.hash,
      };
      for (const [kind, state, url] of [
        ["clone", () => {}, `#saml_code=${marker}`],
        ["origin", {}, `https://other-origin.invalid/#saml_code=${marker}`],
      ] as const) {
        try {
          history.pushState(state, "", url);
          result[kind] = "NO_ERROR";
        } catch (error) {
          result[kind] = (error as Error).name;
        }
      }
      return result;
    }, marker);
    expect(nativeErrors).toEqual({
      conversions: 1,
      hash: "",
      clone: "DataCloneError",
      origin: "SecurityError",
    });
    await page.evaluate((marker) => {
      location.hash = `saml_code=${marker}`;
    }, marker);
    await expect
      .poll(() => new URL(page.url()).hash, { timeout: UI_READY })
      .toBe("");
    await page.evaluate((marker) => {
      const nativePush = (window as any).__lateTestNativePush;
      nativePush(
        { historical: true },
        "",
        `${location.pathname}?saml_code=${marker}`,
      );
      nativePush({ current: true }, "", location.pathname);
      history.back();
    }, marker);
    await expect
      .poll(() => page.evaluate(() => history.state), { timeout: UI_READY })
      .toEqual({ historical: true });
    await expect
      .poll(() => new URL(page.url()).search, { timeout: UI_READY })
      .toBe("");
    expect(await page.evaluate(() => (window as any).__lateTestEvents)).toEqual(
      [],
    );
    await page.evaluate(() => {
      location.hash = "ordinary";
    });
    await expect
      .poll(() => page.evaluate(() => (window as any).__lateTestEvents), {
        timeout: UI_READY,
      })
      .toEqual([
        { type: "popstate", url: `${E2E.appUrl}${OBSERVE}#ordinary` },
        { type: "hashchange", url: `${E2E.appUrl}${OBSERVE}#ordinary` },
      ]);
    expect(exchanges).toEqual([]);
    expect(
      await page.evaluate(() => ({
        access: localStorage.getItem("accessToken"),
        refresh: localStorage.getItem("refreshToken"),
      })),
    ).toEqual(credentials);
    await page.goto(OBSERVE);
    await expect(namedRows.locator('[data-field="name"]')).toHaveText([name], {
      timeout: UI_READY,
    });
    const result = await actor.api.get<ProjectPage>(PROJECTS, PROJECT_QUERY);
    expect(
      result.result.table
        .filter((row) => row.name === name)
        .map(({ id, name: rowName }) => ({ id, name: rowName })),
    ).toEqual([project]);
    expect(await tokenRows()).toEqual(before);
    expect(
      await page.evaluate(() => ({
        access: localStorage.getItem("accessToken"),
        refresh: localStorage.getItem("refreshToken"),
      })),
    ).toEqual(credentials);
    await testInfo.attach("authorized-observe", {
      contentType: "image/png",
      body: await page.screenshot(),
    });
  },
);
