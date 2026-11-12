// API/storage companion to the browser key-creation flow. Run only on an
// isolated cloud stack with an explicitly supplied interactive-session token.
// This is not a substitute for the managed browser flow.
import { execFile } from "node:child_process";
import { promisify } from "node:util";
import {
  apiPath,
  createAuthenticatedContext,
  isUuid,
  requireMutations,
} from "../lib/api-client.mjs";

const exec = promisify(execFile);
const CREATE = apiPath("/accounts/key/generate_secret_key/");
const LIST = apiPath("/accounts/key/get_secret_keys/");
const REVOKE = apiPath("/accounts/key/delete_secret_key/");
class WorkspaceAssertionError extends Error {}
function assert(condition, message) {
  if (!condition) throw new WorkspaceAssertionError(message);
}
let stage = "isolated-stack prerequisites";

function uuid(value) {
  assert(isUuid(value), "Expected an explicit synthetic UUID.");
  return `'${value}'::uuid`;
}

async function readStoredKey(keyId, auth) {
  // accounts/models/user.py: OrgApiKey; never select either credential column.
  const sql = `SELECT coalesce(json_agg(row_to_json(k)), '[]'::json)
    FROM (SELECT id, name, organization_id, user_id, workspace_id, type, enabled, deleted
      FROM accounts_orgapikey
      WHERE id = ${uuid(keyId)} AND organization_id = ${uuid(auth.organizationId)}) k;`;
  const { stdout } = await exec("docker", [
    "exec",
    process.env.API_JOURNEY_DB_CONTAINER,
    "psql",
    "-X",
    "-v",
    "ON_ERROR_STOP=1",
    "-U",
    process.env.API_JOURNEY_DB_USER,
    "-d",
    process.env.API_JOURNEY_DB_NAME,
    "-At",
    "-c",
    sql,
  ]);
  const rows = JSON.parse(stdout.trim());
  assert(
    rows.length === 1,
    "Expected exactly the newly issued key in storage.",
  );
  return rows[0];
}

async function main() {
  requireMutations();
  // Do not inherit a cached login, a production URL or a default DB container.
  assert(process.env.API_BASE, "Set API_BASE to the isolated cloud stack.");
  const url = new URL(process.env.API_BASE);
  assert(
    url.protocol === "http:" &&
      url.hostname === "localhost" &&
      !url.username &&
      !url.password,
    "The workspace smoke only accepts an HTTP localhost stack.",
  );
  for (const name of [
    "FUTURE_AGI_ACCESS_TOKEN",
    "FUTURE_AGI_ORGANIZATION_ID",
    "FUTURE_AGI_WORKSPACE_ID",
    "API_JOURNEY_DB_CONTAINER",
    "API_JOURNEY_DB_USER",
    "API_JOURNEY_DB_NAME",
  ]) {
    assert(process.env[name], `Set ${name} for the isolated stack.`);
  }
  const auth = await createAuthenticatedContext();
  assert(
    isUuid(auth.user.id),
    "Session user-info must carry the person's UUID.",
  );
  const evidence = [];
  // Pin: L1 accounts/serializers/org_api_key.py and KeysViewSet.generate_secret_key.
  // The request header retains auth.workspaceId in BOTH cases. The body, not
  // ambient request scope, must control the persisted key.workspace_id.
  for (const [mode, workspaceId] of [
    ["selected", auth.workspaceId],
    ["unrestricted", null],
  ]) {
    const keyName = `workspace-${mode}-${auth.runId}`;
    let created;
    try {
      stage = `${mode} session issuance`;
      created = await auth.client.post(CREATE, {
        key_name: keyName,
        workspace_id: workspaceId,
      });
      assert(isUuid(created.key_id), "Issuer must return a key identity.");
      stage = `${mode} stored identity and workspace binding`;
      const stored = await readStoredKey(created.key_id, auth);
      assert(
        stored.name === keyName,
        "Stored key name differs from the submitted name.",
      );
      assert(
        stored.user_id === auth.user.id,
        "Key must belong to the session person.",
      );
      assert(
        stored.organization_id === auth.organizationId,
        "Stored organization differs.",
      );
      assert(
        stored.workspace_id === workspaceId,
        `${mode}: stored workspace binding differs.`,
      );
      assert(stored.type === "user", "Issuer must create a personal key.");
      assert(
        stored.enabled === true && stored.deleted === false,
        "Issued key is not active.",
      );
      stage = `${mode} masked list`;
      const listed = await auth.client.get(LIST, {
        query: { search: keyName, page_size: 10 },
      });
      assert(
        Array.isArray(listed.table),
        "List must return its table envelope.",
      );
      assert(
        listed.table.length === 1,
        "Search must return exactly this minted key.",
      );
      const row = listed.table[0];
      assert(
        row.id === created.key_id && row.key_name === keyName,
        "List identity differs.",
      );
      for (const field of ["api_key", "secret_key"]) {
        assert(
          typeof created[field] === "string" && created[field].length > 0,
          "Missing one-time credential.",
        );
        assert(
          row[field] !== created[field] && row[field].includes("*"),
          "List must mask each credential.",
        );
      }
      evidence.push({
        mode,
        key_id: created.key_id,
        workspace_id: stored.workspace_id,
        type: stored.type,
      });
    } finally {
      // Revoke only the key this invocation created. Retain the row for audit.
      if (created?.key_id) {
        await auth.client.delete(REVOKE, { body: { key_id: created.key_id } });
        const stored = await readStoredKey(created.key_id, auth);
        assert(
          stored.deleted === true && stored.enabled === false,
          "Created key was not revoked.",
        );
      }
    }
  }
  console.log(
    JSON.stringify(
      {
        status: "passed",
        organization_id: auth.organizationId,
        checks: evidence,
      },
      null,
      2,
    ),
  );
}

main().catch((error) => {
  // API errors may include response bodies; print only our value-free assertions.
  console.error(
    JSON.stringify({
      status: "failed",
      stage,
      reason:
        error instanceof WorkspaceAssertionError
          ? error.message
          : "API or database operation failed",
    }),
  );
  process.exitCode = 1;
});
