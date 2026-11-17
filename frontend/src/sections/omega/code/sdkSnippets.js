// The Python and TypeScript tabs of a code panel, shown only once the platform
// package is published: each reads its release part (OMEGA_PART.SDK_PYTHON,
// OMEGA_PART.SDK_TYPESCRIPT, decided by the server from the release's
// OMEGA_RELEASED_PARTS; P22 switches.js codeTabs). Until then this module
// returns null and the panel offers curl only (the
// install lines are exactly `pip install futureagi` and `npm i @future-agi/sdk`,
// never shown before publication).
//
// Method names follow the public paths in sdk-split PLAN section 2.4; the
// client reads FI_API_KEY, FI_SECRET_KEY, FI_PLATFORM_BASE_URL and the
// selection variables from the environment (section 2.6), the same source as
// the keys, so the platform origin passes the host guard. A call with no
// public method (a deploy from a bundle file) has no tab.

import { CODE_TAB } from "src/sections/omega/shell/switches";
import { OMEGA_PART } from "src/sections/omega/shell/rollout";

const INSTALL_LINE = {
  [CODE_TAB.PYTHON]: "pip install futureagi",
  [CODE_TAB.TYPESCRIPT]: "npm i @future-agi/sdk",
};

const PART = {
  [CODE_TAB.PYTHON]: OMEGA_PART.SDK_PYTHON,
  [CODE_TAB.TYPESCRIPT]: OMEGA_PART.SDK_TYPESCRIPT,
};

const snake = (name) => name.replace(/[A-Z]/g, (c) => `_${c.toLowerCase()}`);

/** A JSON value as a Python literal (keys stay strings; true/false/null mapped). */
export function pyLiteral(value) {
  if (value === null || value === undefined) return "None";
  if (value === true) return "True";
  if (value === false) return "False";
  if (Array.isArray(value)) return `[${value.map(pyLiteral).join(", ")}]`;
  if (typeof value === "object") {
    const pairs = Object.entries(value).map(
      ([k, v]) => `${JSON.stringify(k)}: ${pyLiteral(v)}`,
    );
    return `{${pairs.join(", ")}}`;
  }
  return JSON.stringify(value);
}

const pyKwargs = (body = {}) =>
  Object.entries(body)
    .map(([k, v]) => `${snake(k)}=${pyLiteral(v)}`)
    .join(", ");

const tsObject = (body = {}) => {
  const text = JSON.stringify(body);
  return text === "{}" ? "" : text;
};

const idOf = (request) => request.path.split("?")[0].split("/")[3] ?? "";

/** [python call, typescript call, needs a Sandbox handle, yields a stream] for a request id. */
function calls(request) {
  const id = JSON.stringify(decodeURIComponent(idOf(request)));
  const body = request.json || {};
  switch (request.id) {
    case "sandboxes.list":
      return ["client.sandboxes.list()", "await client.sandboxes.list()"];
    case "sandboxes.create":
      return [
        `client.sandboxes.create(${pyKwargs(body)})`,
        `await client.sandboxes.create(${tsObject(body)})`,
      ];
    case "sandboxes.get":
      return [
        `client.sandboxes.get(${id})`,
        `await client.sandboxes.get(${id})`,
      ];
    case "sandboxes.settings":
      return [
        "client.sandboxes.settings.get()",
        "await client.sandboxes.settings.get()",
      ];
    case "sandboxes.exec":
      return [
        `sandbox.commands.run(${JSON.stringify(body.command)})`,
        `await sandbox.commands.run(${JSON.stringify(body.command)})`,
        true,
      ];
    case "sandboxes.files":
      return ["sandbox.files.list()", "await sandbox.files.list()", true];
    case "sandboxes.snapshots":
      return [
        "sandbox.snapshots.list()",
        "await sandbox.snapshots.list()",
        true,
      ];
    case "sandboxes.snapshot":
      return [
        "sandbox.snapshots.create()",
        "await sandbox.snapshots.create()",
        true,
      ];
    case "sandboxes.activity":
      return ["sandbox.activity()", "await sandbox.activity()", true];
    case "sandboxes.hibernate":
      return ["sandbox.hibernate()", "await sandbox.hibernate()", true];
    case "sandboxes.resume":
      return ["sandbox.resume()", "await sandbox.resume()", true];
    case "sandboxes.delete":
      return ["sandbox.kill()", "await sandbox.kill()", true];
    case "deployments.list":
      return ["client.deployments.list()", "await client.deployments.list()"];
    case "deployments.get":
    case "deployments.health":
    case "deployments.delete": {
      const verb = request.id.split(".")[1];
      return [
        `client.deployments.${verb}(${id})`,
        `await client.deployments.${verb}(${id})`,
      ];
    }
    case "deployments.logs": {
      const { source, revision } = request.query || {};
      return [
        `client.deployments.logs(${id}, source=${JSON.stringify(source)}, revision=${revision})`,
        `client.deployments.logs(${id}, { source: ${JSON.stringify(source)}, revision: ${revision} })`,
        false,
        true,
      ];
    }
    case "deployments.rotateToken":
      return [
        `client.deployments.rotate_access_token(${id})`,
        `await client.deployments.rotateAccessToken(${id})`,
      ];
    case "deployments.rollback": {
      const revision = Number(request.path.split("/")[5]);
      return [
        `client.deployments.rollback(${id}, ${revision}, expected_revision=${body.expectedRevision})`,
        `await client.deployments.rollback(${id}, ${revision}, { expectedRevision: ${body.expectedRevision} })`,
      ];
    }
    case "credentials.list":
      return [
        "client.workspace.secrets.list()",
        "await client.workspace.secrets.list()",
      ];
    default:
      return null;
  }
}

function environment(baseUrl, scope) {
  const lines = [
    "# FI_API_KEY and FI_SECRET_KEY: your key pair",
    `export FI_PLATFORM_BASE_URL=${JSON.stringify(baseUrl)}`,
  ];
  if (scope?.organizationId)
    lines.push(
      `export FI_ORGANIZATION_ID=${JSON.stringify(scope.organizationId)}`,
    );
  if (scope?.workspaceId)
    lines.push(`export FI_WORKSPACE_ID=${JSON.stringify(scope.workspaceId)}`);
  return lines.join("\n");
}

/**
 * The code for one tab, or null when that tab is not released or the call has
 * no public method. `released` maps release parts to whether the server
 * reports them ready (useOmegaPart).
 */
export function sdkSnippet(
  tab,
  request,
  { baseUrl, scope, released = {} } = {},
) {
  if (!PART[tab] || released[PART[tab]] !== true) return null;
  if (request.id.startsWith("sandboxes.")) return null;
  const found = calls(request);
  if (!found) return null;
  const [py, ts, needsSandbox, streams] = found;
  const sandboxId = JSON.stringify(decodeURIComponent(idOf(request)));
  const head = `${INSTALL_LINE[tab]}\n${environment(baseUrl, scope)}`;
  if (tab === CODE_TAB.PYTHON) {
    const lines = [
      "from fi import FutureAGIClient",
      "",
      "client = FutureAGIClient()",
    ];
    if (needsSandbox)
      lines.push(`sandbox = client.sandboxes.get(${sandboxId})`);
    if (streams) lines.push(`for event in ${py}:`, "    print(event)");
    else lines.push(`print(${py})`);
    return `${head}\n\n${lines.join("\n")}\n`;
  }
  const lines = [
    'import { FutureAGIClient } from "@future-agi/sdk";',
    "",
    "const client = new FutureAGIClient();",
  ];
  if (needsSandbox)
    lines.push(`const sandbox = await client.sandboxes.get(${sandboxId});`);
  if (streams)
    lines.push(`for await (const event of ${ts}) console.log(event);`);
  else lines.push(`console.log(${ts});`);
  return `${head}\n\n${lines.join("\n")}\n`;
}
