import { describe, expect, it } from "vitest";

import { OMEGA_PART } from "src/sections/omega/shell/rollout";
import { CODE_TAB } from "src/sections/omega/shell/switches";

import { pyLiteral, sdkSnippet } from "../sdkSnippets";
import { everyRequest } from "./every-request";
import { uniqueRequests as uniqueRequestsOf } from "./snippet-runs";

const BASE = "https://app.example.test/omega/api";
const SCOPE = { organizationId: "org-a", workspaceId: "ws-a" };
const ON = { [OMEGA_PART.SDK_PYTHON]: true, [OMEGA_PART.SDK_TYPESCRIPT]: true };

// The public paths of sdk-split PLAN section 2.4 these tabs may call, in both
// spellings. Anything else is a method the package does not have.
const PYTHON_CALLS = new Set([
  "client.sandboxes.list",
  "client.sandboxes.create",
  "client.sandboxes.get",
  "client.sandboxes.settings.get",
  "sandbox.commands.run",
  "sandbox.files.list",
  "sandbox.snapshots.list",
  "sandbox.snapshots.create",
  "sandbox.activity",
  "sandbox.hibernate",
  "sandbox.resume",
  "sandbox.kill",
  "client.deployments.list",
  "client.deployments.get",
  "client.deployments.health",
  "client.deployments.delete",
  "client.deployments.rollback",
  "client.deployments.logs",
  "client.deployments.rotate_access_token",
  "client.workspace.secrets.list",
]);
const TS_CALLS = new Set(
  [...PYTHON_CALLS].map((call) =>
    call.replace(/_([a-z])/g, (_, c) => c.toUpperCase()),
  ),
);
const callsIn = (code) =>
  [...code.matchAll(/\b((?:client|sandbox)(?:\.[A-Za-z_]+)+)\(/g)].map(
    (m) => m[1],
  );

describe("the Python and TypeScript tabs", () => {
  const requests = uniqueRequestsOf(everyRequest());

  it("show nothing until the release reports the package published", () => {
    for (const request of requests) {
      for (const tab of [CODE_TAB.PYTHON, CODE_TAB.TYPESCRIPT]) {
        expect(
          sdkSnippet(tab, request, { baseUrl: BASE, scope: SCOPE }),
        ).toBeNull();
        expect(
          sdkSnippet(tab, request, {
            baseUrl: BASE,
            released: {
              [OMEGA_PART.SDK_PYTHON]: false,
              [OMEGA_PART.SDK_TYPESCRIPT]: "yes",
            },
          }),
        ).toBeNull();
      }
      expect(
        sdkSnippet(CODE_TAB.CURL, request, { baseUrl: BASE, released: ON }),
      ).toBeNull();
    }
  });

  it("each part turns on its own tab only", () => {
    const list = requests.find((r) => r.id === "deployments.list");
    const pyOnly = { [OMEGA_PART.SDK_PYTHON]: true };
    expect(
      sdkSnippet(CODE_TAB.PYTHON, list, { baseUrl: BASE, released: pyOnly }),
    ).not.toBeNull();
    expect(
      sdkSnippet(CODE_TAB.TYPESCRIPT, list, {
        baseUrl: BASE,
        released: pyOnly,
      }),
    ).toBeNull();
  });

  it("start with the exact install line, and call only the package's public methods", () => {
    for (const request of requests) {
      const py = sdkSnippet(CODE_TAB.PYTHON, request, {
        baseUrl: BASE,
        scope: SCOPE,
        released: ON,
      });
      const ts = sdkSnippet(CODE_TAB.TYPESCRIPT, request, {
        baseUrl: BASE,
        scope: SCOPE,
        released: ON,
      });
      if (
        request.id.startsWith("sandboxes.") ||
        request.id === "deployments.create"
      ) {
        // Sandboxes and bundle deploys offer curl only in either language.
        expect(py).toBeNull();
        expect(ts).toBeNull();
        continue;
      }
      expect(py.split("\n")[0], request.id).toBe("pip install futureagi");
      expect(ts.split("\n")[0], request.id).toBe("npm i @future-agi/sdk");
      expect(py).toContain("from fi import FutureAGIClient");
      expect(ts).toContain(
        'import { FutureAGIClient } from "@future-agi/sdk";',
      );
      for (const code of [py, ts]) {
        expect(code).toContain(`export FI_PLATFORM_BASE_URL="${BASE}"`);
        expect(code).toContain('export FI_WORKSPACE_ID="ws-a"');
      }
      for (const call of callsIn(py))
        expect(PYTHON_CALLS.has(call), `${request.id}: ${call}`).toBe(true);
      for (const call of callsIn(ts))
        expect(TS_CALLS.has(call), `${request.id}: ${call}`).toBe(true);
    }
  });

  it.each([CODE_TAB.PYTHON, CODE_TAB.TYPESCRIPT])(
    "%s offers curl only for sandboxes while other requests retain SDK snippets",
    (tab) => {
      const sandboxes = requests.filter((r) => r.id.startsWith("sandboxes."));
      expect(sandboxes.length).toBeGreaterThan(0);
      for (const request of sandboxes) {
        expect(
          sdkSnippet(tab, request, { baseUrl: BASE, released: ON }),
          request.id,
        ).toBeNull();
      }
      const list = requests.find((r) => r.id === "deployments.list");
      expect(sdkSnippet(tab, list, { baseUrl: BASE, released: ON })).toContain(
        "client.deployments.list()",
      );
    },
  );

  it("formats Python literals", () => {
    expect(pyLiteral({ a: true, b: null, c: [false, "x"] })).toBe(
      '{"a": True, "b": None, "c": [False, "x"]}',
    );
  });
});
