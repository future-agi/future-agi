import test from "node:test";
import assert from "node:assert/strict";
import { isAllowedPath, isAllowedRoute, isBundleRoute } from "../src/policy.mjs";

const METHODS = ["GET", "HEAD", "POST", "PUT", "PATCH", "DELETE", "OPTIONS", "TRACE"];

test("admits the customer sandboxes route family the tab uses", () => {
  for (const [method, path] of [
    ["GET", "/workspace/sandboxes"],
    ["POST", "/workspace/sandboxes"],
    ["GET", "/workspace/sandboxes/settings"],
    ["PUT", "/workspace/sandboxes/settings"],
    ["GET", "/workspace/sandboxes/sbx-1"],
    ["PATCH", "/workspace/sandboxes/sbx-1"],
    ["DELETE", "/workspace/sandboxes/sbx-1"],
    ["POST", "/workspace/sandboxes/sbx-1/exec"],
    ["POST", "/workspace/sandboxes/sbx-1/exec/exec-1/cancel"],
    ["GET", "/workspace/sandboxes/sbx-1/files"],
    ["GET", "/workspace/sandboxes/sbx-1/files/src/main.py"],
    ["PUT", "/workspace/sandboxes/sbx-1/files/src/my%20file.txt"],
    ["GET", "/workspace/sandboxes/sbx-1/snapshots"],
    ["POST", "/workspace/sandboxes/sbx-1/snapshots"],
    ["POST", "/workspace/sandboxes/sbx-1/snapshots/snap-1/restore"],
    ["GET", "/workspace/sandboxes/sbx-1/activity"],
    ["POST", "/workspace/sandboxes/sbx-1/hibernate"],
    ["POST", "/workspace/sandboxes/sbx-1/resume"],
  ]) {
    assert.equal(isAllowedRoute(method, path), true, `${method} ${path}`);
  }
  // The old prefix family admitted any shape; the manifest admits only these.
  for (const [method, path] of [["GET", "/workspace/sandboxes/sbx-1/used-by"], ["POST", "/workspace/sandboxes/settings"], ["PUT", "/workspace/sandboxes/sbx-1/files/src%2Fmain.py"]]) {
    assert.equal(isAllowedRoute(method, path), false, `${method} ${path}`);
  }
});

test("admits the customer deployments route family the tab uses", () => {
  const id = "dep-0123456789abcdef";
  for (const [method, path] of [
    ["GET", "/workspace/deployments"],
    ["POST", "/workspace/deployments"],
    ["GET", `/workspace/deployments/${id}`],
    ["DELETE", `/workspace/deployments/${id}`],
    ["GET", `/workspace/deployments/${id}/health`],
    ["GET", `/workspace/deployments/${id}/logs`],
    ["POST", `/workspace/deployments/${id}/access-token`],
    ["POST", `/workspace/deployments/${id}/revisions/3/rollback`],
  ]) {
    assert.equal(isAllowedRoute(method, path), true, `${method} ${path}`);
  }
  for (const [method, path] of [
    ["GET", "/workspace/deployments/dep-1"],
    ["PATCH", `/workspace/deployments/${id}/env`],
    ["POST", `/workspace/deployments/${id}/revisions`],
    ["POST", `/workspace/deployments/${id}/revisions/0/rollback`],
  ]) {
    assert.equal(isAllowedRoute(method, path), false, `${method} ${path}`);
  }
});

test("prefix families never admit HEAD, OPTIONS, TRACE or traversal", () => {
  for (const method of ["HEAD", "OPTIONS", "TRACE", "CONNECT"]) {
    assert.equal(isAllowedRoute(method, "/workspace/sandboxes"), false, method);
    assert.equal(isAllowedRoute(method, "/workspace/deployments/dep-1"), false, method);
  }
  for (const path of [
    "/workspace/sandboxes/",
    "/workspace/sandboxes//exec",
    "/workspace/sandboxes/sbx-1/files/../../../os/agents",
    "/workspace/sandboxes/sbx-1/files/%2e%2e%2f%2e%2e%2fos%2fagents",
    "/workspace/sandboxes/sbx-1/files/%2E%2E/secret",
    "/workspace/sandboxes/./sbx-1",
    "/workspace/deployments/%2e/dep-1",
    "/workspace/deployments/dep-1/%2F/logs",
    "/workspace/sandboxes/sbx-1%5Cother",
    "/workspace/sandboxes/sbx-1%00",
    "/workspace/sandboxesx",
    "/workspace/sandboxes-admin",
    "/workspace/deployments.json",
  ]) {
    for (const method of METHODS) {
      assert.equal(isAllowedRoute(method, path), false, `${method} ${path}`);
    }
  }
});

test("operator /os routes stay closed for every method", () => {
  for (const path of [
    "/os/sandboxes",
    "/os/sandboxes/sbx-1",
    "/os/sandboxes/sbx-1/exec",
    "/os/sandboxes/sbx-1/services/svc-1/logs",
    "/os/agents",
    "/os/agents/researcher",
    "/os/agents/researcher/deployments",
    "/os/agents/researcher/deployments/3/rollback",
    "/os/deployments",
    "/os/deployments/dep-1",
    "/os/workspace/sandboxes",
    "/os/audit",
    "/os/audit/integrity",
    "/os/mcp",
    "/os/memory",
    "/os/memory/ingest",
    "/os/society",
    "/os/fleet",
    "/os/admin",
    "/os/connectors/build",
    "/os/connectors/conn-1/api-key",
    "/os/custom-connectors/conn-1/secrets",
    "/workspace/../os/sandboxes",
    "/workspace/sandboxes/../../os/sandboxes",
    "/%6fs/sandboxes",
  ]) {
    for (const method of METHODS) {
      assert.equal(isAllowedRoute(method, path), false, `operator route leaked: ${method} ${path}`);
    }
  }
  assert.equal(isAllowedPath("/os/sandboxes"), false);
});

test("Squad's /os projections and odd spellings of /os are closed too", () => {
  for (const path of [
    "/os/connectors",
    "/os/connectors/build",
    "/os/connectors/conn-1",
    "/os/connectors/conn-1/oauth/start",
    "/os/custom-connectors",
    "/os/custom-connectors/conn-1/connect",
    "/os/memory/graph",
    "/os/memory/events/event-1/lineage",
    "/os/channels",
    "/os/channels/managed-runs/events",
    "/os",
    "/OS/agents",
    "//os/agents",
    "/%6Fs/memory/graph",
  ]) {
    for (const method of METHODS) {
      assert.equal(isAllowedRoute(method, path), false, `operator namespace leaked: ${method} ${path}`);
    }
  }
});

test("bundle-sized bodies only on deployment uploads, sandbox file writes and agent imports", () => {
  assert.equal(isBundleRoute("POST", "/workspace/deployments"), true);
  assert.equal(isBundleRoute("PUT", "/workspace/sandboxes/sbx-1/files/a/b.txt"), true);
  assert.equal(isBundleRoute("POST", "/workspace/workforce/imports"), true);
  assert.equal(isBundleRoute("GET", "/workspace/deployments"), false);
  assert.equal(isBundleRoute("POST", "/workspace/sandboxes"), false);
  assert.equal(isBundleRoute("POST", "/workspace/sandboxes/sbx-1/exec"), false);
  assert.equal(isBundleRoute("PUT", "/workspace/sandboxes/sbx-1/files"), false);
  assert.equal(isBundleRoute("POST", "/workspace/workforce/agents"), false);
});

test("admits Omega Console Integrations, Usage and agent archives by exact shape and method", () => {
  for (const [method, path] of [
    ["GET", "/workspace/integrations"],
    ["POST", "/workspace/integrations/apps/gmail/connect"],
    ["POST", "/workspace/integrations/oauth/complete"],
    ["POST", "/workspace/integrations/custom-mcp"],
    ["GET", "/workspace/integrations/connections/int_1"],
    ["DELETE", "/workspace/integrations/connections/int_1"],
    ["POST", "/workspace/integrations/connections/int_1/tools"],
    ["PUT", "/workspace/integrations/connections/int_1/agents/workforce_agent-1"],
    ["DELETE", "/workspace/integrations/connections/int_1/agents/workforce_agent-1"],
    ["PUT", "/workspace/integrations/access"],
    ["GET", "/workspace/usage"],
    ["GET", "/workspace/workforce/agents/workforce_agent-1/export"],
    ["POST", "/workspace/workforce/imports/preview"],
    ["POST", "/workspace/workforce/imports"],
  ]) {
    assert.equal(isAllowedRoute(method, path), true, `${method} ${path}`);
  }
  for (const [method, path] of [
    ["POST", "/workspace/integrations"],
    ["DELETE", "/workspace/integrations"],
    ["GET", "/workspace/integrations/apps/gmail/connect"],
    ["GET", "/workspace/integrations/oauth/complete"],
    ["PATCH", "/workspace/integrations/connections/int_1"],
    ["GET", "/workspace/integrations/connections/int_1/agents/a1"],
    ["POST", "/workspace/integrations/connections/int_1/agents/a1"],
    ["GET", "/workspace/integrations/oauth/callback"],
    ["GET", "/workspace/integrations/connections/int_1/credentials"],
    ["GET", "/workspace/integrations/access"],
    ["POST", "/workspace/integrations/access"],
    ["DELETE", "/workspace/integrations/access"],
    ["PUT", "/workspace/integrations/access/tools"],
    ["POST", "/gateway/teams/webhook"],
    ["POST", "/gateway/slack/webhook"],
    ["GET", "/workspace/integrations/connections/../../os/connectors"],
    ["POST", "/workspace/usage"],
    ["DELETE", "/workspace/workforce/imports"],
    ["GET", "/workspace/workforce/imports/preview"],
    ["POST", "/workspace/workforce/agents/a1/export"],
    ["GET", "/os/connectors/github-remote/oauth"],
    ["POST", "/os/custom-connectors"],
  ]) {
    assert.equal(isAllowedRoute(method, path), false, `${method} ${path}`);
  }
});
