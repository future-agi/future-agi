import { describe, expect, it, vi } from "vitest";

import {
  createObserveClient,
  observeRouteTemplates,
  observeRoutes,
} from "../observeClient";

// The answer FA's ensure view gives (tracer/views/omega_deployments.py).
const ENSURED = {
  data: {
    status: true,
    result: {
      projectId: "7f9c2b4e-1d2a-4c55-9e1f-0a1b2c3d4e5f",
      projectName: "deploy/support-agent",
      alerts: [{ id: "alert-1", kind: "errors_spike" }],
      alertsDeferred: null,
    },
  },
};

describe("observeClient", () => {
  it("ensures a deployment's project through the backend's ensure operation", async () => {
    const post = vi.fn(async () => ENSURED);
    const client = createObserveClient({ request: { post } });

    await expect(
      client.ensure({
        deploymentId: "dep-0123456789abcdef",
        deploymentName: "support-agent",
      }),
    ).resolves.toEqual({
      projectId: "7f9c2b4e-1d2a-4c55-9e1f-0a1b2c3d4e5f",
      projectName: "deploy/support-agent",
      alerts: [{ id: "alert-1", kind: "errors_spike" }],
      alertsDeferred: null,
    });
    expect(post).toHaveBeenCalledWith(
      "/tracer/omega/deployments/dep-0123456789abcdef/observe/",
      { name: "support-agent" },
    );
  });

  it("creates a link from the ensured project, scoped to the deployment and revision", async () => {
    const client = createObserveClient({
      request: { post: vi.fn(async () => ENSURED) },
    });
    const result = await client.linkForDeployment({
      deploymentId: "dep-0123456789abcdef",
      deploymentName: "support-agent",
      revision: 2,
      window: "24h",
      returnTo:
        "https://app.futureagi.test/dashboard/deployments/dep-0123456789abcdef",
      location: { origin: "https://app.futureagi.test" },
    });
    expect(result.target.projectId).toBe(
      "7f9c2b4e-1d2a-4c55-9e1f-0a1b2c3d4e5f",
    );
    expect(result.href).toContain(
      "/dashboard/observe/7f9c2b4e-1d2a-4c55-9e1f-0a1b2c3d4e5f/llm-tracing?",
    );
    expect(result.href).toContain("observeFrom=support-agent");
    expect(result.href).toContain("primaryTraceDateFilter=");
  });

  it("refuses malformed input before calling, and an answer that is not a project", async () => {
    const post = vi.fn(async () => ({ data: { status: false, result: "x" } }));
    const client = createObserveClient({ request: { post } });
    await expect(
      client.ensure({ deploymentId: "", deploymentName: "agent" }),
    ).rejects.toThrow("Invalid deployment id");
    await expect(
      client.ensure({ deploymentId: "dep-1/../x", deploymentName: "agent" }),
    ).rejects.toThrow("Invalid deployment id");
    await expect(
      client.ensure({ deploymentId: "dep-1", deploymentName: "" }),
    ).rejects.toThrow("deployment name");
    await expect(
      client.ensure({ deploymentId: "dep-1", deploymentName: "Not A Name" }),
    ).rejects.toThrow("deployment name");
    expect(post).not.toHaveBeenCalled();
    await expect(
      client.ensure({ deploymentId: "dep-1", deploymentName: "agent" }),
    ).rejects.toThrow("not a project");
  });

  it("names only the operation the backend has, at the path the contract generates", () => {
    expect(observeRouteTemplates).toEqual({
      ensure: "/tracer/omega/deployments/{deployment_id}/observe/",
    });
    expect(observeRoutes.ensure("dep-0123456789abcdef")).toBe(
      "/tracer/omega/deployments/dep-0123456789abcdef/observe/",
    );
  });
});
