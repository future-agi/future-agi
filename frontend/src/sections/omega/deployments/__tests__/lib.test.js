import { describe, expect, it, vi } from "vitest";

import {
  peekOmegaConnect,
  resetOmegaConnectForTests,
  takeOmegaConnectToken,
} from "src/sections/omega/embed/connect-intake";

import {
  createBody,
  createDeploymentsApi,
  deploymentRoutes,
} from "../api/deploymentsApi";
import {
  heldAccessToken,
  keepAccessToken,
  resetAccessTokensForTests,
} from "../lib/accessTokens";
import { liveRevisionOf, relativeTime } from "../lib/format";
import { deploymentPath, newDeploymentPath, openInStudio } from "../lib/links";
import {
  envFromRows,
  nameProblem,
  referenceableSecrets,
  settingsProblems,
} from "../lib/settingsForm";
import { ID, detailView } from "./helpers";

const bundle = {
  archive: new Uint8Array([31, 139, 8, 0, 1, 2, 3]),
  sha256: "a".repeat(64),
  contentSha256: "b".repeat(64),
  size: 7,
};

describe("deployments client", () => {
  it("builds the omega-deploy/1 create body the CLI sends", () => {
    const body = createBody({
      name: "greeter",
      bundle,
      env: { A: "1" },
      secretRefs: ["Z_KEY", "A_KEY"],
      studio: true,
    });
    expect(body).toEqual({
      protocol: "omega-deploy/1",
      kind: "project",
      name: "greeter",
      bundle: {
        format: "tar+gzip",
        sha256: "a".repeat(64),
        contentSha256: "b".repeat(64),
        size: 7,
        data: "H4sIAAECAw==",
      },
      env: { A: "1" },
      secretRefs: ["A_KEY", "Z_KEY"],
      serve: { studio: true },
    });
    expect(createBody({ name: "x", bundle, studio: false }).serve).toEqual({
      studio: false,
    });
  });

  it("only addresses real deployment ids", () => {
    expect(deploymentRoutes.one(ID)).toBe(`/workspace/deployments/${ID}`);
    expect(deploymentRoutes.rollback(ID, 1)).toBe(
      `/workspace/deployments/${ID}/revisions/1/rollback`,
    );
    expect(() => deploymentRoutes.one("../os/agents")).toThrow(TypeError);
    expect(() => deploymentRoutes.rollback(ID, 0)).toThrow(TypeError);
  });

  it("sends Idempotency-Key on every mutation that needs one and refuses without", async () => {
    const omegaJson = vi.fn(async () => ({}));
    const api = createDeploymentsApi({ omegaJson, openOmegaStream: vi.fn() });
    await api.create({ name: "greeter", bundle }, { idempotencyKey: "k1" });
    expect(omegaJson).toHaveBeenLastCalledWith(
      "/workspace/deployments",
      expect.objectContaining({
        method: "POST",
        headers: { "Idempotency-Key": "k1" },
      }),
    );
    await api.rollback(
      ID,
      1,
      { expectedRevision: 2 },
      { idempotencyKey: "k2" },
    );
    expect(omegaJson).toHaveBeenLastCalledWith(
      `/workspace/deployments/${ID}/revisions/1/rollback`,
      {
        method: "POST",
        json: { expectedRevision: 2 },
        headers: { "Idempotency-Key": "k2" },
      },
    );
    expect(() => api.rotateToken(ID, {})).toThrow(/Idempotency-Key/);
  });

  it("opens build logs resumable by seq and runtime logs as a fresh tail", () => {
    const openOmegaStream = vi.fn(() => ({
      close() {},
      done: Promise.resolve(),
    }));
    const api = createDeploymentsApi({ omegaJson: vi.fn(), openOmegaStream });
    api.logs(ID, { source: "build", revision: 2, lastEventId: "7" });
    expect(openOmegaStream).toHaveBeenLastCalledWith(
      `/workspace/deployments/${ID}/logs`,
      expect.objectContaining({
        query: { source: "build", revision: 2 },
        lastEventId: "7",
        resumable: true,
      }),
    );
    api.logs(ID, { source: "runtime", tail: 0 });
    expect(openOmegaStream).toHaveBeenLastCalledWith(
      `/workspace/deployments/${ID}/logs`,
      expect.objectContaining({
        query: { source: "runtime", tail: 0 },
        resumable: false,
      }),
    );
  });
});

describe("access tokens", () => {
  it("are taken out of the detail and held in memory only", () => {
    resetAccessTokensForTests();
    const stripped = keepAccessToken({
      ...detailView(),
      accessToken: "t".repeat(43),
    });
    expect(stripped).not.toHaveProperty("accessToken");
    expect(heldAccessToken(ID)).toBe("t".repeat(43));
    expect(JSON.stringify(stripped)).not.toContain("t".repeat(43));
  });

  it("hand Studio the server and token in memory with a router push, never in a URL (OD-21)", () => {
    resetOmegaConnectForTests();
    const navigate = vi.fn();
    expect(openInStudio("http://127.0.0.1:55408", "a b", navigate)).toBe(true);
    expect(navigate).toHaveBeenCalledTimes(1);
    expect(navigate).toHaveBeenCalledWith("/dashboard/studio");
    expect(JSON.stringify(navigate.mock.calls)).not.toContain("a b");
    expect(peekOmegaConnect()).toEqual({
      url: "http://127.0.0.1:55408",
      origin: "http://127.0.0.1:55408",
    });
    expect(takeOmegaConnectToken()).toBe("a b");
    expect(takeOmegaConnectToken()).toBeNull();
    // Nothing to hand over, or nowhere to go: no offer and no move.
    resetOmegaConnectForTests();
    expect(openInStudio("http://127.0.0.1:55408", null, navigate)).toBe(false);
    expect(openInStudio("http://127.0.0.1:55408", "tok")).toBe(false);
    expect(openInStudio("ftp://127.0.0.1:55408", "tok", navigate)).toBe(false);
    expect(navigate).toHaveBeenCalledTimes(1);
    expect(takeOmegaConnectToken()).toBeNull();
  });
});

describe("links and format", () => {
  it("routes inside /dashboard/deployments", () => {
    expect(deploymentPath(ID)).toBe(`/dashboard/deployments/${ID}`);
    expect(deploymentPath(ID, "logs")).toBe(
      `/dashboard/deployments/${ID}/logs`,
    );
    expect(newDeploymentPath(ID)).toBe(
      `/dashboard/deployments/new?deployment=${ID}`,
    );
  });

  it("finds the serving revision and words times", () => {
    expect(liveRevisionOf(detailView({ liveRevision: 1 })).revision).toBe(1);
    expect(liveRevisionOf(detailView({ liveRevision: null }))).toBeNull();
    expect(relativeTime(new Date(Date.now() - 5 * 60_000).toISOString())).toBe(
      "5 min ago",
    );
  });
});

describe("settings form", () => {
  it("validates names like EE does", () => {
    expect(nameProblem("greeter-2")).toBeNull();
    expect(nameProblem("Greeter")).toMatch(/lowercase/);
    expect(nameProblem("2fast")).toMatch(/starting with a letter/);
  });

  it("refuses reserved, repeated and doubly-declared variables", () => {
    const { env, problems } = envFromRows(
      [
        { key: "GREETING", value: "hi" },
        { key: "OMEGA_SERVE_TOKEN", value: "x" },
        { key: "GREETING", value: "again" },
        { key: "API_KEY", value: "v" },
        { key: "1BAD", value: "v" },
        { key: "", value: "" },
      ],
      ["API_KEY"],
    );
    expect(env).toEqual({ GREETING: "hi" });
    expect(Object.keys(problems).sort()).toEqual(["1", "2", "3", "4"]);
    expect(problems[1]).toMatch(/set by the deployment itself/);
    expect(problems[3]).toMatch(/also picked as a secret/);
  });

  it("offers only secrets EE can inject", () => {
    const secrets = referenceableSecrets([
      { name: "OPENAI_KEY", status: "active" },
      { name: "lower_key", status: "active" },
      { name: "DUP", status: "active" },
      { name: "DUP", status: "active" },
      { name: "OLD", status: "revoked" },
    ]);
    expect(secrets.map((s) => [s.name, s.usable])).toEqual([
      ["DUP", false],
      ["OPENAI_KEY", true],
      ["lower_key", false],
    ]);
    expect(settingsProblems({ name: "ok", rows: [], secretRefs: [] }).ok).toBe(
      true,
    );
  });
});
