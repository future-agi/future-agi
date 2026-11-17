// Contract: every vault call the Deployments tab makes is one the gateway's
// route manifest admits (wall W2, services/omega-gateway/manifest), at the
// role floor EE's vault enforces, with an Idempotency-Key on every write and a
// body that holds nothing but the fields EE reads.
/* global __dirname */
import fs from "node:fs";
import path from "node:path";
import { describe, expect, it, vi } from "vitest";

import { OmegaError } from "src/sections/omega/shared/omegaStates";

import { createDeploymentsApi, credentialRoutes } from "../api/deploymentsApi";

const MANIFEST = JSON.parse(
  fs.readFileSync(
    path.join(
      __dirname,
      "../../../../../../services/omega-gateway/manifest/console-route-manifest.v1.json",
    ),
    "utf8",
  ),
);

function admitted(method, path) {
  return MANIFEST.routes.find((route) => {
    if (route.method !== method) return false;
    const pattern = route.path.replace(
      /\{([a-zA-Z]+)\}/g,
      (_, name) => `(?:${MANIFEST.params[name]})`,
    );
    return new RegExp(`^${pattern}$`).test(path);
  });
}

function recordingApi() {
  const calls = [];
  const omegaJson = vi.fn(async (path, options = {}) => {
    calls.push({ path, method: options.method ?? "GET", options });
    if (path.endsWith("/rotate") || options.method === "POST")
      return {
        credential: { credentialId: "cred_1", name: "A_KEY", version: 2 },
      };
    if (options.method === "DELETE")
      return { deleted: true, credentialId: "cred_1" };
    return { credentials: [] };
  });
  return {
    api: createDeploymentsApi({ omegaJson, openOmegaStream: vi.fn() }),
    calls,
  };
}

describe.each([
  ["list", "deployments"],
  ["credentials", "credentials"],
])("%s response schema", (method, key) => {
  it.each([
    undefined,
    null,
    {},
    [],
    "proxy text",
    { [key]: "x" },
    { [key]: {} },
    { [key]: null },
  ])("rejects an unexpected list body %j with a typed error", async (body) => {
    const api = createDeploymentsApi({ omegaJson: vi.fn(async () => body) });
    const result = api[method]();
    await expect(result).rejects.toBeInstanceOf(OmegaError);
    await expect(result).rejects.toMatchObject({
      state: "error",
      status: null,
      code: "malformed_response",
      reason: null,
      title: null,
      message: "Omega could not complete the request.",
      retryAfter: null,
    });
  });

  it("preserves empty and populated arrays", async () => {
    for (const rows of [[], [{ id: "example" }]]) {
      const api = createDeploymentsApi({
        omegaJson: vi.fn(async () => ({ [key]: rows })),
      });
      await expect(api[method]()).resolves.toBe(rows);
    }
  });
});

describe("vault calls against the route manifest", () => {
  it("each call is a manifest route; writes are admin-floor, write-class and keyed", async () => {
    const { api, calls } = recordingApi();
    await api.credentials();
    const created = await api.createCredential(
      { name: "A_KEY", kind: "api_key", secret: "canary-value" },
      { idempotencyKey: "k-1" },
    );
    const rotated = await api.rotateCredential(
      "cred_1",
      { secret: "canary-2" },
      { idempotencyKey: "k-2" },
    );
    const deleted = await api.revokeCredential("cred_1", {
      idempotencyKey: "k-3",
    });
    expect(created.credentialId).toBe("cred_1");
    expect(rotated.version).toBe(2);
    expect(deleted).toBe(true);

    expect(calls.map((c) => `${c.method} ${c.path}`)).toEqual([
      "GET /workspace/credentials",
      "POST /workspace/credentials",
      "POST /workspace/credentials/cred_1/rotate",
      "DELETE /workspace/credentials/cred_1",
    ]);
    for (const call of calls) {
      const route = admitted(call.method, call.path);
      expect(route, `${call.method} ${call.path}`).toBeTruthy();
      if (call.method === "GET") {
        expect(route.role).toBe("member");
        continue;
      }
      expect(route.role).toBe("admin");
      expect(route.rate).toBe("write");
      expect(call.options.headers["Idempotency-Key"]).toMatch(/^k-\d$/);
    }
    expect(calls[1].options.json).toEqual({
      name: "A_KEY",
      kind: "api_key",
      secret: "canary-value",
    });
    expect(calls[2].options.json).toEqual({ secret: "canary-2" });
    expect(calls[3].options.json).toBeUndefined();
  });

  it("refuses a write without an Idempotency-Key before anything is sent", () => {
    const { api, calls } = recordingApi();
    expect(() =>
      api.createCredential({ name: "A", kind: "api_key", secret: "v" }, {}),
    ).toThrow(/Idempotency-Key/);
    expect(() => api.rotateCredential("cred_1", { secret: "v" }, {})).toThrow(
      /Idempotency-Key/,
    );
    expect(() => api.revokeCredential("cred_1", {})).toThrow(/Idempotency-Key/);
    expect(calls).toHaveLength(0);
  });

  it("never builds a path from an id that could add a segment or a query", () => {
    for (const bad of ["a/b", "../x", "x?y=1", "x#y", "", "a b", 7, null]) {
      expect(() => credentialRoutes.one(bad), String(bad)).toThrow(TypeError);
      expect(() => credentialRoutes.rotate(bad), String(bad)).toThrow(
        TypeError,
      );
    }
    expect(credentialRoutes.rotate("cred_0f2e-9a")).toBe(
      "/workspace/credentials/cred_0f2e-9a/rotate",
    );
  });

  it("the manifest admits no other credential write (no PATCH, PUT, binding writes or value reads)", () => {
    const writes = MANIFEST.routes
      .filter(
        (route) =>
          route.path.startsWith("/workspace/credentials") &&
          route.method !== "GET",
      )
      .map((route) => `${route.method} ${route.path}`)
      .sort();
    expect(writes).toEqual([
      "DELETE /workspace/credentials/{id}",
      "POST /workspace/credentials",
      "POST /workspace/credentials/{id}/rotate",
    ]);
  });
});
