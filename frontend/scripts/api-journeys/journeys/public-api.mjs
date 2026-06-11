import { execFile } from "node:child_process";
import { createHmac, randomUUID } from "node:crypto";
import process from "node:process";
import { fileURLToPath } from "node:url";
import { promisify } from "node:util";
import { apiPath, assert, requireMutations, skip } from "../lib/api-client.mjs";

const execFileAsync = promisify(execFile);
let publicApiRequestCounter = 0;

export const publicApiJourneys = [
  {
    id: "MCP-OAUTH-001",
    title: "MCP public health and OAuth guard endpoints return JSON contracts",
    tags: ["mcp", "oauth", "public", "safe", "guard"],
    public: true,
    async run({ apiBase, evidence }) {
      const health = await request(apiBase, "GET", apiPath("/mcp/health/"));
      assertStatus(health, 200, "MCP health");
      assert(
        health.body?.status === true &&
          health.body?.result?.healthy === true &&
          Number.isFinite(Number(health.body?.result?.tool_count)),
        `MCP health payload mismatch: ${JSON.stringify(health.body)}`,
      );
      assertNoSensitiveTokens(health.body, "MCP health");
      evidence.push({
        checkpoint: "mcp_health",
        status: health.status,
        tool_count: health.body.result.tool_count,
      });

      const approveInfoMissing = await request(
        apiBase,
        "GET",
        apiPath("/mcp/oauth/approve-info/"),
      );
      assertStatus(approveInfoMissing, 400, "approve-info missing request_id");
      assertJsonError(
        approveInfoMissing,
        "Missing request_id",
        "approve-info missing request_id",
      );

      const approveInfoExpired = await request(
        apiBase,
        "GET",
        `${apiPath("/mcp/oauth/approve-info/")}?request_id=api-journey-missing`,
      );
      assertStatus(approveInfoExpired, 404, "approve-info expired request_id");
      assertJsonError(
        approveInfoExpired,
        "Approval request not found or expired",
        "approve-info expired request_id",
      );
      evidence.push({
        checkpoint: "approve_info_guards",
        missing_status: approveInfoMissing.status,
        expired_status: approveInfoExpired.status,
      });

      const authorizeMissing = await request(
        apiBase,
        "GET",
        apiPath("/mcp/oauth/authorize/"),
      );
      assertStatus(authorizeMissing, 400, "authorize missing parameters");
      assertJsonError(
        authorizeMissing,
        "Missing client_id or redirect_uri",
        "authorize missing parameters",
      );

      const authorizeUnsupported = await request(
        apiBase,
        "GET",
        `${apiPath("/mcp/oauth/authorize/")}?client_id=missing-client&redirect_uri=${encodeURIComponent(
          "https://example.com/callback",
        )}&response_type=token`,
      );
      assertStatus(
        authorizeUnsupported,
        400,
        "authorize unsupported response_type",
      );
      assertJsonError(
        authorizeUnsupported,
        "Unsupported response_type",
        "authorize unsupported response_type",
      );
      evidence.push({
        checkpoint: "authorize_parameter_guards",
        missing_status: authorizeMissing.status,
        unsupported_status: authorizeUnsupported.status,
      });

      const authorizeUnknown = await request(
        apiBase,
        "GET",
        `${apiPath("/mcp/oauth/authorize/")}?client_id=missing-client&redirect_uri=${encodeURIComponent(
          "https://example.com/callback",
        )}&response_type=code`,
      );
      evidence.push({
        checkpoint: "authorize_unknown_client",
        status: authorizeUnknown.status,
        content_type: authorizeUnknown.contentType,
        body_kind: typeof authorizeUnknown.body,
      });
      assertNoHtml500(authorizeUnknown, "authorize unknown client");
      assert(
        [400, 503].includes(authorizeUnknown.status),
        `authorize unknown client expected 400 or registry 503, saw ${authorizeUnknown.status}: ${formatBody(
          authorizeUnknown.body,
        )}`,
      );
      assertJsonError(
        authorizeUnknown,
        authorizeUnknown.status === 503
          ? "OAuth client registry unavailable"
          : "Unknown client_id",
        "authorize unknown client",
      );

      const tokenMissing = await request(
        apiBase,
        "POST",
        apiPath("/mcp/oauth/token/"),
        {},
      );
      assertStatus(tokenMissing, 400, "token missing fields");
      assertOAuthError(tokenMissing, "invalid_request", "token missing fields");
      assert(
        String(tokenMissing.body?.error_description || "").includes(
          "grant_type",
        ),
        `token missing fields did not mention grant_type: ${JSON.stringify(
          tokenMissing.body,
        )}`,
      );

      const tokenUnsupported = await request(
        apiBase,
        "POST",
        apiPath("/mcp/oauth/token/"),
        {
          grant_type: "client_credentials",
          client_id: "missing-client",
          client_secret: "secret",
        },
      );
      assertStatus(tokenUnsupported, 400, "token unsupported grant");
      assertOAuthError(
        tokenUnsupported,
        "unsupported_grant_type",
        "token unsupported grant",
      );
      evidence.push({
        checkpoint: "token_parameter_guards",
        missing_status: tokenMissing.status,
        unsupported_status: tokenUnsupported.status,
      });

      const tokenUnknownClient = await request(
        apiBase,
        "POST",
        apiPath("/mcp/oauth/token/"),
        {
          grant_type: "authorization_code",
          code: "api-journey-missing-code",
          client_id: "missing-client",
          client_secret: "secret",
          redirect_uri: "https://example.com/callback",
        },
      );
      evidence.push({
        checkpoint: "token_unknown_client",
        status: tokenUnknownClient.status,
        content_type: tokenUnknownClient.contentType,
        body_kind: typeof tokenUnknownClient.body,
      });
      assertNoHtml500(tokenUnknownClient, "token unknown client");
      assert(
        [401, 503].includes(tokenUnknownClient.status),
        `token unknown client expected 401 or registry 503, saw ${tokenUnknownClient.status}: ${formatBody(
          tokenUnknownClient.body,
        )}`,
      );
      assertOAuthError(
        tokenUnknownClient,
        tokenUnknownClient.status === 503 ? "server_error" : "invalid_client",
        "token unknown client",
      );

      const refreshMissingToken = await request(
        apiBase,
        "POST",
        apiPath("/mcp/oauth/token/"),
        {
          grant_type: "refresh_token",
          client_id: "missing-client",
          client_secret: "secret",
        },
      );
      assertStatus(refreshMissingToken, 400, "refresh token missing token");
      assertOAuthError(
        refreshMissingToken,
        "invalid_request",
        "refresh token missing token",
      );

      const refreshUnknownClient = await request(
        apiBase,
        "POST",
        apiPath("/mcp/oauth/token/"),
        {
          grant_type: "refresh_token",
          refresh_token: "api-journey-missing-refresh-token",
          client_id: "missing-client",
          client_secret: "secret",
        },
      );
      evidence.push({
        checkpoint: "refresh_token_unknown_client",
        status: refreshUnknownClient.status,
        content_type: refreshUnknownClient.contentType,
        body_kind: typeof refreshUnknownClient.body,
      });
      assertNoHtml500(refreshUnknownClient, "refresh token unknown client");
      assert(
        [401, 503].includes(refreshUnknownClient.status),
        `refresh token unknown client expected 401 or registry 503, saw ${refreshUnknownClient.status}: ${formatBody(
          refreshUnknownClient.body,
        )}`,
      );
      assertOAuthError(
        refreshUnknownClient,
        refreshUnknownClient.status === 503 ? "server_error" : "invalid_client",
        "refresh token unknown client",
      );

      const approveUnauthenticated = await request(
        apiBase,
        "POST",
        apiPath("/mcp/oauth/approve/"),
        { request_id: "api-journey-missing", approved: false },
      );
      assertJsonAuthGuard(approveUnauthenticated, "approve unauthenticated");

      const consentUnauthenticated = await request(
        apiBase,
        "POST",
        apiPath("/mcp/oauth/consent/"),
        {
          client_id: "missing-client",
          redirect_uri: "https://example.com/callback",
          approved: false,
        },
      );
      assertJsonAuthGuard(consentUnauthenticated, "consent unauthenticated");

      evidence.push({
        health_tool_count: health.body.result.tool_count,
        approve_info_missing_status: approveInfoMissing.status,
        approve_info_expired_status: approveInfoExpired.status,
        authorize_unknown_status: authorizeUnknown.status,
        token_unknown_client_status: tokenUnknownClient.status,
        refresh_missing_token_status: refreshMissingToken.status,
        refresh_unknown_client_status: refreshUnknownClient.status,
        approve_unauthenticated_status: approveUnauthenticated.status,
        approve_unauthenticated_content_type:
          approveUnauthenticated.contentType,
        consent_unauthenticated_status: consentUnauthenticated.status,
        consent_unauthenticated_content_type:
          consentUnauthenticated.contentType,
      });
    },
  },
  {
    id: "PUBLIC-AUTH-001",
    title:
      "Authenticated API surfaces reject anonymous requests with JSON auth errors",
    tags: ["public", "auth", "guard", "accounts", "usage", "integrations"],
    public: true,
    async run({ apiBase, evidence }) {
      const endpoints = [
        {
          label: "2FA status",
          method: "GET",
          path: apiPath("/accounts/2fa/status/"),
        },
        {
          label: "passkeys list",
          method: "GET",
          path: apiPath("/accounts/passkeys/"),
        },
        {
          label: "workspace usage summary",
          method: "GET",
          path: apiPath("/usage/workspace-usage-summary/"),
        },
        {
          label: "workspace eval summary",
          method: "GET",
          path: apiPath("/usage/workspace-eval-summary/"),
        },
        {
          label: "integration connection create",
          method: "POST",
          path: apiPath("/integrations/connections/"),
          body: {},
        },
        {
          label: "integration credential validate",
          method: "POST",
          path: apiPath("/integrations/connections/validate/"),
          body: {},
        },
        {
          label: "agent graph dataset execute",
          method: "POST",
          path: apiPath(
            "/agent-playground/graphs/{graph_id}/dataset/execute/",
            {
              graph_id: "00000000-0000-0000-0000-000000000000",
            },
          ),
          body: {},
        },
        {
          label: "user info",
          method: "GET",
          path: apiPath("/accounts/user-info/"),
        },
        {
          label: "legacy team users",
          method: "GET",
          path: apiPath("/accounts/team/users/"),
        },
      ];

      for (const endpoint of endpoints) {
        const result = await request(
          apiBase,
          endpoint.method,
          endpoint.path,
          endpoint.body,
        );
        assertJsonAuthGuard(result, endpoint.label);
        evidence.push({
          label: endpoint.label,
          method: endpoint.method,
          path: endpoint.path,
          status: result.status,
          code: result.body?.code || null,
        });
      }
    },
  },
  {
    id: "PUBLIC-AUTH-002",
    title:
      "Representative mutating APIs reject anonymous requests before work starts",
    tags: [
      "public",
      "auth",
      "guard",
      "mutations",
      "datasets",
      "evals",
      "observe",
      "simulation",
      "gateway",
    ],
    public: true,
    async run({ apiBase, evidence }) {
      const zeroUuid = "00000000-0000-0000-0000-000000000000";
      const endpoints = [
        {
          label: "legacy team users delete alias",
          method: "DELETE",
          path: apiPath("/accounts/team/users/"),
          body: {},
        },
        {
          label: "dataset create empty",
          method: "POST",
          path: apiPath("/model-hub/develops/create-empty-dataset/"),
          body: {},
        },
        {
          label: "dataset add empty rows",
          method: "POST",
          path: apiPath("/model-hub/develops/{dataset_id}/add_empty_rows/", {
            dataset_id: zeroUuid,
          }),
          body: {},
        },
        {
          label: "dataset optimization create",
          method: "POST",
          path: apiPath("/model-hub/dataset-optimization/"),
          body: {},
        },
        {
          label: "dataset optimization stop",
          method: "POST",
          path: apiPath("/model-hub/dataset-optimization/{id}/stop/", {
            id: zeroUuid,
          }),
          body: {},
        },
        {
          label: "dataset compare preview run eval",
          method: "POST",
          path: apiPath("/model-hub/datasets/compare/preview-run-eval/"),
          body: {},
        },
        {
          label: "dataset compare row delete",
          method: "DELETE",
          path: apiPath(
            "/model-hub/datasets/get-compare-row/{compare_id}/{row_id}/",
            { compare_id: zeroUuid, row_id: zeroUuid },
          ),
          body: {},
        },
        {
          label: "dataset compare add eval",
          method: "POST",
          path: apiPath(
            "/model-hub/datasets/{dataset_id}/compare-datasets/add-eval/",
            { dataset_id: zeroUuid },
          ),
          body: {},
        },
        {
          label: "dataset compare start eval",
          method: "POST",
          path: apiPath(
            "/model-hub/datasets/{dataset_id}/compare-datasets/start-eval/",
            { dataset_id: zeroUuid },
          ),
          body: {},
        },
        {
          label: "eval template create v2",
          method: "POST",
          path: apiPath("/model-hub/eval-templates/create-v2/"),
          body: {},
        },
        {
          label: "eval template update",
          method: "PUT",
          path: apiPath("/model-hub/eval-templates/{template_id}/update/", {
            template_id: zeroUuid,
          }),
          body: {},
        },
        {
          label: "annotation submit",
          method: "POST",
          path: apiPath(
            "/model-hub/annotation-queues/{queue_id}/items/{id}/annotations/submit/",
            { queue_id: zeroUuid, id: zeroUuid },
          ),
          body: {},
        },
        {
          label: "observe project create",
          method: "POST",
          path: apiPath("/tracer/project/"),
          body: {},
        },
        {
          label: "observe trace patch",
          method: "PATCH",
          path: apiPath("/tracer/trace/{id}/", { id: zeroUuid }),
          body: {},
        },
        {
          label: "dashboard query",
          method: "POST",
          path: apiPath("/tracer/dashboard/query/"),
          body: {},
        },
        {
          label: "dashboard widget preview query",
          method: "POST",
          path: apiPath("/tracer/dashboard/{dashboard_pk}/widgets/preview/", {
            dashboard_pk: zeroUuid,
          }),
          body: {},
        },
        {
          label: "dashboard widget execute query",
          method: "POST",
          path: apiPath(
            "/tracer/dashboard/{dashboard_pk}/widgets/{id}/query/",
            { dashboard_pk: zeroUuid, id: zeroUuid },
          ),
          body: {},
        },
        {
          label: "simulation run-test create",
          method: "POST",
          path: apiPath("/simulate/run-tests/create/"),
          body: {},
        },
        {
          label: "simulation run-test execute",
          method: "POST",
          path: apiPath("/simulate/run-tests/{run_test_id}/execute/", {
            run_test_id: zeroUuid,
          }),
          body: {},
        },
        {
          label: "simulation rerun test executions",
          method: "POST",
          path: apiPath(
            "/simulate/run-tests/{run_test_id}/rerun-test-executions/",
            { run_test_id: zeroUuid },
          ),
          body: {},
        },
        {
          label: "simulation run new evals",
          method: "POST",
          path: apiPath("/simulate/run-tests/{run_test_id}/run-new-evals/", {
            run_test_id: zeroUuid,
          }),
          body: {},
        },
        {
          label: "simulation scenario create",
          method: "POST",
          path: apiPath("/simulate/scenarios/create/"),
          body: {},
        },
        {
          label: "billing budget create",
          method: "POST",
          path: apiPath("/usage/v2/budgets/"),
          body: {},
        },
        {
          label: "gateway playground test",
          method: "POST",
          path: apiPath("/agentcc/gateways/{id}/test-playground/", {
            id: zeroUuid,
          }),
          body: {},
        },
        {
          label: "eval playground execute",
          method: "POST",
          path: apiPath("/model-hub/eval-playground/"),
          body: {},
        },
        {
          label: "evaluate rows",
          method: "POST",
          path: apiPath("/model-hub/evaluate-rows/"),
          body: {},
        },
        {
          label: "simulation rerun calls",
          method: "POST",
          path: apiPath(
            "/simulate/test-executions/{test_execution_id}/rerun-calls/",
            { test_execution_id: zeroUuid },
          ),
          body: {},
        },
        {
          label: "simulation eval explanation refresh",
          method: "POST",
          path: apiPath(
            "/simulate/test-executions/{test_execution_id}/eval-explanation-summary/refresh/",
            { test_execution_id: zeroUuid },
          ),
          body: {},
        },
        {
          label: "simulation optimiser analysis refresh",
          method: "POST",
          path: apiPath(
            "/simulate/test-executions/{test_execution_id}/optimiser-analysis/refresh/",
            { test_execution_id: zeroUuid },
          ),
          body: {},
        },
        {
          label: "simulation chat send message",
          method: "POST",
          path: apiPath(
            "/simulate/call-executions/{call_execution_id}/chat/send-message/",
            { call_execution_id: zeroUuid },
          ),
          body: {},
        },
      ];

      for (const endpoint of endpoints) {
        const result = await request(
          apiBase,
          endpoint.method,
          endpoint.path,
          endpoint.body,
        );
        assertJsonAuthGuard(result, endpoint.label);
        evidence.push({
          label: endpoint.label,
          method: endpoint.method,
          path: endpoint.path,
          status: result.status,
          code: result.body?.code || null,
        });
      }
    },
  },
  {
    id: "PUBLIC-AUTH-003",
    title:
      "Representative data-read APIs reject anonymous requests before workspace data access",
    tags: [
      "public",
      "auth",
      "guard",
      "reads",
      "datasets",
      "evals",
      "annotations",
      "observe",
      "simulation",
      "agents",
      "gateway",
    ],
    public: true,
    async run({ apiBase, evidence }) {
      const zeroUuid = "00000000-0000-0000-0000-000000000000";
      const endpoints = [
        {
          label: "dataset list",
          method: "GET",
          path: apiPath("/model-hub/develops/get-datasets/"),
        },
        {
          label: "dataset names",
          method: "GET",
          path: apiPath("/model-hub/develops/get-datasets-names/"),
        },
        {
          label: "dataset table",
          method: "GET",
          path: apiPath("/model-hub/develops/{dataset_id}/get-dataset-table/", {
            dataset_id: zeroUuid,
          }),
        },
        {
          label: "dataset compare delete preview",
          method: "GET",
          path: apiPath("/model-hub/datasets/delete-compare/{compare_id}/", {
            compare_id: zeroUuid,
          }),
        },
        {
          label: "eval template list",
          method: "POST",
          path: apiPath("/model-hub/eval-templates/list/"),
          body: {},
        },
        {
          label: "eval template detail",
          method: "GET",
          path: apiPath("/model-hub/eval-templates/{template_id}/detail/", {
            template_id: zeroUuid,
          }),
        },
        {
          label: "annotation queue list",
          method: "GET",
          path: apiPath("/model-hub/annotation-queues/"),
        },
        {
          label: "annotation queue detail",
          method: "GET",
          path: apiPath("/model-hub/annotation-queues/{id}/", {
            id: zeroUuid,
          }),
        },
        {
          label: "prototype project list",
          method: "GET",
          path: apiPath("/tracer/project/"),
        },
        {
          label: "observe project list",
          method: "GET",
          path: apiPath("/tracer/project/list_projects/"),
        },
        {
          label: "project detail",
          method: "GET",
          path: apiPath("/tracer/project/{id}/", { id: zeroUuid }),
        },
        {
          label: "raw trace list",
          method: "GET",
          path: apiPath("/tracer/trace/"),
        },
        {
          label: "raw trace detail",
          method: "GET",
          path: apiPath("/tracer/trace/{id}/", { id: zeroUuid }),
        },
        {
          label: "simulation run-test list",
          method: "GET",
          path: apiPath("/simulate/run-tests/"),
        },
        {
          label: "simulation run-test detail",
          method: "GET",
          path: apiPath("/simulate/run-tests/{run_test_id}/", {
            run_test_id: zeroUuid,
          }),
        },
        {
          label: "simulation session comparison",
          method: "GET",
          path: apiPath(
            "/simulate/call-executions/{call_execution_id}/session-comparison/",
            { call_execution_id: zeroUuid },
          ),
        },
        {
          label: "simulation scenario list",
          method: "GET",
          path: apiPath("/simulate/scenarios/"),
        },
        {
          label: "simulation scenario detail",
          method: "GET",
          path: apiPath("/simulate/scenarios/{scenario_id}/", {
            scenario_id: zeroUuid,
          }),
        },
        {
          label: "agent graph list",
          method: "GET",
          path: apiPath("/agent-playground/graphs/"),
        },
        {
          label: "agent graph detail",
          method: "GET",
          path: apiPath("/agent-playground/graphs/{id}/", { id: zeroUuid }),
        },
        {
          label: "gateway list",
          method: "GET",
          path: apiPath("/agentcc/gateways/"),
        },
        {
          label: "gateway detail",
          method: "GET",
          path: apiPath("/agentcc/gateways/{id}/", { id: zeroUuid }),
        },
      ];

      for (const endpoint of endpoints) {
        const result = await request(
          apiBase,
          endpoint.method,
          endpoint.path,
          endpoint.body,
        );
        assertJsonAuthGuard(result, endpoint.label);
        evidence.push({
          label: endpoint.label,
          method: endpoint.method,
          path: endpoint.path,
          status: result.status,
          code: result.body?.code || null,
        });
      }
    },
  },
  {
    id: "PUBLIC-AUTH-004",
    title:
      "Sensitive key, secret, billing, and provider APIs reject anonymous requests",
    tags: [
      "public",
      "auth",
      "guard",
      "security",
      "keys",
      "secrets",
      "billing",
      "provider-config",
      "gateway",
    ],
    public: true,
    async run({ apiBase, evidence }) {
      const zeroUuid = "00000000-0000-0000-0000-000000000000";
      const endpoints = [
        {
          label: "developer secret key list",
          method: "GET",
          path: apiPath("/accounts/key/get_secret_keys/"),
        },
        {
          label: "developer secret key generate",
          method: "POST",
          path: apiPath("/accounts/key/generate_secret_key/"),
          body: {},
        },
        {
          label: "developer secret key disable",
          method: "POST",
          path: apiPath("/accounts/key/disable_key/"),
          body: { key_id: zeroUuid },
        },
        {
          label: "developer secret key enable",
          method: "POST",
          path: apiPath("/accounts/key/enable_key/"),
          body: { key_id: zeroUuid },
        },
        {
          label: "developer secret key delete",
          method: "DELETE",
          path: apiPath("/accounts/key/delete_secret_key/"),
          body: { key_id: zeroUuid },
        },
        {
          label: "system org keys",
          method: "GET",
          path: apiPath("/accounts/keys/"),
        },
        {
          label: "provider API key list",
          method: "GET",
          path: apiPath("/model-hub/api-keys/"),
        },
        {
          label: "provider API key create",
          method: "POST",
          path: apiPath("/model-hub/api-keys/"),
          body: {},
        },
        {
          label: "provider API key detail",
          method: "GET",
          path: apiPath("/model-hub/api-keys/{id}/", { id: zeroUuid }),
        },
        {
          label: "provider API key update",
          method: "PATCH",
          path: apiPath("/model-hub/api-keys/{id}/", { id: zeroUuid }),
          body: {},
        },
        {
          label: "provider API key delete",
          method: "DELETE",
          path: apiPath("/model-hub/api-keys/{id}/", { id: zeroUuid }),
          body: {},
        },
        {
          label: "provider status",
          method: "GET",
          path: apiPath("/model-hub/develops/provider-status/"),
        },
        {
          label: "custom model list",
          method: "GET",
          path: apiPath("/model-hub/custom-models/"),
        },
        {
          label: "custom model create",
          method: "POST",
          path: apiPath("/model-hub/custom_models/create/"),
          body: {},
        },
        {
          label: "custom model edit",
          method: "PATCH",
          path: apiPath("/model-hub/custom_models/edit/"),
          body: {},
        },
        {
          label: "model secret list",
          method: "GET",
          path: apiPath("/model-hub/secrets/"),
        },
        {
          label: "model secret create",
          method: "POST",
          path: apiPath("/model-hub/secrets/"),
          body: {},
        },
        {
          label: "model secret detail",
          method: "GET",
          path: apiPath("/model-hub/secrets/{id}/", { id: zeroUuid }),
        },
        {
          label: "model secret update",
          method: "PATCH",
          path: apiPath("/model-hub/secrets/{id}/", { id: zeroUuid }),
          body: {},
        },
        {
          label: "model secret delete",
          method: "DELETE",
          path: apiPath("/model-hub/secrets/{id}/", { id: zeroUuid }),
          body: {},
        },
        {
          label: "model tool list",
          method: "GET",
          path: apiPath("/model-hub/tools/"),
        },
        {
          label: "model tool create",
          method: "POST",
          path: apiPath("/model-hub/tools/"),
          body: {},
        },
        {
          label: "TTS voice list",
          method: "GET",
          path: apiPath("/model-hub/tts-voices/"),
        },
        {
          label: "TTS voice create",
          method: "POST",
          path: apiPath("/model-hub/tts-voices/"),
          body: {},
        },
        {
          label: "EE license list",
          method: "GET",
          path: apiPath("/usage/ee/licenses/"),
        },
        {
          label: "EE license create",
          method: "POST",
          path: apiPath("/usage/ee/licenses/"),
          body: {},
        },
        {
          label: "EE license revoke",
          method: "POST",
          path: apiPath("/usage/ee/licenses/{grant_id}/revoke/", {
            grant_id: zeroUuid,
          }),
          body: {},
        },
        {
          label: "payment methods",
          method: "GET",
          path: apiPath("/usage/v2/payment-methods/"),
        },
        {
          label: "payment setup intent",
          method: "POST",
          path: apiPath("/usage/v2/payment-methods/setup-intent/"),
          body: {},
        },
        {
          label: "payment method delete",
          method: "DELETE",
          path: apiPath("/usage/v2/payment-methods/{pm_id}/", {
            pm_id: zeroUuid,
          }),
          body: {},
        },
        {
          label: "payment method default",
          method: "POST",
          path: apiPath("/usage/v2/payment-methods/{pm_id}/default/", {
            pm_id: zeroUuid,
          }),
          body: {},
        },
        {
          label: "billing portal session",
          method: "POST",
          path: apiPath("/usage/create-billing-portal-session/"),
          body: {},
        },
        {
          label: "checkout session",
          method: "POST",
          path: apiPath("/usage/create-checkout-session/"),
          body: {},
        },
        {
          label: "legacy billing details",
          method: "GET",
          path: apiPath("/usage/get-billing-details/"),
        },
        {
          label: "plans and add-ons",
          method: "GET",
          path: apiPath("/usage/v2/plans-and-addons/"),
        },
        {
          label: "gateway API key list",
          method: "GET",
          path: apiPath("/agentcc/api-keys/"),
        },
        {
          label: "gateway API key create",
          method: "POST",
          path: apiPath("/agentcc/api-keys/"),
          body: {},
        },
        {
          label: "gateway API key detail",
          method: "GET",
          path: apiPath("/agentcc/api-keys/{id}/", { id: zeroUuid }),
        },
        {
          label: "gateway API key update",
          method: "PATCH",
          path: apiPath("/agentcc/api-keys/{id}/", { id: zeroUuid }),
          body: {},
        },
        {
          label: "gateway API key delete",
          method: "DELETE",
          path: apiPath("/agentcc/api-keys/{id}/", { id: zeroUuid }),
          body: {},
        },
        {
          label: "gateway API key revoke",
          method: "POST",
          path: apiPath("/agentcc/api-keys/{id}/revoke/", { id: zeroUuid }),
          body: {},
        },
        {
          label: "gateway API key sync",
          method: "POST",
          path: apiPath("/agentcc/api-keys/sync/"),
          body: {},
        },
      ];

      for (const endpoint of endpoints) {
        const result = await request(
          apiBase,
          endpoint.method,
          endpoint.path,
          endpoint.body,
        );
        assertJsonAuthGuard(result, endpoint.label);
        evidence.push({
          label: endpoint.label,
          method: endpoint.method,
          path: endpoint.path,
          status: result.status,
          code: result.body?.code || null,
        });
      }
    },
  },
  {
    id: "PUBLIC-AUTH-005",
    title: "SDK and ingest API-key routes reject missing credentials",
    tags: [
      "public",
      "auth",
      "guard",
      "sdk",
      "api-key",
      "evals",
      "simulation",
      "traces",
    ],
    public: true,
    async run({ apiBase, evidence }) {
      const endpoints = [
        {
          label: "SDK configure evaluations",
          method: "POST",
          path: apiPath("/sdk/api/v1/configure-evaluations/"),
          body: {},
        },
        {
          label: "SDK standalone eval v1",
          method: "POST",
          path: apiPath("/sdk/api/v1/eval/"),
          body: {},
        },
        {
          label: "SDK eval detail",
          method: "GET",
          path: apiPath("/sdk/api/v1/eval/{eval_id}/", { eval_id: "123" }),
        },
        {
          label: "SDK CI/CD runs list",
          method: "GET",
          path: apiPath("/sdk/api/v1/evaluate-pipeline/"),
        },
        {
          label: "SDK CI/CD run create",
          method: "POST",
          path: apiPath("/sdk/api/v1/evaluate-pipeline/"),
          body: {},
        },
        {
          label: "SDK get evals",
          method: "GET",
          path: apiPath("/sdk/api/v1/get-evals/"),
        },
        {
          label: "SDK standalone eval v2 detail",
          method: "GET",
          path: apiPath("/sdk/api/v1/new-eval/"),
        },
        {
          label: "SDK standalone eval v2 create",
          method: "POST",
          path: apiPath("/sdk/api/v1/new-eval/"),
          body: {},
        },
        {
          label: "SDK simulation analytics",
          method: "GET",
          path: apiPath("/sdk/api/v1/simulation/analytics/"),
        },
        {
          label: "SDK simulation metrics",
          method: "GET",
          path: apiPath("/sdk/api/v1/simulation/metrics/"),
        },
        {
          label: "SDK simulation runs",
          method: "GET",
          path: apiPath("/sdk/api/v1/simulation/runs/"),
        },
        {
          label: "trace span attribute keys",
          method: "GET",
          path: apiPath("/api/traces/span-attribute-keys/"),
        },
      ];

      for (const endpoint of endpoints) {
        const result = await request(
          apiBase,
          endpoint.method,
          endpoint.path,
          endpoint.body,
        );
        assertJsonAuthGuard(result, endpoint.label);
        evidence.push({
          label: endpoint.label,
          method: endpoint.method,
          path: endpoint.path,
          status: result.status,
          code: result.body?.code || null,
        });
      }
    },
  },
  {
    id: "PUBLIC-AUTH-006",
    title: "Account security management routes reject anonymous requests",
    tags: [
      "public",
      "auth",
      "guard",
      "accounts",
      "security",
      "2fa",
      "passkeys",
    ],
    public: true,
    async run({ apiBase, evidence }) {
      const zeroUuid = "00000000-0000-4000-8000-000000000123";
      const endpoints = [
        {
          label: "TOTP setup",
          method: "POST",
          path: apiPath("/accounts/2fa/totp/setup/"),
          body: {},
        },
        {
          label: "TOTP confirm",
          method: "POST",
          path: apiPath("/accounts/2fa/totp/confirm/"),
          body: { token: "000000" },
        },
        {
          label: "TOTP disable",
          method: "DELETE",
          path: apiPath("/accounts/2fa/totp/"),
        },
        {
          label: "recovery code list",
          method: "GET",
          path: apiPath("/accounts/2fa/recovery-codes/"),
        },
        {
          label: "recovery code regenerate",
          method: "POST",
          path: apiPath("/accounts/2fa/recovery-codes/regenerate/"),
          body: {},
        },
        {
          label: "organization 2FA policy read",
          method: "GET",
          path: apiPath("/accounts/organization/2fa-policy/"),
        },
        {
          label: "organization 2FA policy update",
          method: "PUT",
          path: apiPath("/accounts/organization/2fa-policy/"),
          body: { require_2fa: false, grace_period_days: 7 },
        },
        {
          label: "passkey register options",
          method: "POST",
          path: apiPath("/accounts/passkey/register/options/"),
          body: {},
        },
        {
          label: "passkey register verify",
          method: "POST",
          path: apiPath("/accounts/passkey/register/verify/"),
          body: {},
        },
        {
          label: "passkey detail",
          method: "GET",
          path: apiPath("/accounts/passkeys/{id}/", { id: zeroUuid }),
        },
        {
          label: "passkey delete",
          method: "DELETE",
          path: apiPath("/accounts/passkeys/{id}/", { id: zeroUuid }),
        },
      ];

      for (const endpoint of endpoints) {
        const result = await request(
          apiBase,
          endpoint.method,
          endpoint.path,
          endpoint.body,
        );
        assertJsonAuthGuard(result, endpoint.label);
        evidence.push({
          label: endpoint.label,
          method: endpoint.method,
          path: endpoint.path,
          status: result.status,
          code: result.body?.code || null,
        });
      }
    },
  },
  {
    id: "PUBLIC-AUTH-007",
    title: "Public account auth routes return safe validation contracts",
    tags: [
      "public",
      "auth",
      "guard",
      "accounts",
      "validation",
      "2fa",
      "passkeys",
    ],
    public: true,
    async run({ apiBase, evidence }) {
      const config = await request(
        apiBase,
        "GET",
        apiPath("/accounts/config/"),
      );
      assertStatus(config, 200, "public config");
      assert(
        config.body?.status === true &&
          typeof config.body?.result?.cloud === "boolean" &&
          Array.isArray(config.body?.result?.available_regions),
        `public config payload mismatch: ${formatBody(config.body)}`,
      );
      assertNoSensitiveTokens(config.body, "public config");
      evidence.push({
        label: "public config",
        method: "GET",
        path: apiPath("/accounts/config/"),
        status: config.status,
        cloud: config.body.result.cloud,
      });

      const jsonClientErrorEndpoints = [
        {
          label: "token missing credentials",
          method: "POST",
          path: apiPath("/accounts/token/"),
          body: {},
          expected: "email",
        },
        {
          label: "token refresh missing token",
          method: "POST",
          path: apiPath("/accounts/token/refresh/"),
          body: {},
          expected: "refresh",
        },
        {
          label: "accept invitation invalid preview link",
          method: "GET",
          path: apiPath("/accounts/accept-invitation/{uidb64}/{token}/", {
            uidb64: "bad",
            token: "bad",
          }),
          expected: "Invitation link is invalid or has expired",
        },
        {
          label: "accept invitation invalid password payload",
          method: "POST",
          path: apiPath("/accounts/accept-invitation/{uidb64}/{token}/", {
            uidb64: "bad",
            token: "bad",
          }),
          body: { password: "short" },
          expected: "new_password",
        },
        {
          label: "TOTP login verify missing fields",
          method: "POST",
          path: apiPath("/accounts/2fa/verify/totp/"),
          body: {},
          expected: "challenge_token",
        },
        {
          label: "recovery login verify missing fields",
          method: "POST",
          path: apiPath("/accounts/2fa/verify/recovery/"),
          body: {},
          expected: "challenge_token",
        },
        {
          label: "passkey 2FA options missing challenge",
          method: "POST",
          path: apiPath("/accounts/2fa/verify/passkey/options/"),
          body: {},
          expected: "challenge_token",
        },
        {
          label: "passkey 2FA verify missing fields",
          method: "POST",
          path: apiPath("/accounts/2fa/verify/passkey/"),
          body: {},
          expected: "credential",
        },
        {
          label: "passkey auth verify missing credential",
          method: "POST",
          path: apiPath("/accounts/passkey/authenticate/verify/"),
          body: {},
          expected: "credential",
        },
        {
          label: "signup missing required fields",
          method: "POST",
          path: apiPath("/accounts/signup/"),
          body: {},
          expected: "email",
        },
        {
          label: "logout missing bearer token",
          method: "POST",
          path: apiPath("/accounts/logout/"),
          body: {},
          expected: "No auth token provided",
        },
      ];

      for (const endpoint of jsonClientErrorEndpoints) {
        const result = await request(
          apiBase,
          endpoint.method,
          endpoint.path,
          endpoint.body,
        );
        assertJsonClientError(result, endpoint.expected, endpoint.label);
        evidence.push({
          label: endpoint.label,
          method: endpoint.method,
          path: endpoint.path,
          status: result.status,
          code: result.body?.code || null,
        });
      }

      const onboardingRead = await request(
        apiBase,
        "GET",
        apiPath("/accounts/onboarding/"),
      );
      assertJsonAuthGuard(onboardingRead, "onboarding read");
      evidence.push({
        label: "onboarding read",
        method: "GET",
        path: apiPath("/accounts/onboarding/"),
        status: onboardingRead.status,
        code: onboardingRead.body?.code || null,
      });

      const onboardingUpdate = await request(
        apiBase,
        "POST",
        apiPath("/accounts/onboarding/"),
        {},
      );
      assertJsonAuthGuard(onboardingUpdate, "onboarding update");
      evidence.push({
        label: "onboarding update",
        method: "POST",
        path: apiPath("/accounts/onboarding/"),
        status: onboardingUpdate.status,
        code: onboardingUpdate.body?.code || null,
      });

      const passkeyOptions = await request(
        apiBase,
        "POST",
        apiPath("/accounts/passkey/authenticate/options/"),
        {},
      );
      assertStatus(passkeyOptions, 200, "passkey authenticate options");
      assert(
        typeof passkeyOptions.body?.challenge === "string" &&
          typeof passkeyOptions.body?.session_id === "string" &&
          Array.isArray(passkeyOptions.body?.allowCredentials),
        `passkey authenticate options payload mismatch: ${formatBody(
          passkeyOptions.body,
        )}`,
      );
      assertNoSensitiveTokens(
        passkeyOptions.body,
        "passkey authenticate options",
      );
      evidence.push({
        label: "passkey authenticate options",
        method: "POST",
        path: apiPath("/accounts/passkey/authenticate/options/"),
        status: passkeyOptions.status,
        credential_count: passkeyOptions.body.allowCredentials.length,
      });

      const htmlLinkEndpoints = [
        {
          label: "annotation digest unsubscribe invalid token",
          path: apiPath("/accounts/notifications/unsubscribe/"),
          expected: "Link expired",
        },
        {
          label: "annotation digest snooze invalid token",
          path: `${apiPath("/accounts/notifications/snooze/")}?days=abc`,
          expected: "Link expired",
        },
      ];

      for (const endpoint of htmlLinkEndpoints) {
        const result = await request(apiBase, "GET", endpoint.path);
        assertStatus(result, 200, endpoint.label);
        assert(
          String(result.contentType).includes("text/html"),
          `${endpoint.label} expected text/html, saw ${result.contentType}`,
        );
        assert(
          typeof result.body === "string" &&
            result.body.includes(endpoint.expected),
          `${endpoint.label} missing expected HTML text ${endpoint.expected}`,
        );
        assertNoHtml500(result, endpoint.label);
        assertNoSensitiveTokens(result.body, endpoint.label);
        evidence.push({
          label: endpoint.label,
          method: "GET",
          path: endpoint.path,
          status: result.status,
          content_type: result.contentType,
        });
      }
    },
  },
  {
    id: "PUBLIC-AUTH-008",
    title:
      "System health and ingest compatibility routes expose safe contracts",
    tags: ["public", "auth", "guard", "system", "health", "ingest", "traces"],
    public: true,
    async run({ apiBase, evidence }) {
      const health = await request(apiBase, "GET", apiPath("/health/"));
      assertStatus(health, 200, "root health");
      assert(
        health.body?.status === true &&
          health.body?.result === "Server is up and running",
        `root health payload mismatch: ${formatBody(health.body)}`,
      );
      assertNoSensitiveTokens(health.body, "root health");
      evidence.push({
        label: "root health",
        method: "GET",
        path: apiPath("/health/"),
        status: health.status,
      });

      const deployment = await request(
        apiBase,
        "GET",
        apiPath("/api/deployment-info/"),
      );
      assertStatus(deployment, 200, "deployment info");
      assert(
        deployment.body?.status === true &&
          ["oss", "ee", "cloud"].includes(deployment.body?.result?.mode),
        `deployment info payload mismatch: ${formatBody(deployment.body)}`,
      );
      assertNoSensitiveTokens(deployment.body, "deployment info");
      evidence.push({
        label: "deployment info",
        method: "GET",
        path: apiPath("/api/deployment-info/"),
        status: deployment.status,
        mode: deployment.body.result.mode,
      });

      const clickhouse = await request(
        apiBase,
        "GET",
        apiPath("/api/health/clickhouse/"),
      );
      assertStatus(clickhouse, 200, "ClickHouse health");
      assert(
        ["healthy", "degraded", "unhealthy", "disabled"].includes(
          clickhouse.body?.status,
        ) &&
          typeof clickhouse.body?.clickhouse_connected === "boolean" &&
          clickhouse.body?.cdc_lag &&
          typeof clickhouse.body.cdc_lag === "object" &&
          clickhouse.body?.routing &&
          typeof clickhouse.body.routing === "object",
        `ClickHouse health payload mismatch: ${formatBody(clickhouse.body)}`,
      );
      assertNoSensitiveTokens(clickhouse.body, "ClickHouse health");
      evidence.push({
        label: "ClickHouse health",
        method: "GET",
        path: apiPath("/api/health/clickhouse/"),
        status: clickhouse.status,
        health_status: clickhouse.body.status,
        clickhouse_connected: clickhouse.body.clickhouse_connected,
      });

      const authGuardEndpoints = [
        {
          label: "Langfuse health",
          method: "GET",
          path: apiPath("/api/public/health"),
        },
        {
          label: "Langfuse traces list",
          method: "GET",
          path: apiPath("/api/public/traces"),
        },
        {
          label: "Langfuse ingestion",
          method: "POST",
          path: apiPath("/api/public/ingestion"),
          body: {},
        },
        {
          label: "Langfuse OTLP compat traces",
          method: "POST",
          path: apiPath("/api/public/otel/v1/traces"),
          body: {},
        },
        {
          label: "root OTLP traces",
          method: "POST",
          path: apiPath("/v1/traces/"),
          body: {},
        },
        {
          label: "tracer OTLP traces",
          method: "POST",
          path: apiPath("/tracer/v1/traces"),
          body: {},
        },
        {
          label: "tracer OTLP traces slash",
          method: "POST",
          path: apiPath("/tracer/v1/traces/"),
          body: {},
        },
        {
          label: "AI tools discovery",
          method: "GET",
          path: apiPath("/ai-tools/tools/"),
        },
        {
          label: "span attribute values",
          method: "GET",
          path: apiPath("/api/traces/span-attribute-values/"),
        },
        {
          label: "span attribute detail",
          method: "GET",
          path: apiPath("/api/traces/span-attribute-detail/"),
        },
        {
          label: "call websocket bridge",
          method: "POST",
          path: apiPath("/call-websocket/"),
          body: {},
        },
      ];

      for (const endpoint of authGuardEndpoints) {
        const result = await request(
          apiBase,
          endpoint.method,
          endpoint.path,
          endpoint.body,
        );
        assertJsonAuthGuard(result, endpoint.label);
        evidence.push({
          label: endpoint.label,
          method: endpoint.method,
          path: endpoint.path,
          status: result.status,
          code: result.body?.code || null,
        });
      }
    },
  },
  {
    id: "PUBLIC-AUTH-009",
    title: "Account admin and workspace routes reject anonymous requests",
    tags: [
      "public",
      "auth",
      "guard",
      "accounts",
      "organization",
      "workspace",
      "appsmith",
      "marketplace",
    ],
    public: true,
    async run({ apiBase, evidence }) {
      const zeroUuid = "00000000-0000-4000-8000-000000000456";

      const apiKeyGuardEndpoints = [
        {
          label: "Appsmith users list",
          method: "GET",
          path: apiPath("/accounts/appsmith/users/"),
        },
        {
          label: "Appsmith users patch alias",
          method: "PATCH",
          path: apiPath("/accounts/appsmith/users/"),
          body: {},
        },
        {
          label: "Appsmith users create",
          method: "POST",
          path: apiPath("/accounts/appsmith/users/"),
          body: {},
        },
        {
          label: "Appsmith SOS login",
          method: "POST",
          path: apiPath("/accounts/appsmith/users/login"),
          body: {},
        },
        {
          label: "Appsmith user detail",
          method: "GET",
          path: apiPath("/accounts/appsmith/users/{user_id}/", {
            user_id: zeroUuid,
          }),
        },
        {
          label: "Appsmith user update",
          method: "PATCH",
          path: apiPath("/accounts/appsmith/users/{user_id}/", {
            user_id: zeroUuid,
          }),
          body: {},
        },
        {
          label: "Appsmith user detail post alias",
          method: "POST",
          path: apiPath("/accounts/appsmith/users/{user_id}/", {
            user_id: zeroUuid,
          }),
          body: {},
        },
      ];

      for (const endpoint of apiKeyGuardEndpoints) {
        const result = await request(
          apiBase,
          endpoint.method,
          endpoint.path,
          endpoint.body,
        );
        assertJsonClientError(result, "No API key provided", endpoint.label);
        evidence.push({
          label: endpoint.label,
          method: endpoint.method,
          path: endpoint.path,
          status: result.status,
          code: result.body?.code || null,
        });
      }

      const publicValidationEndpoints = [
        {
          label: "AWS Marketplace launch missing token",
          method: "POST",
          path: apiPath("/accounts/aws-marketplace/launch-software/"),
          body: {},
          expected: "Missing AWS Marketplace registration token",
        },
        {
          label: "AWS Marketplace signup missing fields",
          method: "POST",
          path: apiPath("/accounts/aws-marketplace/signup/"),
          body: {},
          expected: "onboarding_token",
        },
        {
          label: "AWS Marketplace verify-token wrong content type",
          method: "POST",
          path: apiPath("/accounts/aws-marketplace/verify-token/"),
          body: {},
          expected: "Content-Type",
        },
        {
          label: "password reset confirm missing fields",
          method: "POST",
          path: apiPath("/accounts/password-reset-confirm/{uidb64}/{token}/", {
            uidb64: "bad",
            token: "bad",
          }),
          body: {},
          expected: "new_password",
        },
        {
          label: "password reset initiate missing email",
          method: "POST",
          path: apiPath("/accounts/password-reset-initiate/"),
          body: {},
          expected: "email",
        },
      ];

      for (const endpoint of publicValidationEndpoints) {
        const result = await request(
          apiBase,
          endpoint.method,
          endpoint.path,
          endpoint.body,
        );
        assertJsonClientError(result, endpoint.expected, endpoint.label);
        evidence.push({
          label: endpoint.label,
          method: endpoint.method,
          path: endpoint.path,
          status: result.status,
          code: result.body?.code || null,
        });
      }

      const authGuardEndpoints = [
        {
          label: "delete users bulk",
          method: "DELETE",
          path: apiPath("/accounts/delete-users/"),
        },
        {
          label: "organizations list",
          method: "GET",
          path: apiPath("/accounts/organizations/"),
        },
        {
          label: "organizations create alias",
          method: "POST",
          path: apiPath("/accounts/organizations/"),
          body: {},
        },
        {
          label: "organization create",
          method: "POST",
          path: apiPath("/accounts/organizations/create/"),
          body: {},
        },
        {
          label: "current organization",
          method: "GET",
          path: apiPath("/accounts/organizations/current/"),
        },
        {
          label: "additional organization create",
          method: "POST",
          path: apiPath("/accounts/organizations/new/"),
          body: {},
        },
        {
          label: "organization switch",
          method: "POST",
          path: apiPath("/accounts/organizations/switch/"),
          body: {},
        },
        {
          label: "organization update",
          method: "PATCH",
          path: apiPath("/accounts/organizations/update/"),
          body: {},
        },
        {
          label: "passkey rename",
          method: "PATCH",
          path: apiPath("/accounts/passkeys/{id}/", { id: zeroUuid }),
          body: {},
        },
        {
          label: "redis key delete",
          method: "DELETE",
          path: apiPath("/accounts/redis-key/"),
        },
        {
          label: "redis key set",
          method: "POST",
          path: apiPath("/accounts/redis-key/"),
          body: {},
        },
        {
          label: "resend invitation emails",
          method: "POST",
          path: apiPath("/accounts/resend-invitation-emails/"),
          body: {},
        },
        {
          label: "update user",
          method: "POST",
          path: apiPath("/accounts/update-user/"),
          body: {},
        },
        {
          label: "workspace user deactivate",
          method: "POST",
          path: apiPath("/accounts/user/deactivate/"),
          body: {},
        },
        {
          label: "workspace user delete",
          method: "POST",
          path: apiPath("/accounts/user/delete/"),
          body: {},
        },
        {
          label: "workspace user resend invite",
          method: "POST",
          path: apiPath("/accounts/user/resend-invite/"),
          body: {},
        },
        {
          label: "workspace user role update",
          method: "POST",
          path: apiPath("/accounts/user/role/update/"),
          body: {},
        },
        {
          label: "workspace invite",
          method: "POST",
          path: apiPath("/accounts/workspace/invite/"),
          body: {},
        },
        {
          label: "workspace switch",
          method: "POST",
          path: apiPath("/accounts/workspace/switch/"),
          body: {},
        },
        {
          label: "legacy workspaces delete alias",
          method: "DELETE",
          path: apiPath("/accounts/workspaces/"),
        },
        {
          label: "legacy workspaces list",
          method: "GET",
          path: apiPath("/accounts/workspaces/"),
        },
        {
          label: "legacy workspaces create",
          method: "POST",
          path: apiPath("/accounts/workspaces/"),
          body: {},
        },
        {
          label: "legacy workspaces update alias",
          method: "PUT",
          path: apiPath("/accounts/workspaces/"),
          body: {},
        },
        {
          label: "legacy workspace delete",
          method: "DELETE",
          path: apiPath("/accounts/workspaces/{workspace_id}/", {
            workspace_id: zeroUuid,
          }),
        },
        {
          label: "legacy workspace detail",
          method: "GET",
          path: apiPath("/accounts/workspaces/{workspace_id}/", {
            workspace_id: zeroUuid,
          }),
        },
        {
          label: "legacy workspace update post alias",
          method: "POST",
          path: apiPath("/accounts/workspaces/{workspace_id}/", {
            workspace_id: zeroUuid,
          }),
          body: {},
        },
        {
          label: "legacy workspace members delete alias",
          method: "DELETE",
          path: apiPath("/accounts/workspaces/{workspace_id}/members/", {
            workspace_id: zeroUuid,
          }),
        },
        {
          label: "legacy workspace members add",
          method: "POST",
          path: apiPath("/accounts/workspaces/{workspace_id}/members/", {
            workspace_id: zeroUuid,
          }),
          body: {},
        },
        {
          label: "legacy workspace member delete",
          method: "DELETE",
          path: apiPath(
            "/accounts/workspaces/{workspace_id}/members/{member_id}/",
            { workspace_id: zeroUuid, member_id: zeroUuid },
          ),
        },
        {
          label: "legacy workspace member detail",
          method: "GET",
          path: apiPath(
            "/accounts/workspaces/{workspace_id}/members/{member_id}/",
            { workspace_id: zeroUuid, member_id: zeroUuid },
          ),
        },
        {
          label: "legacy workspace member post alias",
          method: "POST",
          path: apiPath(
            "/accounts/workspaces/{workspace_id}/members/{member_id}/",
            { workspace_id: zeroUuid, member_id: zeroUuid },
          ),
          body: {},
        },
      ];

      for (const endpoint of authGuardEndpoints) {
        const result = await request(
          apiBase,
          endpoint.method,
          endpoint.path,
          endpoint.body,
        );
        assertJsonAuthGuard(result, endpoint.label);
        evidence.push({
          label: endpoint.label,
          method: endpoint.method,
          path: endpoint.path,
          status: result.status,
          code: result.body?.code || null,
        });
      }
    },
  },
  {
    id: "PUBLIC-AUTH-010",
    title: "Generated AgentCC routes reject anonymous requests safely",
    tags: [
      "public",
      "auth",
      "guard",
      "agentcc",
      "gateway",
      "webhooks",
      "admin-token",
    ],
    public: true,
    async run({ apiBase, evidence }) {
      const zeroUuid = "00000000-0000-4000-8000-000000000456";
      const pathParams = { id: zeroUuid, session_id: zeroUuid };

      const authGuardEndpointSpecs = [
        ["GET", "/agentcc/analytics/cost-breakdown/"],
        ["GET", "/agentcc/analytics/error-breakdown/"],
        ["GET", "/agentcc/analytics/guardrail-overview/"],
        ["GET", "/agentcc/analytics/guardrail-rules/"],
        ["GET", "/agentcc/analytics/guardrail-trends/"],
        ["GET", "/agentcc/analytics/latency-stats/"],
        ["GET", "/agentcc/analytics/model-comparison/"],
        ["GET", "/agentcc/analytics/overview/"],
        ["GET", "/agentcc/analytics/usage-timeseries/"],
        ["PUT", "/agentcc/api-keys/{id}/"],
        ["GET", "/agentcc/blocklists/"],
        ["POST", "/agentcc/blocklists/"],
        ["DELETE", "/agentcc/blocklists/{id}/"],
        ["GET", "/agentcc/blocklists/{id}/"],
        ["PATCH", "/agentcc/blocklists/{id}/"],
        ["PUT", "/agentcc/blocklists/{id}/"],
        ["POST", "/agentcc/blocklists/{id}/add-words/"],
        ["POST", "/agentcc/blocklists/{id}/remove-words/"],
        ["GET", "/agentcc/custom-properties/"],
        ["POST", "/agentcc/custom-properties/"],
        ["POST", "/agentcc/custom-properties/validate/"],
        ["DELETE", "/agentcc/custom-properties/{id}/"],
        ["GET", "/agentcc/custom-properties/{id}/"],
        ["PATCH", "/agentcc/custom-properties/{id}/"],
        ["PUT", "/agentcc/custom-properties/{id}/"],
        ["GET", "/agentcc/email-alerts/"],
        ["POST", "/agentcc/email-alerts/"],
        ["DELETE", "/agentcc/email-alerts/{id}/"],
        ["GET", "/agentcc/email-alerts/{id}/"],
        ["PATCH", "/agentcc/email-alerts/{id}/"],
        ["PUT", "/agentcc/email-alerts/{id}/"],
        ["POST", "/agentcc/email-alerts/{id}/test/"],
        ["GET", "/agentcc/gateways/protect-templates/"],
        ["POST", "/agentcc/gateways/{id}/cancel-batch/"],
        ["GET", "/agentcc/gateways/{id}/config/"],
        ["GET", "/agentcc/gateways/{id}/get-batch/"],
        ["POST", "/agentcc/gateways/{id}/health_check/"],
        ["GET", "/agentcc/gateways/{id}/mcp-prompts/"],
        ["GET", "/agentcc/gateways/{id}/mcp-resources/"],
        ["GET", "/agentcc/gateways/{id}/mcp-status/"],
        ["GET", "/agentcc/gateways/{id}/mcp-tools/"],
        ["GET", "/agentcc/gateways/{id}/providers/"],
        ["POST", "/agentcc/gateways/{id}/reload/"],
        ["POST", "/agentcc/gateways/{id}/remove-budget/"],
        ["POST", "/agentcc/gateways/{id}/remove-mcp-server/"],
        ["POST", "/agentcc/gateways/{id}/remove-provider/"],
        ["POST", "/agentcc/gateways/{id}/set-budget/"],
        ["POST", "/agentcc/gateways/{id}/submit-batch/"],
        ["POST", "/agentcc/gateways/{id}/test-mcp-tool/"],
        ["POST", "/agentcc/gateways/{id}/toggle-guardrail/"],
        ["POST", "/agentcc/gateways/{id}/update-config/"],
        ["POST", "/agentcc/gateways/{id}/update-guardrail/"],
        ["POST", "/agentcc/gateways/{id}/update-mcp-guardrails/"],
        ["POST", "/agentcc/gateways/{id}/update-mcp-server/"],
        ["POST", "/agentcc/gateways/{id}/update-provider/"],
        ["GET", "/agentcc/guardrail-configs/pii-entities/"],
        ["GET", "/agentcc/guardrail-configs/topics/"],
        ["POST", "/agentcc/guardrail-configs/validate-cel/"],
        ["GET", "/agentcc/guardrail-feedback/"],
        ["POST", "/agentcc/guardrail-feedback/"],
        ["GET", "/agentcc/guardrail-feedback/summary/"],
        ["DELETE", "/agentcc/guardrail-feedback/{id}/"],
        ["GET", "/agentcc/guardrail-feedback/{id}/"],
        ["PATCH", "/agentcc/guardrail-feedback/{id}/"],
        ["PUT", "/agentcc/guardrail-feedback/{id}/"],
        ["GET", "/agentcc/guardrail-policies/"],
        ["POST", "/agentcc/guardrail-policies/"],
        ["POST", "/agentcc/guardrail-policies/sync/"],
        ["DELETE", "/agentcc/guardrail-policies/{id}/"],
        ["GET", "/agentcc/guardrail-policies/{id}/"],
        ["PATCH", "/agentcc/guardrail-policies/{id}/"],
        ["PUT", "/agentcc/guardrail-policies/{id}/"],
        ["POST", "/agentcc/guardrail-policies/{id}/apply/"],
        ["GET", "/agentcc/org-configs/"],
        ["POST", "/agentcc/org-configs/"],
        ["GET", "/agentcc/org-configs/active/"],
        ["DELETE", "/agentcc/org-configs/{id}/"],
        ["GET", "/agentcc/org-configs/{id}/"],
        ["PATCH", "/agentcc/org-configs/{id}/"],
        ["PUT", "/agentcc/org-configs/{id}/"],
        ["POST", "/agentcc/org-configs/{id}/activate/"],
        ["GET", "/agentcc/org-configs/{id}/diff/"],
        ["GET", "/agentcc/provider-credentials/"],
        ["POST", "/agentcc/provider-credentials/"],
        ["POST", "/agentcc/provider-credentials/fetch_models/"],
        ["DELETE", "/agentcc/provider-credentials/{id}/"],
        ["GET", "/agentcc/provider-credentials/{id}/"],
        ["PATCH", "/agentcc/provider-credentials/{id}/"],
        ["PUT", "/agentcc/provider-credentials/{id}/"],
        ["POST", "/agentcc/provider-credentials/{id}/rotate/"],
        ["GET", "/agentcc/request-logs/"],
        ["GET", "/agentcc/request-logs/export/"],
        ["GET", "/agentcc/request-logs/search/"],
        ["GET", "/agentcc/request-logs/sessions/"],
        ["GET", "/agentcc/request-logs/sessions/{session_id}/"],
        ["GET", "/agentcc/request-logs/{id}/"],
        ["GET", "/agentcc/routing-policies/"],
        ["POST", "/agentcc/routing-policies/"],
        ["POST", "/agentcc/routing-policies/sync/"],
        ["DELETE", "/agentcc/routing-policies/{id}/"],
        ["GET", "/agentcc/routing-policies/{id}/"],
        ["PATCH", "/agentcc/routing-policies/{id}/"],
        ["PUT", "/agentcc/routing-policies/{id}/"],
        ["POST", "/agentcc/routing-policies/{id}/activate/"],
        ["GET", "/agentcc/sessions/"],
        ["POST", "/agentcc/sessions/"],
        ["DELETE", "/agentcc/sessions/{id}/"],
        ["GET", "/agentcc/sessions/{id}/"],
        ["PATCH", "/agentcc/sessions/{id}/"],
        ["PUT", "/agentcc/sessions/{id}/"],
        ["POST", "/agentcc/sessions/{id}/close/"],
        ["GET", "/agentcc/sessions/{id}/requests/"],
        ["GET", "/agentcc/shadow-experiments/"],
        ["POST", "/agentcc/shadow-experiments/"],
        ["DELETE", "/agentcc/shadow-experiments/{id}/"],
        ["GET", "/agentcc/shadow-experiments/{id}/"],
        ["PATCH", "/agentcc/shadow-experiments/{id}/"],
        ["PUT", "/agentcc/shadow-experiments/{id}/"],
        ["PATCH", "/agentcc/shadow-experiments/{id}/complete/"],
        ["PATCH", "/agentcc/shadow-experiments/{id}/pause/"],
        ["PATCH", "/agentcc/shadow-experiments/{id}/resume/"],
        ["GET", "/agentcc/shadow-experiments/{id}/stats/"],
        ["GET", "/agentcc/shadow-results/"],
        ["GET", "/agentcc/shadow-results/{id}/"],
        ["GET", "/agentcc/webhook-events/"],
        ["GET", "/agentcc/webhook-events/{id}/"],
        ["POST", "/agentcc/webhook-events/{id}/retry/"],
        ["GET", "/agentcc/webhooks/"],
        ["POST", "/agentcc/webhooks/"],
        ["DELETE", "/agentcc/webhooks/{id}/"],
        ["GET", "/agentcc/webhooks/{id}/"],
        ["PATCH", "/agentcc/webhooks/{id}/"],
        ["PUT", "/agentcc/webhooks/{id}/"],
        ["POST", "/agentcc/webhooks/{id}/test/"],
      ];

      const adminTokenEndpointSpecs = [
        ["GET", "/agentcc/api-keys/bulk/"],
        ["GET", "/agentcc/org-configs/bulk/"],
        ["GET", "/agentcc/spend-summary/"],
      ];

      const webhookValidationEndpointSpecs = [
        ["POST", "/agentcc/webhook/logs/"],
        ["POST", "/agentcc/webhook/shadow-results/"],
      ];

      const endpointFromSpec = ([method, template]) => ({
        label: `${method} ${template}`,
        method,
        path: apiPath(template, pathParams),
        body: ["POST", "PUT", "PATCH"].includes(method) ? {} : undefined,
      });

      for (const endpoint of authGuardEndpointSpecs.map(endpointFromSpec)) {
        const result = await request(
          apiBase,
          endpoint.method,
          endpoint.path,
          endpoint.body,
        );
        assertJsonAuthGuard(result, endpoint.label);
        evidence.push({
          label: endpoint.label,
          method: endpoint.method,
          path: endpoint.path,
          status: result.status,
          code: result.body?.code || null,
        });
      }

      for (const endpoint of adminTokenEndpointSpecs.map(endpointFromSpec)) {
        const result = await request(
          apiBase,
          endpoint.method,
          endpoint.path,
          endpoint.body,
        );
        assertJsonPermissionGuard(result, endpoint.label);
        evidence.push({
          label: endpoint.label,
          method: endpoint.method,
          path: endpoint.path,
          status: result.status,
          code: result.body?.code || null,
        });
      }

      for (const endpoint of webhookValidationEndpointSpecs.map(
        endpointFromSpec,
      )) {
        const result = await request(
          apiBase,
          endpoint.method,
          endpoint.path,
          endpoint.body,
        );
        assertWebhookSecretGuard(result, endpoint.label);
        evidence.push({
          label: endpoint.label,
          method: endpoint.method,
          path: endpoint.path,
          status: result.status,
          code: result.body?.code || null,
        });
      }
    },
  },
  {
    id: "PUBLIC-AUTH-011",
    title: "Generated Falcon AI routes reject anonymous requests safely",
    tags: ["public", "auth", "guard", "falcon", "mcp-connectors", "oauth"],
    public: true,
    async run({ apiBase, evidence }) {
      const zeroUuid = "00000000-0000-4000-8000-000000000456";
      const pathParams = {
        conversation_id: zeroUuid,
        connector_id: zeroUuid,
        memory_id: zeroUuid,
        message_id: zeroUuid,
        skill_id: zeroUuid,
      };

      const authGuardEndpointSpecs = [
        ["GET", "/falcon-ai/conversations/"],
        ["POST", "/falcon-ai/conversations/"],
        ["DELETE", "/falcon-ai/conversations/{conversation_id}/"],
        ["GET", "/falcon-ai/conversations/{conversation_id}/"],
        ["PATCH", "/falcon-ai/conversations/{conversation_id}/"],
        ["GET", "/falcon-ai/conversations/{conversation_id}/stream-status/"],
        ["POST", "/falcon-ai/files/upload/"],
        ["GET", "/falcon-ai/mcp-connectors/"],
        ["POST", "/falcon-ai/mcp-connectors/"],
        ["DELETE", "/falcon-ai/mcp-connectors/{connector_id}/"],
        ["GET", "/falcon-ai/mcp-connectors/{connector_id}/"],
        ["PATCH", "/falcon-ai/mcp-connectors/{connector_id}/"],
        ["POST", "/falcon-ai/mcp-connectors/{connector_id}/authenticate/"],
        ["POST", "/falcon-ai/mcp-connectors/{connector_id}/discover/"],
        ["POST", "/falcon-ai/mcp-connectors/{connector_id}/test/"],
        ["PATCH", "/falcon-ai/mcp-connectors/{connector_id}/tools/"],
        ["GET", "/falcon-ai/memory/"],
        ["POST", "/falcon-ai/memory/"],
        ["DELETE", "/falcon-ai/memory/{memory_id}/"],
        ["POST", "/falcon-ai/messages/{message_id}/feedback/"],
        ["POST", "/falcon-ai/quick-analysis/"],
        ["GET", "/falcon-ai/skills/"],
        ["POST", "/falcon-ai/skills/"],
        ["DELETE", "/falcon-ai/skills/{skill_id}/"],
        ["GET", "/falcon-ai/skills/{skill_id}/"],
        ["PATCH", "/falcon-ai/skills/{skill_id}/"],
      ];

      const endpointFromSpec = ([method, template]) => ({
        label: `${method} ${template}`,
        method,
        path: apiPath(template, pathParams),
        body: ["POST", "PUT", "PATCH"].includes(method) ? {} : undefined,
      });

      for (const endpoint of authGuardEndpointSpecs.map(endpointFromSpec)) {
        const result = await request(
          apiBase,
          endpoint.method,
          endpoint.path,
          endpoint.body,
        );
        assertJsonAuthGuard(result, endpoint.label);
        evidence.push({
          label: endpoint.label,
          method: endpoint.method,
          path: endpoint.path,
          status: result.status,
          code: result.body?.code || null,
        });
      }

      const oauthCallback = await request(
        apiBase,
        "GET",
        apiPath(
          "/falcon-ai/mcp-connectors/{connector_id}/oauth/callback/",
          pathParams,
        ),
      );
      assertHtmlClientError(
        oauthCallback,
        "Missing code or state parameter",
        "Falcon MCP OAuth callback missing code/state",
      );
      evidence.push({
        label: "GET /falcon-ai/mcp-connectors/{connector_id}/oauth/callback/",
        method: "GET",
        path: apiPath(
          "/falcon-ai/mcp-connectors/{connector_id}/oauth/callback/",
          pathParams,
        ),
        status: oauthCallback.status,
        content_type: oauthCallback.contentType,
      });
    },
  },
  {
    id: "PUBLIC-AUTH-012",
    title: "Generated Agent Playground routes reject anonymous requests safely",
    tags: ["public", "auth", "guard", "agent-playground", "graphs"],
    public: true,
    async run({ apiBase, evidence }) {
      const zeroUuid = "00000000-0000-4000-8000-000000000456";
      const pathParams = {
        id: zeroUuid,
        version_id: zeroUuid,
        nc_id: zeroUuid,
        node_id: zeroUuid,
      };

      const authGuardEndpointSpecs = [
        ["POST", "/agent-playground/graphs/from-trace/"],
        ["PUT", "/agent-playground/graphs/{id}/"],
        ["DELETE", "/agent-playground/graphs/{id}/"],
        ["PUT", "/agent-playground/graphs/{id}/versions/{version_id}/"],
        [
          "POST",
          "/agent-playground/graphs/{id}/versions/{version_id}/node-connections/",
        ],
        [
          "DELETE",
          "/agent-playground/graphs/{id}/versions/{version_id}/node-connections/{nc_id}/",
        ],
        [
          "DELETE",
          "/agent-playground/graphs/{id}/versions/{version_id}/nodes/{node_id}/",
        ],
      ];

      const endpointFromSpec = ([method, template]) => ({
        label: `${method} ${template}`,
        method,
        path: apiPath(template, pathParams),
        body: ["POST", "PUT", "PATCH"].includes(method) ? {} : undefined,
      });

      for (const endpoint of authGuardEndpointSpecs.map(endpointFromSpec)) {
        const result = await request(
          apiBase,
          endpoint.method,
          endpoint.path,
          endpoint.body,
        );
        assertJsonAuthGuard(result, endpoint.label);
        evidence.push({
          label: endpoint.label,
          method: endpoint.method,
          path: endpoint.path,
          status: result.status,
          code: result.body?.code || null,
        });
      }
    },
  },
  {
    id: "PUBLIC-AUTH-013",
    title:
      "Generated MCP, integrations, and OTLP health routes expose safe boundaries",
    tags: ["public", "auth", "guard", "mcp", "integrations", "otlp", "health"],
    public: true,
    async run({ apiBase, evidence }) {
      const zeroUuid = "00000000-0000-4000-8000-000000000456";

      const health = await request(apiBase, "GET", apiPath("/v1/health"));
      assertStatus(health, 200, "OTLP v1 health");
      assert(
        health.body?.status === "healthy" &&
          health.body?.service === "otlp-trace-receiver",
        `OTLP v1 health payload mismatch: ${formatBody(health.body)}`,
      );
      assertNoSensitiveTokens(health.body, "OTLP v1 health");
      evidence.push({
        label: "GET /v1/health",
        method: "GET",
        path: apiPath("/v1/health"),
        status: health.status,
        service: health.body.service,
      });

      const authGuardEndpointSpecs = [
        ["PUT", "/mcp/config/"],
        ["POST", "/mcp/internal/tool-call/"],
        ["GET", "/integrations/sync-logs/{id}/"],
      ];

      const endpointFromSpec = ([method, template]) => ({
        label: `${method} ${template}`,
        method,
        path: apiPath(template, { id: zeroUuid }),
        body: ["POST", "PUT", "PATCH"].includes(method) ? {} : undefined,
      });

      for (const endpoint of authGuardEndpointSpecs.map(endpointFromSpec)) {
        const result = await request(
          apiBase,
          endpoint.method,
          endpoint.path,
          endpoint.body,
        );
        assertJsonAuthGuard(result, endpoint.label);
        evidence.push({
          label: endpoint.label,
          method: endpoint.method,
          path: endpoint.path,
          status: result.status,
          code: result.body?.code || null,
        });
      }
    },
  },
  {
    id: "PUBLIC-AUTH-014",
    title:
      "Generated SAML auth routes expose public validation and auth boundaries",
    tags: ["public", "auth", "guard", "saml", "sso"],
    public: true,
    async run({ apiBase, evidence }) {
      const zeroUuid = "00000000-0000-4000-8000-000000000789";

      const publicValidationSpecs = [
        {
          label: "GET /saml2_auth/idp-login/",
          path: apiPath("/saml2_auth/idp-login/"),
          expectedText: "email",
        },
        {
          label: "GET /saml2_auth/login/",
          path: `${apiPath("/saml2_auth/login/")}?provider=slack`,
          expectedText: "provider",
        },
        {
          label: "GET /saml2_auth/login{format}",
          path: `${apiPath("/saml2_auth/login{format}", {
            format: ".json",
          })}?provider=slack`,
          expectedText: "provider",
        },
      ];

      for (const endpoint of publicValidationSpecs) {
        const result = await request(apiBase, "GET", endpoint.path);
        assertJsonClientError(result, endpoint.expectedText, endpoint.label);
        evidence.push({
          label: endpoint.label,
          method: "GET",
          path: endpoint.path,
          status: result.status,
          content_type: result.contentType,
        });
      }

      const authGuardEndpointSpecs = [
        ["GET", "/saml2_auth/idp-uploads/"],
        ["POST", "/saml2_auth/idp-uploads/"],
        ["GET", "/saml2_auth/idp-uploads/{id}/"],
        ["PUT", "/saml2_auth/idp-uploads/{id}/"],
        ["DELETE", "/saml2_auth/idp-uploads/{id}/"],
      ];

      for (const [method, template] of authGuardEndpointSpecs) {
        const path = apiPath(template, { id: zeroUuid });
        const result = await request(
          apiBase,
          method,
          path,
          ["POST", "PUT", "PATCH"].includes(method) ? {} : undefined,
        );
        assertJsonAuthGuard(result, `${method} ${template}`);
        evidence.push({
          label: `${method} ${template}`,
          method,
          path,
          status: result.status,
          code: result.body?.code || null,
        });
      }

      const redirectSpecs = [
        ["POST", "/saml2_auth/acs/", apiPath("/saml2_auth/acs/")],
        [
          "GET",
          "/saml2_auth/auth/callback/",
          apiPath("/saml2_auth/auth/callback/"),
        ],
        [
          "GET",
          "/saml2_auth/auth/callback{format}",
          apiPath("/saml2_auth/auth/callback{format}", { format: ".json" }),
        ],
        [
          "GET",
          "/saml2_auth/github/callback/",
          apiPath("/saml2_auth/github/callback/"),
        ],
        [
          "GET",
          "/saml2_auth/github/callback{format}",
          apiPath("/saml2_auth/github/callback{format}", { format: ".json" }),
        ],
        [
          "GET",
          "/saml2_auth/microsoft/callback/",
          apiPath("/saml2_auth/microsoft/callback/"),
        ],
        [
          "GET",
          "/saml2_auth/microsoft/callback{format}",
          apiPath("/saml2_auth/microsoft/callback{format}", {
            format: ".json",
          }),
        ],
      ];

      for (const [method, template, path] of redirectSpecs) {
        const result = await request(apiBase, method, path, undefined, {
          redirect: "manual",
        });
        assertSamlPublicRedirect(result, `${method} ${template}`);
        evidence.push({
          label: `${method} ${template}`,
          method,
          path,
          status: result.status,
          location_present: Boolean(result.location),
        });
      }
    },
  },
  {
    id: "PUBLIC-AUTH-015",
    title:
      "Generated Scenarios action routes reject anonymous requests before mutation or lookup",
    tags: ["public", "auth", "guard", "simulation", "scenarios"],
    public: true,
    async run({ apiBase, evidence }) {
      const zeroUuid = "00000000-0000-4000-8000-000000000987";
      const endpoints = [
        {
          label: "GET /simulate/scenarios/get-columns/",
          method: "GET",
          path: apiPath("/simulate/scenarios/get-columns/"),
        },
        {
          label: "POST /simulate/scenarios/{scenario_id}/add-columns/",
          method: "POST",
          path: apiPath("/simulate/scenarios/{scenario_id}/add-columns/", {
            scenario_id: zeroUuid,
          }),
          body: {},
        },
        {
          label: "POST /simulate/scenarios/{scenario_id}/add-rows/",
          method: "POST",
          path: apiPath("/simulate/scenarios/{scenario_id}/add-rows/", {
            scenario_id: zeroUuid,
          }),
          body: {},
        },
        {
          label: "DELETE /simulate/scenarios/{scenario_id}/delete/",
          method: "DELETE",
          path: apiPath("/simulate/scenarios/{scenario_id}/delete/", {
            scenario_id: zeroUuid,
          }),
        },
        {
          label: "PUT /simulate/scenarios/{scenario_id}/edit/",
          method: "PUT",
          path: apiPath("/simulate/scenarios/{scenario_id}/edit/", {
            scenario_id: zeroUuid,
          }),
          body: {},
        },
        {
          label: "PUT /simulate/scenarios/{scenario_id}/prompts/",
          method: "PUT",
          path: apiPath("/simulate/scenarios/{scenario_id}/prompts/", {
            scenario_id: zeroUuid,
          }),
          body: {},
        },
      ];

      for (const endpoint of endpoints) {
        const result = await request(
          apiBase,
          endpoint.method,
          endpoint.path,
          endpoint.body,
        );
        assertJsonAuthGuard(result, endpoint.label);
        evidence.push({
          label: endpoint.label,
          method: endpoint.method,
          path: endpoint.path,
          status: result.status,
          code: result.body?.code || null,
        });
      }
    },
  },
  {
    id: "PUBLIC-AUTH-016",
    title:
      "Generated Simulator Agent routes reject anonymous requests before list, lookup, or mutation",
    tags: ["public", "auth", "guard", "simulation", "simulator-agents"],
    public: true,
    async run({ apiBase, evidence }) {
      const zeroUuid = "00000000-0000-4000-8000-000000001016";
      const endpoints = [
        {
          label: "GET /simulate/simulator-agents/",
          method: "GET",
          path: apiPath("/simulate/simulator-agents/"),
        },
        {
          label: "POST /simulate/simulator-agents/create/",
          method: "POST",
          path: apiPath("/simulate/simulator-agents/create/"),
          body: {},
        },
        {
          label: "GET /simulate/simulator-agents/{agent_id}/",
          method: "GET",
          path: apiPath("/simulate/simulator-agents/{agent_id}/", {
            agent_id: zeroUuid,
          }),
        },
        {
          label: "DELETE /simulate/simulator-agents/{agent_id}/delete/",
          method: "DELETE",
          path: apiPath("/simulate/simulator-agents/{agent_id}/delete/", {
            agent_id: zeroUuid,
          }),
        },
        {
          label: "PUT /simulate/simulator-agents/{agent_id}/edit/",
          method: "PUT",
          path: apiPath("/simulate/simulator-agents/{agent_id}/edit/", {
            agent_id: zeroUuid,
          }),
          body: {},
        },
      ];

      for (const endpoint of endpoints) {
        const result = await request(
          apiBase,
          endpoint.method,
          endpoint.path,
          endpoint.body,
        );
        assertJsonAuthGuard(result, endpoint.label);
        evidence.push({
          label: endpoint.label,
          method: endpoint.method,
          path: endpoint.path,
          status: result.status,
          code: result.body?.code || null,
        });
      }
    },
  },
  {
    id: "PUBLIC-AUTH-017",
    title:
      "Generated Agent Definition routes reject anonymous requests before list, version, or mutation work",
    tags: ["public", "auth", "guard", "simulation", "agent-definitions"],
    public: true,
    async run({ apiBase, evidence }) {
      const agentId = "00000000-0000-4000-8000-000000001017";
      const versionId = "00000000-0000-4000-8000-000000001018";
      const endpoints = [
        {
          label: "GET /simulate/agent-definitions/",
          method: "GET",
          path: apiPath("/simulate/agent-definitions/"),
        },
        {
          label: "DELETE /simulate/agent-definitions/",
          method: "DELETE",
          path: apiPath("/simulate/agent-definitions/"),
        },
        {
          label: "POST /simulate/agent-definitions/create/",
          method: "POST",
          path: apiPath("/simulate/agent-definitions/create/"),
          body: {},
        },
        {
          label: "GET /simulate/agent-definitions/{agent_id}/",
          method: "GET",
          path: apiPath("/simulate/agent-definitions/{agent_id}/", {
            agent_id: agentId,
          }),
        },
        {
          label: "DELETE /simulate/agent-definitions/{agent_id}/delete/",
          method: "DELETE",
          path: apiPath("/simulate/agent-definitions/{agent_id}/delete/", {
            agent_id: agentId,
          }),
        },
        {
          label: "PUT /simulate/agent-definitions/{agent_id}/edit/",
          method: "PUT",
          path: apiPath("/simulate/agent-definitions/{agent_id}/edit/", {
            agent_id: agentId,
          }),
          body: {},
        },
        {
          label: "GET /simulate/agent-definitions/{agent_id}/versions/",
          method: "GET",
          path: apiPath("/simulate/agent-definitions/{agent_id}/versions/", {
            agent_id: agentId,
          }),
        },
        {
          label: "POST /simulate/agent-definitions/{agent_id}/versions/create/",
          method: "POST",
          path: apiPath(
            "/simulate/agent-definitions/{agent_id}/versions/create/",
            {
              agent_id: agentId,
            },
          ),
          body: {},
        },
        {
          label:
            "GET /simulate/agent-definitions/{agent_id}/versions/{version_id}/",
          method: "GET",
          path: apiPath(
            "/simulate/agent-definitions/{agent_id}/versions/{version_id}/",
            {
              agent_id: agentId,
              version_id: versionId,
            },
          ),
        },
        {
          label:
            "POST /simulate/agent-definitions/{agent_id}/versions/{version_id}/activate/",
          method: "POST",
          path: apiPath(
            "/simulate/agent-definitions/{agent_id}/versions/{version_id}/activate/",
            {
              agent_id: agentId,
              version_id: versionId,
            },
          ),
          body: {},
        },
        {
          label:
            "GET /simulate/agent-definitions/{agent_id}/versions/{version_id}/call-executions/",
          method: "GET",
          path: apiPath(
            "/simulate/agent-definitions/{agent_id}/versions/{version_id}/call-executions/",
            {
              agent_id: agentId,
              version_id: versionId,
            },
          ),
        },
        {
          label:
            "DELETE /simulate/agent-definitions/{agent_id}/versions/{version_id}/delete/",
          method: "DELETE",
          path: apiPath(
            "/simulate/agent-definitions/{agent_id}/versions/{version_id}/delete/",
            {
              agent_id: agentId,
              version_id: versionId,
            },
          ),
        },
        {
          label:
            "GET /simulate/agent-definitions/{agent_id}/versions/{version_id}/eval-summary/",
          method: "GET",
          path: apiPath(
            "/simulate/agent-definitions/{agent_id}/versions/{version_id}/eval-summary/",
            {
              agent_id: agentId,
              version_id: versionId,
            },
          ),
        },
        {
          label:
            "POST /simulate/agent-definitions/{agent_id}/versions/{version_id}/restore/",
          method: "POST",
          path: apiPath(
            "/simulate/agent-definitions/{agent_id}/versions/{version_id}/restore/",
            {
              agent_id: agentId,
              version_id: versionId,
            },
          ),
          body: {},
        },
      ];

      for (const endpoint of endpoints) {
        const result = await request(
          apiBase,
          endpoint.method,
          endpoint.path,
          endpoint.body,
        );
        assertJsonAuthGuard(result, endpoint.label);
        evidence.push({
          label: endpoint.label,
          method: endpoint.method,
          path: endpoint.path,
          status: result.status,
          code: result.body?.code || null,
        });
      }
    },
  },
  {
    id: "PUBLIC-AUTH-018",
    title:
      "Generated Agent Definition operation routes reject anonymous requests before router or provider work",
    tags: [
      "public",
      "auth",
      "guard",
      "simulation",
      "agent-definition-operations",
    ],
    public: true,
    async run({ apiBase, evidence }) {
      const agentId = "00000000-0000-4000-8000-000000001019";
      const endpoints = [
        {
          label: "GET /simulate/api/agent-definition-operations/",
          method: "GET",
          path: apiPath("/simulate/api/agent-definition-operations/"),
        },
        {
          label: "POST /simulate/api/agent-definition-operations/",
          method: "POST",
          path: apiPath("/simulate/api/agent-definition-operations/"),
          body: {},
        },
        {
          label:
            "POST /simulate/api/agent-definition-operations/fetch_assistant_from_provider/",
          method: "POST",
          path: apiPath(
            "/simulate/api/agent-definition-operations/fetch_assistant_from_provider/",
          ),
          body: {},
        },
        {
          label: "GET /simulate/api/agent-definition-operations/{id}/",
          method: "GET",
          path: apiPath("/simulate/api/agent-definition-operations/{id}/", {
            id: agentId,
          }),
        },
        {
          label: "PUT /simulate/api/agent-definition-operations/{id}/",
          method: "PUT",
          path: apiPath("/simulate/api/agent-definition-operations/{id}/", {
            id: agentId,
          }),
          body: {},
        },
        {
          label: "PATCH /simulate/api/agent-definition-operations/{id}/",
          method: "PATCH",
          path: apiPath("/simulate/api/agent-definition-operations/{id}/", {
            id: agentId,
          }),
          body: {},
        },
        {
          label: "DELETE /simulate/api/agent-definition-operations/{id}/",
          method: "DELETE",
          path: apiPath("/simulate/api/agent-definition-operations/{id}/", {
            id: agentId,
          }),
        },
      ];

      for (const endpoint of endpoints) {
        const result = await request(
          apiBase,
          endpoint.method,
          endpoint.path,
          endpoint.body,
        );
        assertJsonAuthGuard(result, endpoint.label);
        evidence.push({
          label: endpoint.label,
          method: endpoint.method,
          path: endpoint.path,
          status: result.status,
          code: result.body?.code || null,
        });
      }
    },
  },
  {
    id: "PUBLIC-AUTH-019",
    title:
      "Generated Agent Prompt Optimiser routes reject anonymous requests before run, graph, or trial work",
    tags: ["public", "auth", "guard", "simulation", "agent-prompt-optimiser"],
    public: true,
    async run({ apiBase, evidence }) {
      const runId = "00000000-0000-4000-8000-000000001020";
      const trialId = "00000000-0000-4000-8000-000000001021";
      const endpoints = [
        {
          label: "GET /simulate/api/agent-prompt-optimiser/",
          method: "GET",
          path: apiPath("/simulate/api/agent-prompt-optimiser/"),
        },
        {
          label: "POST /simulate/api/agent-prompt-optimiser/",
          method: "POST",
          path: apiPath("/simulate/api/agent-prompt-optimiser/"),
          body: {},
        },
        {
          label: "GET /simulate/api/agent-prompt-optimiser/{id}/",
          method: "GET",
          path: apiPath("/simulate/api/agent-prompt-optimiser/{id}/", {
            id: runId,
          }),
        },
        {
          label: "PUT /simulate/api/agent-prompt-optimiser/{id}/",
          method: "PUT",
          path: apiPath("/simulate/api/agent-prompt-optimiser/{id}/", {
            id: runId,
          }),
          body: {},
        },
        {
          label: "PATCH /simulate/api/agent-prompt-optimiser/{id}/",
          method: "PATCH",
          path: apiPath("/simulate/api/agent-prompt-optimiser/{id}/", {
            id: runId,
          }),
          body: {},
        },
        {
          label: "DELETE /simulate/api/agent-prompt-optimiser/{id}/",
          method: "DELETE",
          path: apiPath("/simulate/api/agent-prompt-optimiser/{id}/", {
            id: runId,
          }),
        },
        {
          label: "GET /simulate/api/agent-prompt-optimiser/{id}/graph/",
          method: "GET",
          path: apiPath("/simulate/api/agent-prompt-optimiser/{id}/graph/", {
            id: runId,
          }),
        },
        {
          label: "GET /simulate/api/agent-prompt-optimiser/{id}/steps/",
          method: "GET",
          path: apiPath("/simulate/api/agent-prompt-optimiser/{id}/steps/", {
            id: runId,
          }),
        },
        {
          label:
            "GET /simulate/api/agent-prompt-optimiser/{id}/trial/{trial_id}/evaluations/",
          method: "GET",
          path: apiPath(
            "/simulate/api/agent-prompt-optimiser/{id}/trial/{trial_id}/evaluations/",
            { id: runId, trial_id: trialId },
          ),
        },
        {
          label:
            "GET /simulate/api/agent-prompt-optimiser/{id}/trial/{trial_id}/prompt/",
          method: "GET",
          path: apiPath(
            "/simulate/api/agent-prompt-optimiser/{id}/trial/{trial_id}/prompt/",
            { id: runId, trial_id: trialId },
          ),
        },
        {
          label:
            "GET /simulate/api/agent-prompt-optimiser/{id}/trial/{trial_id}/scenarios/",
          method: "GET",
          path: apiPath(
            "/simulate/api/agent-prompt-optimiser/{id}/trial/{trial_id}/scenarios/",
            { id: runId, trial_id: trialId },
          ),
        },
      ];

      for (const endpoint of endpoints) {
        const result = await request(
          apiBase,
          endpoint.method,
          endpoint.path,
          endpoint.body,
        );
        assertJsonAuthGuard(result, endpoint.label);
        evidence.push({
          label: endpoint.label,
          method: endpoint.method,
          path: endpoint.path,
          status: result.status,
          code: result.body?.code || null,
        });
      }
    },
  },
  {
    id: "PUBLIC-AUTH-020",
    title:
      "Generated Persona routes reject anonymous requests before catalog, duplicate, or mutation work",
    tags: ["public", "auth", "guard", "simulation", "personas"],
    public: true,
    async run({ apiBase, evidence }) {
      const personaId = "00000000-0000-4000-8000-000000001022";
      const endpoints = [
        {
          label: "GET /simulate/api/personas/",
          method: "GET",
          path: apiPath("/simulate/api/personas/"),
        },
        {
          label: "POST /simulate/api/personas/",
          method: "POST",
          path: apiPath("/simulate/api/personas/"),
          body: {},
        },
        {
          label: "POST /simulate/api/personas/duplicate/{persona_id}/",
          method: "POST",
          path: apiPath("/simulate/api/personas/duplicate/{persona_id}/", {
            persona_id: personaId,
          }),
          body: {},
        },
        {
          label: "GET /simulate/api/personas/field-options/",
          method: "GET",
          path: apiPath("/simulate/api/personas/field-options/"),
        },
        {
          label: "GET /simulate/api/personas/system/",
          method: "GET",
          path: apiPath("/simulate/api/personas/system/"),
        },
        {
          label: "GET /simulate/api/personas/workspace/",
          method: "GET",
          path: apiPath("/simulate/api/personas/workspace/"),
        },
        {
          label: "GET /simulate/api/personas/{id}/",
          method: "GET",
          path: apiPath("/simulate/api/personas/{id}/", { id: personaId }),
        },
        {
          label: "PUT /simulate/api/personas/{id}/",
          method: "PUT",
          path: apiPath("/simulate/api/personas/{id}/", { id: personaId }),
          body: {},
        },
        {
          label: "PATCH /simulate/api/personas/{id}/",
          method: "PATCH",
          path: apiPath("/simulate/api/personas/{id}/", { id: personaId }),
          body: {},
        },
        {
          label: "DELETE /simulate/api/personas/{id}/",
          method: "DELETE",
          path: apiPath("/simulate/api/personas/{id}/", { id: personaId }),
        },
        {
          label: "POST /simulate/api/personas/{id}/duplicate/",
          method: "POST",
          path: apiPath("/simulate/api/personas/{id}/duplicate/", {
            id: personaId,
          }),
          body: {},
        },
      ];

      for (const endpoint of endpoints) {
        const result = await request(
          apiBase,
          endpoint.method,
          endpoint.path,
          endpoint.body,
        );
        assertJsonAuthGuard(result, endpoint.label);
        evidence.push({
          label: endpoint.label,
          method: endpoint.method,
          path: endpoint.path,
          status: result.status,
          code: result.body?.code || null,
        });
      }
    },
  },
  {
    id: "PUBLIC-AUTH-021",
    title:
      "Generated LiveKit routes reject anonymous and unsigned requests before call or credential work",
    tags: ["public", "auth", "guard", "simulation", "livekit"],
    public: true,
    async run({ apiBase, evidence }) {
      const callId = "00000000-0000-4000-8000-000000001023";
      const phoneNumber = "+15551234567";
      const internalEndpoints = [
        {
          label: "GET /simulate/api/livekit/call-config/{call_id}/",
          method: "GET",
          path: apiPath("/simulate/api/livekit/call-config/{call_id}/", {
            call_id: callId,
          }),
        },
        {
          label: "PATCH /simulate/api/livekit/call-execution/{call_id}/",
          method: "PATCH",
          path: apiPath("/simulate/api/livekit/call-execution/{call_id}/", {
            call_id: callId,
          }),
          body: {},
        },
        {
          label: "GET /simulate/api/livekit/phone-resolution/{phone_number}/",
          method: "GET",
          path: apiPath(
            "/simulate/api/livekit/phone-resolution/{phone_number}/",
            { phone_number: phoneNumber },
          ),
        },
        {
          label: "POST /simulate/api/livekit/temporal-signal/",
          method: "POST",
          path: apiPath("/simulate/api/livekit/temporal-signal/"),
          body: {},
        },
        {
          label: "POST /simulate/api/livekit/transcripts/{call_id}/",
          method: "POST",
          path: apiPath("/simulate/api/livekit/transcripts/{call_id}/", {
            call_id: callId,
          }),
          body: {},
        },
      ];

      for (const endpoint of internalEndpoints) {
        const result = await request(
          apiBase,
          endpoint.method,
          endpoint.path,
          endpoint.body,
        );
        assertLiveKitInternalBearerGuard(result, endpoint.label);
        evidence.push({
          label: endpoint.label,
          method: endpoint.method,
          path: endpoint.path,
          status: result.status,
          code: result.body?.code || null,
          auth_boundary: "internal_bearer_secret",
        });
      }

      const listenerToken = await request(
        apiBase,
        "GET",
        apiPath("/simulate/api/livekit/listener-token/{call_id}/", {
          call_id: callId,
        }),
      );
      assertJsonAuthGuard(
        listenerToken,
        "GET /simulate/api/livekit/listener-token/{call_id}/",
      );
      evidence.push({
        label: "GET /simulate/api/livekit/listener-token/{call_id}/",
        method: "GET",
        path: apiPath("/simulate/api/livekit/listener-token/{call_id}/", {
          call_id: callId,
        }),
        status: listenerToken.status,
        code: listenerToken.body?.code || null,
        auth_boundary: "user_session",
      });

      const validateCredentials = await request(
        apiBase,
        "POST",
        apiPath("/simulate/api/livekit/validate-credentials/"),
        {},
      );
      assertJsonAuthGuard(
        validateCredentials,
        "POST /simulate/api/livekit/validate-credentials/",
      );
      evidence.push({
        label: "POST /simulate/api/livekit/validate-credentials/",
        method: "POST",
        path: apiPath("/simulate/api/livekit/validate-credentials/"),
        status: validateCredentials.status,
        code: validateCredentials.body?.code || null,
        auth_boundary: "user_session",
      });

      const webhook = await request(
        apiBase,
        "POST",
        apiPath("/simulate/api/livekit/webhook/"),
        {},
      );
      assertLiveKitWebhookGuard(webhook, "POST /simulate/api/livekit/webhook/");
      evidence.push({
        label: "POST /simulate/api/livekit/webhook/",
        method: "POST",
        path: apiPath("/simulate/api/livekit/webhook/"),
        status: webhook.status,
        code: webhook.body?.code || null,
        auth_boundary: "livekit_signed_webhook",
      });
    },
  },
  {
    id: "PUBLIC-AUTH-022",
    title:
      "Generated prompt simulation and export routes reject anonymous requests before prompt, run, or CSV work",
    tags: ["public", "auth", "guard", "simulation", "prompt-simulation"],
    public: true,
    async run({ apiBase, evidence }) {
      const promptTemplateId = "00000000-0000-4000-8000-000000001024";
      const runTestId = "00000000-0000-4000-8000-000000001025";
      const endpoints = [
        {
          label: "GET /simulate/prompt-simulations/scenarios/",
          method: "GET",
          path: apiPath("/simulate/prompt-simulations/scenarios/"),
        },
        {
          label:
            "GET /simulate/prompt-templates/{prompt_template_id}/simulations/",
          method: "GET",
          path: apiPath(
            "/simulate/prompt-templates/{prompt_template_id}/simulations/",
            { prompt_template_id: promptTemplateId },
          ),
        },
        {
          label:
            "POST /simulate/prompt-templates/{prompt_template_id}/simulations/",
          method: "POST",
          path: apiPath(
            "/simulate/prompt-templates/{prompt_template_id}/simulations/",
            { prompt_template_id: promptTemplateId },
          ),
          body: {},
        },
        {
          label:
            "GET /simulate/prompt-templates/{prompt_template_id}/simulations/{run_test_id}/",
          method: "GET",
          path: apiPath(
            "/simulate/prompt-templates/{prompt_template_id}/simulations/{run_test_id}/",
            { prompt_template_id: promptTemplateId, run_test_id: runTestId },
          ),
        },
        {
          label:
            "PATCH /simulate/prompt-templates/{prompt_template_id}/simulations/{run_test_id}/",
          method: "PATCH",
          path: apiPath(
            "/simulate/prompt-templates/{prompt_template_id}/simulations/{run_test_id}/",
            { prompt_template_id: promptTemplateId, run_test_id: runTestId },
          ),
          body: {},
        },
        {
          label:
            "DELETE /simulate/prompt-templates/{prompt_template_id}/simulations/{run_test_id}/",
          method: "DELETE",
          path: apiPath(
            "/simulate/prompt-templates/{prompt_template_id}/simulations/{run_test_id}/",
            { prompt_template_id: promptTemplateId, run_test_id: runTestId },
          ),
        },
        {
          label:
            "POST /simulate/prompt-templates/{prompt_template_id}/simulations/{run_test_id}/execute/",
          method: "POST",
          path: apiPath(
            "/simulate/prompt-templates/{prompt_template_id}/simulations/{run_test_id}/execute/",
            { prompt_template_id: promptTemplateId, run_test_id: runTestId },
          ),
          body: {},
        },
        {
          label: "GET /simulate/export/{item_id}/?type=runtest",
          method: "GET",
          path: `${apiPath("/simulate/export/{item_id}/", {
            item_id: runTestId,
          })}?type=runtest`,
        },
      ];

      for (const endpoint of endpoints) {
        const result = await request(
          apiBase,
          endpoint.method,
          endpoint.path,
          endpoint.body,
        );
        assertJsonAuthGuard(result, endpoint.label);
        evidence.push({
          label: endpoint.label,
          method: endpoint.method,
          path: endpoint.path,
          status: result.status,
          code: result.body?.code || null,
        });
      }
    },
  },
  {
    id: "PUBLIC-AUTH-023",
    title:
      "Generated usage v2 billing routes reject anonymous requests before billing, payment, or usage work",
    tags: ["public", "auth", "guard", "usage", "billing"],
    public: true,
    async run({ apiBase, evidence }) {
      const paymentMethodId = "pm_api_journey_missing";
      const authGuardEndpoints = [
        {
          label: "POST /usage/v2/add-addon/",
          method: "POST",
          path: apiPath("/usage/v2/add-addon/"),
          body: {},
        },
        {
          label: "PUT /usage/v2/add-addon/",
          method: "PUT",
          path: apiPath("/usage/v2/add-addon/"),
          body: {},
        },
        {
          label: "DELETE /usage/v2/add-addon/",
          method: "DELETE",
          path: apiPath("/usage/v2/add-addon/"),
        },
        {
          label: "POST /usage/v2/addon/",
          method: "POST",
          path: apiPath("/usage/v2/addon/"),
          body: {},
        },
        {
          label: "PUT /usage/v2/addon/",
          method: "PUT",
          path: apiPath("/usage/v2/addon/"),
          body: {},
        },
        {
          label: "DELETE /usage/v2/addon/",
          method: "DELETE",
          path: apiPath("/usage/v2/addon/"),
        },
        {
          label: "POST /usage/v2/downgrade-to-free/",
          method: "POST",
          path: apiPath("/usage/v2/downgrade-to-free/"),
          body: {},
        },
        {
          label: "POST /usage/v2/payment-methods/",
          method: "POST",
          path: apiPath("/usage/v2/payment-methods/"),
          body: {},
        },
        {
          label: "PUT /usage/v2/payment-methods/",
          method: "PUT",
          path: apiPath("/usage/v2/payment-methods/"),
          body: {},
        },
        {
          label: "GET /usage/v2/payment-methods/setup-intent/",
          method: "GET",
          path: apiPath("/usage/v2/payment-methods/setup-intent/"),
        },
        {
          label: "PUT /usage/v2/payment-methods/setup-intent/",
          method: "PUT",
          path: apiPath("/usage/v2/payment-methods/setup-intent/"),
          body: {},
        },
        {
          label: "POST /usage/v2/payment-methods/{pm_id}/",
          method: "POST",
          path: apiPath("/usage/v2/payment-methods/{pm_id}/", {
            pm_id: paymentMethodId,
          }),
          body: {},
        },
        {
          label: "DELETE /usage/v2/payment-methods/{pm_id}/default/",
          method: "DELETE",
          path: apiPath("/usage/v2/payment-methods/{pm_id}/default/", {
            pm_id: paymentMethodId,
          }),
        },
        {
          label: "POST /usage/v2/reinstate-addon/",
          method: "POST",
          path: apiPath("/usage/v2/reinstate-addon/"),
          body: {},
        },
        {
          label: "PUT /usage/v2/reinstate-addon/",
          method: "PUT",
          path: apiPath("/usage/v2/reinstate-addon/"),
          body: {},
        },
        {
          label: "DELETE /usage/v2/reinstate-addon/",
          method: "DELETE",
          path: apiPath("/usage/v2/reinstate-addon/"),
        },
        {
          label: "POST /usage/v2/remove-addon/",
          method: "POST",
          path: apiPath("/usage/v2/remove-addon/"),
          body: {},
        },
        {
          label: "PUT /usage/v2/remove-addon/",
          method: "PUT",
          path: apiPath("/usage/v2/remove-addon/"),
          body: {},
        },
        {
          label: "DELETE /usage/v2/remove-addon/",
          method: "DELETE",
          path: apiPath("/usage/v2/remove-addon/"),
        },
        {
          label: "POST /usage/v2/upgrade-to-payg/",
          method: "POST",
          path: apiPath("/usage/v2/upgrade-to-payg/"),
          body: {},
        },
        {
          label: "PUT /usage/v2/upgrade-to-payg/",
          method: "PUT",
          path: apiPath("/usage/v2/upgrade-to-payg/"),
          body: {},
        },
        {
          label: "GET /usage/v2/usage-overview/",
          method: "GET",
          path: apiPath("/usage/v2/usage-overview/"),
        },
        {
          label: "GET /usage/v2/usage-time-series/",
          method: "GET",
          path: apiPath("/usage/v2/usage-time-series/"),
        },
        {
          label: "GET /usage/v2/usage-workspace-breakdown/",
          method: "GET",
          path: apiPath("/usage/v2/usage-workspace-breakdown/"),
        },
      ];

      for (const endpoint of authGuardEndpoints) {
        const result = await request(
          apiBase,
          endpoint.method,
          endpoint.path,
          endpoint.body,
        );
        assertJsonAuthGuard(result, endpoint.label);
        evidence.push({
          label: endpoint.label,
          method: endpoint.method,
          path: endpoint.path,
          status: result.status,
          code: result.body?.code || null,
          auth_boundary: "user_session",
        });
      }

      const stripeWebhook = await request(
        apiBase,
        "POST",
        apiPath("/usage/v2/stripe-webhook/"),
        {},
      );
      assertStatus(stripeWebhook, 400, "POST /usage/v2/stripe-webhook/");
      assertJsonErrorCaseInsensitive(
        stripeWebhook,
        "Stripe-Signature",
        "POST /usage/v2/stripe-webhook/",
      );
      assertNoHtml500(stripeWebhook, "POST /usage/v2/stripe-webhook/");
      assertNoSensitiveTokens(
        stripeWebhook.body,
        "POST /usage/v2/stripe-webhook/",
      );
      evidence.push({
        label: "POST /usage/v2/stripe-webhook/",
        method: "POST",
        path: apiPath("/usage/v2/stripe-webhook/"),
        status: stripeWebhook.status,
        code: stripeWebhook.body?.code || null,
        auth_boundary: "stripe_signature",
      });
    },
  },
  {
    id: "PUBLIC-AUTH-024",
    title:
      "Generated usage admin routes reject anonymous requests before API-key or superuser billing work",
    tags: ["public", "auth", "guard", "usage", "admin", "billing"],
    public: true,
    async run({ apiBase, evidence }) {
      const usageAdminId = 100000001;
      const adminEndpoints = [
        {
          label: "GET /usage/admin/custom-plan/",
          method: "GET",
          path: apiPath("/usage/admin/custom-plan/"),
        },
        {
          label: "POST /usage/admin/custom-plan/",
          method: "POST",
          path: apiPath("/usage/admin/custom-plan/"),
          body: {},
        },
        {
          label: "PUT /usage/admin/custom-plan/",
          method: "PUT",
          path: apiPath("/usage/admin/custom-plan/"),
          body: {},
        },
        {
          label: "GET /usage/admin/entitlements/",
          method: "GET",
          path: apiPath("/usage/admin/entitlements/"),
        },
        {
          label: "POST /usage/admin/entitlements/",
          method: "POST",
          path: apiPath("/usage/admin/entitlements/"),
          body: {},
        },
        {
          label: "DELETE /usage/admin/entitlements/",
          method: "DELETE",
          path: apiPath("/usage/admin/entitlements/"),
        },
        {
          label: "POST /usage/admin/invoice/generate/",
          method: "POST",
          path: apiPath("/usage/admin/invoice/generate/"),
          body: {},
        },
        {
          label: "POST /usage/admin/invoice/preview/",
          method: "POST",
          path: apiPath("/usage/admin/invoice/preview/"),
          body: {},
        },
        {
          label: "GET /usage/admin/pricing/",
          method: "GET",
          path: apiPath("/usage/admin/pricing/"),
        },
        {
          label: "POST /usage/admin/pricing/",
          method: "POST",
          path: apiPath("/usage/admin/pricing/"),
          body: {},
        },
        {
          label: "DELETE /usage/admin/pricing/",
          method: "DELETE",
          path: apiPath("/usage/admin/pricing/"),
        },
      ];
      const appsmithResources = [
        {
          collectionTemplate: "/usage/subscription-tier/",
          detailTemplate: "/usage/subscription-tier/{subscription_id}/",
          params: { subscription_id: usageAdminId },
          methods: ["GET", "POST", "PATCH", "DELETE"],
        },
        {
          collectionTemplate: "/usage/organization-billing/",
          detailTemplate: "/usage/organization-billing/{billing_id}/",
          params: { billing_id: usageAdminId },
          methods: ["GET", "PATCH"],
        },
        {
          collectionTemplate: "/usage/organization-subscription/",
          detailTemplate:
            "/usage/organization-subscription/{organization_subscription_id}/",
          params: { organization_subscription_id: usageAdminId },
          methods: ["GET", "POST", "PATCH", "DELETE"],
        },
        {
          collectionTemplate: "/usage/pricing/",
          detailTemplate: "/usage/pricing/{pricing_id}/",
          params: { pricing_id: usageAdminId },
          methods: ["GET", "POST", "PATCH", "DELETE"],
        },
        {
          collectionTemplate: "/usage/rate-limits/",
          detailTemplate: "/usage/rate-limits/{rate_limit_id}/",
          params: { rate_limit_id: usageAdminId },
          methods: ["GET", "POST", "PATCH", "DELETE"],
        },
        {
          collectionTemplate: "/usage/resource-limits/",
          detailTemplate: "/usage/resource-limits/{resource_limit_id}/",
          params: { resource_limit_id: usageAdminId },
          methods: ["GET", "POST", "PATCH", "DELETE"],
        },
      ];
      const appsmithEndpoints = appsmithResources.flatMap((resource) =>
        [resource.collectionTemplate, resource.detailTemplate].flatMap(
          (template) =>
            resource.methods.map((method) => ({
              label: `${method} ${template}`,
              method,
              path: apiPath(template, resource.params),
              body: ["POST", "PUT", "PATCH"].includes(method) ? {} : undefined,
            })),
        ),
      );

      for (const endpoint of [...adminEndpoints, ...appsmithEndpoints]) {
        const result = await request(
          apiBase,
          endpoint.method,
          endpoint.path,
          endpoint.body,
        );
        assertApiKeyOrPermissionGuard(result, endpoint.label);
        evidence.push({
          label: endpoint.label,
          method: endpoint.method,
          path: endpoint.path,
          status: result.status,
          code: result.body?.code || null,
          auth_boundary: "api_key_or_superuser",
        });
      }
    },
  },
  {
    id: "PUBLIC-AUTH-025",
    title:
      "Generated legacy usage routes reject anonymous or unsigned requests before billing work",
    tags: ["public", "auth", "guard", "usage", "billing", "legacy"],
    public: true,
    async run({ apiBase, evidence }) {
      const authGuardEndpoints = [
        {
          label: "GET /usage/api-call-count/",
          method: "GET",
          path: apiPath("/usage/api-call-count/"),
        },
        {
          label: "POST /usage/cancel-subscription/",
          method: "POST",
          path: apiPath("/usage/cancel-subscription/"),
          body: {},
        },
        {
          label: "POST /usage/create-auto-recharge-session/",
          method: "POST",
          path: apiPath("/usage/create-auto-recharge-session/"),
          body: {},
        },
        {
          label: "POST /usage/create-custom-payment-checkout-session/",
          method: "POST",
          path: apiPath("/usage/create-custom-payment-checkout-session/"),
          body: {},
        },
        {
          label: "POST /usage/download-invoice/",
          method: "POST",
          path: apiPath("/usage/download-invoice/"),
          body: {},
        },
        {
          label: "GET /usage/get-auto-reload-settings/",
          method: "GET",
          path: apiPath("/usage/get-auto-reload-settings/"),
        },
        {
          label: "GET /usage/get-customer-invoices/",
          method: "GET",
          path: apiPath("/usage/get-customer-invoices/"),
        },
        {
          label: "GET /usage/get-last-four-digits/",
          method: "GET",
          path: apiPath("/usage/get-last-four-digits/"),
        },
        {
          label: "GET /usage/get-wallet-balance/",
          method: "GET",
          path: apiPath("/usage/get-wallet-balance/"),
        },
        {
          label: "POST /usage/pricing-card-details/",
          method: "POST",
          path: apiPath("/usage/pricing-card-details/"),
          body: {},
        },
        {
          label: "GET /usage/subscription-plans/",
          method: "GET",
          path: apiPath("/usage/subscription-plans/"),
        },
        {
          label: "POST /usage/update-auto-reload-settings/",
          method: "POST",
          path: apiPath("/usage/update-auto-reload-settings/"),
          body: {},
        },
        {
          label: "POST /usage/update-billing-details/",
          method: "POST",
          path: apiPath("/usage/update-billing-details/"),
          body: {},
        },
        {
          label: "GET /usage/usage-summary/",
          method: "GET",
          path: apiPath("/usage/usage-summary/"),
        },
      ];
      const apiKeyEndpoints = [
        {
          label: "GET /usage/api-call-type/",
          method: "GET",
          path: apiPath("/usage/api-call-type/"),
        },
        {
          label: "GET /usage/organization-filter/",
          method: "GET",
          path: apiPath("/usage/organization-filter/"),
        },
        {
          label: "GET /usage/organizations/",
          method: "GET",
          path: apiPath("/usage/organizations/"),
        },
        {
          label: "GET /usage/resource-type/",
          method: "GET",
          path: apiPath("/usage/resource-type/"),
        },
      ];

      for (const endpoint of authGuardEndpoints) {
        const result = await request(
          apiBase,
          endpoint.method,
          endpoint.path,
          endpoint.body,
        );
        assertJsonAuthGuard(result, endpoint.label);
        evidence.push({
          label: endpoint.label,
          method: endpoint.method,
          path: endpoint.path,
          status: result.status,
          code: result.body?.code || null,
          auth_boundary: "user_session",
        });
      }

      for (const endpoint of apiKeyEndpoints) {
        const result = await request(
          apiBase,
          endpoint.method,
          endpoint.path,
          endpoint.body,
        );
        assertApiKeyOrPermissionGuard(result, endpoint.label);
        evidence.push({
          label: endpoint.label,
          method: endpoint.method,
          path: endpoint.path,
          status: result.status,
          code: result.body?.code || null,
          auth_boundary: "api_key",
        });
      }

      const pricing = await request(
        apiBase,
        "GET",
        apiPath("/usage/get_latest_prices/"),
      );
      assertUsageLatestPricesBoundary(pricing, "GET /usage/get_latest_prices/");
      evidence.push({
        label: "GET /usage/get_latest_prices/",
        method: "GET",
        path: apiPath("/usage/get_latest_prices/"),
        status: pricing.status,
        code: pricing.body?.code || null,
        auth_boundary: "public_pricing_read",
        result_status: pricing.body?.status ?? null,
        result_key_count:
          pricing.status === 200 && pricing.body?.result
            ? Object.keys(pricing.body.result).length
            : 0,
        error_detail:
          pricing.status === 400 ? pricing.body?.detail || null : null,
      });

      const webhook = await request(
        apiBase,
        "POST",
        apiPath("/usage/webhook/"),
        {},
      );
      assertStatus(webhook, 400, "POST /usage/webhook/");
      assertJsonErrorCaseInsensitive(
        webhook,
        "Stripe-Signature",
        "POST /usage/webhook/",
      );
      assertNoHtml500(webhook, "POST /usage/webhook/");
      assertNoSensitiveTokens(webhook.body, "POST /usage/webhook/");
      evidence.push({
        label: "POST /usage/webhook/",
        method: "POST",
        path: apiPath("/usage/webhook/"),
        status: webhook.status,
        code: webhook.body?.code || null,
        auth_boundary: "stripe_signature",
      });
    },
  },
  {
    id: "PUBLIC-AUTH-026",
    title:
      "Generated model-hub AI, custom model, metric, and embedding routes reject anonymous requests before provider work",
    tags: [
      "public",
      "auth",
      "guard",
      "model-hub",
      "custom-models",
      "custom-metrics",
      "embeddings",
    ],
    public: true,
    async run({ apiBase, evidence }) {
      const guardUuid = "00000000-0000-0000-0000-000000000001";
      const endpoints = [
        {
          label: "POST /model-hub/ai-eval-writer/",
          method: "POST",
          path: apiPath("/model-hub/ai-eval-writer/"),
          body: {},
        },
        {
          label: "POST /model-hub/ai-filter/",
          method: "POST",
          path: apiPath("/model-hub/ai-filter/"),
          body: {},
        },
        {
          label: "GET /model-hub/api/model_voices/",
          method: "GET",
          path: apiPath("/model-hub/api/model_voices/"),
        },
        {
          label: "GET /model-hub/cells/{cell_id}/run-error-localizer/",
          method: "GET",
          path: apiPath("/model-hub/cells/{cell_id}/run-error-localizer/", {
            cell_id: guardUuid,
          }),
        },
        {
          label: "POST /model-hub/cells/{cell_id}/run-error-localizer/",
          method: "POST",
          path: apiPath("/model-hub/cells/{cell_id}/run-error-localizer/", {
            cell_id: guardUuid,
          }),
          body: {},
        },
        {
          label: "GET /model-hub/column-config/{column_id}/",
          method: "GET",
          path: apiPath("/model-hub/column-config/{column_id}/", {
            column_id: guardUuid,
          }),
        },
        {
          label: "GET /model-hub/custom-metric/all/{model_id}/",
          method: "GET",
          path: apiPath("/model-hub/custom-metric/all/{model_id}/", {
            model_id: guardUuid,
          }),
        },
        {
          label: "POST /model-hub/custom-metric/create/",
          method: "POST",
          path: apiPath("/model-hub/custom-metric/create/"),
          body: {},
        },
        {
          label: "GET /model-hub/custom-metric/tag-options/{metric_id}/",
          method: "GET",
          path: apiPath("/model-hub/custom-metric/tag-options/{metric_id}/", {
            metric_id: guardUuid,
          }),
        },
        {
          label: "POST /model-hub/custom-metric/test/",
          method: "POST",
          path: apiPath("/model-hub/custom-metric/test/"),
          body: {},
        },
        {
          label: "POST /model-hub/custom-metric/update/",
          method: "POST",
          path: apiPath("/model-hub/custom-metric/update/"),
          body: {},
        },
        {
          label: "GET /model-hub/custom-metric/{model_id}/",
          method: "GET",
          path: apiPath("/model-hub/custom-metric/{model_id}/", {
            model_id: guardUuid,
          }),
        },
        {
          label: "GET /model-hub/custom-models/list/",
          method: "GET",
          path: apiPath("/model-hub/custom-models/list/"),
        },
        {
          label: "GET /model-hub/custom-models/{id}/",
          method: "GET",
          path: apiPath("/model-hub/custom-models/{id}/", { id: guardUuid }),
        },
        {
          label: "POST /model-hub/custom-models/{id}/",
          method: "POST",
          path: apiPath("/model-hub/custom-models/{id}/", { id: guardUuid }),
          body: {},
        },
        {
          label: "GET /model-hub/custom_models/edit/",
          method: "GET",
          path: apiPath("/model-hub/custom_models/edit/"),
        },
        {
          label: "POST /model-hub/custom_models/update-baseline/{id}/",
          method: "POST",
          path: apiPath("/model-hub/custom_models/update-baseline/{id}/", {
            id: guardUuid,
          }),
          body: {},
        },
        {
          label: "POST /model-hub/custom_models/update-metric/{id}/",
          method: "POST",
          path: apiPath("/model-hub/custom_models/update-metric/{id}/", {
            id: guardUuid,
          }),
          body: {},
        },
        {
          label: "GET /model-hub/embeddings/",
          method: "GET",
          path: apiPath("/model-hub/embeddings/"),
        },
        {
          label: "GET /model-hub/embeddings/{type}/",
          method: "GET",
          path: apiPath("/model-hub/embeddings/{type}/", { type: "openai" }),
        },
      ];

      for (const endpoint of endpoints) {
        const result = await request(
          apiBase,
          endpoint.method,
          endpoint.path,
          endpoint.body,
        );
        assertJsonAuthGuard(result, endpoint.label);
        evidence.push({
          label: endpoint.label,
          method: endpoint.method,
          path: endpoint.path,
          status: result.status,
          code: result.body?.code || null,
          auth_boundary: "user_session",
        });
      }
    },
  },
  {
    id: "PUBLIC-AUTH-027",
    title:
      "Generated model-hub prompt workbench, metrics, and response-schema routes reject anonymous requests before prompt work",
    tags: [
      "public",
      "auth",
      "guard",
      "model-hub",
      "prompt-workbench",
      "prompt-metrics",
      "response-schema",
    ],
    public: true,
    async run({ apiBase, evidence }) {
      const guardUuid = "00000000-0000-0000-0000-000000000001";
      const endpoints = [
        {
          label: "POST /model-hub/prompt-templates/",
          method: "POST",
          path: apiPath("/model-hub/prompt-templates/"),
          body: {},
        },
        {
          label: "POST /model-hub/prompt-templates/analyze-prompt/",
          method: "POST",
          path: apiPath("/model-hub/prompt-templates/analyze-prompt/"),
          body: {},
        },
        {
          label: "POST /model-hub/prompt-templates/derived-variables/preview/",
          method: "POST",
          path: apiPath(
            "/model-hub/prompt-templates/derived-variables/preview/",
          ),
          body: {},
        },
        {
          label: "POST /model-hub/prompt-templates/generate-prompt/",
          method: "POST",
          path: apiPath("/model-hub/prompt-templates/generate-prompt/"),
          body: {},
        },
        {
          label: "POST /model-hub/prompt-templates/generate-variables/",
          method: "POST",
          path: apiPath("/model-hub/prompt-templates/generate-variables/"),
          body: {},
        },
        {
          label: "POST /model-hub/prompt-templates/improve-prompt/",
          method: "POST",
          path: apiPath("/model-hub/prompt-templates/improve-prompt/"),
          body: {},
        },
        {
          label: "PUT /model-hub/prompt-templates/{id}/",
          method: "PUT",
          path: apiPath("/model-hub/prompt-templates/{id}/", {
            id: guardUuid,
          }),
          body: {},
        },
        {
          label: "PATCH /model-hub/prompt-templates/{id}/",
          method: "PATCH",
          path: apiPath("/model-hub/prompt-templates/{id}/", {
            id: guardUuid,
          }),
          body: {},
        },
        {
          label: "DELETE /model-hub/prompt-templates/{id}/",
          method: "DELETE",
          path: apiPath("/model-hub/prompt-templates/{id}/", {
            id: guardUuid,
          }),
        },
        {
          label: "GET /model-hub/prompt-templates/{id}/all-variables/",
          method: "GET",
          path: apiPath("/model-hub/prompt-templates/{id}/all-variables/", {
            id: guardUuid,
          }),
        },
        {
          label: "POST /model-hub/prompt-templates/{id}/save-name/",
          method: "POST",
          path: apiPath("/model-hub/prompt-templates/{id}/save-name/", {
            id: guardUuid,
          }),
          body: {},
        },
        {
          label: "POST /model-hub/prompt-templates/{id}/save-prompt-folder/",
          method: "POST",
          path: apiPath(
            "/model-hub/prompt-templates/{id}/save-prompt-folder/",
            { id: guardUuid },
          ),
          body: {},
        },
        {
          label: "GET /model-hub/prompt-templates/{id}/stop-streaming/",
          method: "GET",
          path: apiPath("/model-hub/prompt-templates/{id}/stop-streaming/", {
            id: guardUuid,
          }),
        },
        {
          label:
            "GET /model-hub/prompt-templates/{prompt_id}/derived-variables/",
          method: "GET",
          path: apiPath(
            "/model-hub/prompt-templates/{prompt_id}/derived-variables/",
            { prompt_id: guardUuid },
          ),
        },
        {
          label:
            "POST /model-hub/prompt-templates/{prompt_id}/derived-variables/extract/",
          method: "POST",
          path: apiPath(
            "/model-hub/prompt-templates/{prompt_id}/derived-variables/extract/",
            { prompt_id: guardUuid },
          ),
          body: {},
        },
        {
          label:
            "GET /model-hub/prompt-templates/{prompt_id}/derived-variables/{column_name}/schema/",
          method: "GET",
          path: apiPath(
            "/model-hub/prompt-templates/{prompt_id}/derived-variables/{column_name}/schema/",
            { prompt_id: guardUuid, column_name: "output" },
          ),
        },
        {
          label: "GET /model-hub/prompt/metrics/",
          method: "GET",
          path: apiPath("/model-hub/prompt/metrics/"),
        },
        {
          label: "GET /model-hub/prompt/metrics/empty-screen",
          method: "GET",
          path: apiPath("/model-hub/prompt/metrics/empty-screen"),
        },
        {
          label: "GET /model-hub/prompt/span-metrics/",
          method: "GET",
          path: apiPath("/model-hub/prompt/span-metrics/"),
        },
        {
          label: "GET /model-hub/response_schema/",
          method: "GET",
          path: apiPath("/model-hub/response_schema/"),
        },
        {
          label: "POST /model-hub/response_schema/",
          method: "POST",
          path: apiPath("/model-hub/response_schema/"),
          body: {},
        },
        {
          label: "GET /model-hub/response_schema/{id}/",
          method: "GET",
          path: apiPath("/model-hub/response_schema/{id}/", {
            id: guardUuid,
          }),
        },
        {
          label: "PUT /model-hub/response_schema/{id}/",
          method: "PUT",
          path: apiPath("/model-hub/response_schema/{id}/", {
            id: guardUuid,
          }),
          body: {},
        },
        {
          label: "PATCH /model-hub/response_schema/{id}/",
          method: "PATCH",
          path: apiPath("/model-hub/response_schema/{id}/", {
            id: guardUuid,
          }),
          body: {},
        },
        {
          label: "DELETE /model-hub/response_schema/{id}/",
          method: "DELETE",
          path: apiPath("/model-hub/response_schema/{id}/", {
            id: guardUuid,
          }),
        },
      ];

      for (const endpoint of endpoints) {
        const result = await request(
          apiBase,
          endpoint.method,
          endpoint.path,
          endpoint.body,
        );
        assertJsonAuthGuard(result, endpoint.label);
        evidence.push({
          label: endpoint.label,
          method: endpoint.method,
          path: endpoint.path,
          status: result.status,
          code: result.body?.code || null,
          auth_boundary: "user_session",
        });
      }
    },
  },
  {
    id: "PUBLIC-AUTH-028",
    title:
      "Generated model-hub prompt library and label routes reject anonymous requests before prompt metadata work",
    tags: [
      "public",
      "auth",
      "guard",
      "model-hub",
      "prompt-library",
      "prompt-labels",
      "prompt-history",
    ],
    public: true,
    async run({ apiBase, evidence }) {
      const guardUuid = "00000000-0000-0000-0000-000000000001";
      const endpoints = [
        {
          label: "GET /model-hub/prompt-base-templates/",
          method: "GET",
          path: apiPath("/model-hub/prompt-base-templates/"),
        },
        {
          label: "POST /model-hub/prompt-base-templates/",
          method: "POST",
          path: apiPath("/model-hub/prompt-base-templates/"),
          body: {},
        },
        {
          label: "GET /model-hub/prompt-base-templates/get-all-categories/",
          method: "GET",
          path: apiPath("/model-hub/prompt-base-templates/get-all-categories/"),
        },
        {
          label: "GET /model-hub/prompt-base-templates/{id}/",
          method: "GET",
          path: apiPath("/model-hub/prompt-base-templates/{id}/", {
            id: guardUuid,
          }),
        },
        {
          label: "PUT /model-hub/prompt-base-templates/{id}/",
          method: "PUT",
          path: apiPath("/model-hub/prompt-base-templates/{id}/", {
            id: guardUuid,
          }),
          body: {},
        },
        {
          label: "PATCH /model-hub/prompt-base-templates/{id}/",
          method: "PATCH",
          path: apiPath("/model-hub/prompt-base-templates/{id}/", {
            id: guardUuid,
          }),
          body: {},
        },
        {
          label: "DELETE /model-hub/prompt-base-templates/{id}/",
          method: "DELETE",
          path: apiPath("/model-hub/prompt-base-templates/{id}/", {
            id: guardUuid,
          }),
        },
        {
          label: "GET /model-hub/prompt-executions/{id}/",
          method: "GET",
          path: apiPath("/model-hub/prompt-executions/{id}/", {
            id: guardUuid,
          }),
        },
        {
          label: "PUT /model-hub/prompt-folders/{id}/",
          method: "PUT",
          path: apiPath("/model-hub/prompt-folders/{id}/", {
            id: guardUuid,
          }),
          body: {},
        },
        {
          label: "GET /model-hub/prompt-history-executions/",
          method: "GET",
          path: apiPath("/model-hub/prompt-history-executions/"),
        },
        {
          label:
            "GET /model-hub/prompt-history-executions/execution-details/{execution_id}/",
          method: "GET",
          path: apiPath(
            "/model-hub/prompt-history-executions/execution-details/{execution_id}/",
            { execution_id: guardUuid },
          ),
        },
        {
          label: "GET /model-hub/prompt-history-executions/{id}/",
          method: "GET",
          path: apiPath("/model-hub/prompt-history-executions/{id}/", {
            id: guardUuid,
          }),
        },
        {
          label: "GET /model-hub/prompt-labels/",
          method: "GET",
          path: apiPath("/model-hub/prompt-labels/"),
        },
        {
          label: "POST /model-hub/prompt-labels/",
          method: "POST",
          path: apiPath("/model-hub/prompt-labels/"),
          body: {},
        },
        {
          label: "POST /model-hub/prompt-labels/assign-multiple-labels/",
          method: "POST",
          path: apiPath("/model-hub/prompt-labels/assign-multiple-labels/"),
          body: {},
        },
        {
          label: "POST /model-hub/prompt-labels/create-system-labels/",
          method: "POST",
          path: apiPath("/model-hub/prompt-labels/create-system-labels/"),
          body: {},
        },
        {
          label: "GET /model-hub/prompt-labels/get-by-name/",
          method: "GET",
          path: apiPath("/model-hub/prompt-labels/get-by-name/"),
        },
        {
          label: "POST /model-hub/prompt-labels/remove/",
          method: "POST",
          path: apiPath("/model-hub/prompt-labels/remove/"),
          body: {},
        },
        {
          label: "POST /model-hub/prompt-labels/set-default/",
          method: "POST",
          path: apiPath("/model-hub/prompt-labels/set-default/"),
          body: {},
        },
        {
          label: "GET /model-hub/prompt-labels/template-labels/",
          method: "GET",
          path: apiPath("/model-hub/prompt-labels/template-labels/"),
        },
        {
          label: "GET /model-hub/prompt-labels/{id}/",
          method: "GET",
          path: apiPath("/model-hub/prompt-labels/{id}/", {
            id: guardUuid,
          }),
        },
        {
          label: "PUT /model-hub/prompt-labels/{id}/",
          method: "PUT",
          path: apiPath("/model-hub/prompt-labels/{id}/", {
            id: guardUuid,
          }),
          body: {},
        },
        {
          label: "PATCH /model-hub/prompt-labels/{id}/",
          method: "PATCH",
          path: apiPath("/model-hub/prompt-labels/{id}/", {
            id: guardUuid,
          }),
          body: {},
        },
        {
          label: "DELETE /model-hub/prompt-labels/{id}/",
          method: "DELETE",
          path: apiPath("/model-hub/prompt-labels/{id}/", {
            id: guardUuid,
          }),
        },
        {
          label:
            "POST /model-hub/prompt-labels/{template_id}/{label_id}/assign-label-by-id/",
          method: "POST",
          path: apiPath(
            "/model-hub/prompt-labels/{template_id}/{label_id}/assign-label-by-id/",
            { template_id: guardUuid, label_id: guardUuid },
          ),
          body: {},
        },
      ];

      for (const endpoint of endpoints) {
        const result = await request(
          apiBase,
          endpoint.method,
          endpoint.path,
          endpoint.body,
        );
        assertJsonAuthGuard(result, endpoint.label);
        evidence.push({
          label: endpoint.label,
          method: endpoint.method,
          path: endpoint.path,
          status: result.status,
          code: result.body?.code || null,
          auth_boundary: "user_session",
        });
      }
    },
  },
  {
    id: "PUBLIC-AUTH-029",
    title:
      "Generated model-hub feedback and score routes reject anonymous requests before feedback or annotation work",
    tags: [
      "public",
      "auth",
      "guard",
      "model-hub",
      "feedback",
      "scores",
      "annotations",
    ],
    public: true,
    async run({ apiBase, evidence }) {
      const guardUuid = "00000000-0000-0000-0000-000000000001";
      const endpoints = [
        {
          label: "GET /model-hub/feedback/",
          method: "GET",
          path: apiPath("/model-hub/feedback/"),
        },
        {
          label: "POST /model-hub/feedback/",
          method: "POST",
          path: apiPath("/model-hub/feedback/"),
          body: {},
        },
        {
          label: "GET /model-hub/feedback/get-feedback-details/",
          method: "GET",
          path: apiPath("/model-hub/feedback/get-feedback-details/"),
        },
        {
          label: "GET /model-hub/feedback/get-feedback-summary/",
          method: "GET",
          path: apiPath("/model-hub/feedback/get-feedback-summary/"),
        },
        {
          label: "GET /model-hub/feedback/get_template/",
          method: "GET",
          path: apiPath("/model-hub/feedback/get_template/"),
        },
        {
          label: "POST /model-hub/feedback/submit-feedback/",
          method: "POST",
          path: apiPath("/model-hub/feedback/submit-feedback/"),
          body: {},
        },
        {
          label: "PUT /model-hub/feedback/{id}/",
          method: "PUT",
          path: apiPath("/model-hub/feedback/{id}/", { id: guardUuid }),
          body: {},
        },
        {
          label: "PATCH /model-hub/feedback/{id}/",
          method: "PATCH",
          path: apiPath("/model-hub/feedback/{id}/", { id: guardUuid }),
          body: {},
        },
        {
          label: "GET /model-hub/scores/",
          method: "GET",
          path: apiPath("/model-hub/scores/"),
        },
        {
          label: "POST /model-hub/scores/",
          method: "POST",
          path: apiPath("/model-hub/scores/"),
          body: {},
        },
        {
          label: "GET /model-hub/scores/{id}/",
          method: "GET",
          path: apiPath("/model-hub/scores/{id}/", { id: guardUuid }),
        },
        {
          label: "PUT /model-hub/scores/{id}/",
          method: "PUT",
          path: apiPath("/model-hub/scores/{id}/", { id: guardUuid }),
          body: {},
        },
        {
          label: "PATCH /model-hub/scores/{id}/",
          method: "PATCH",
          path: apiPath("/model-hub/scores/{id}/", { id: guardUuid }),
          body: {},
        },
      ];

      for (const endpoint of endpoints) {
        const result = await request(
          apiBase,
          endpoint.method,
          endpoint.path,
          endpoint.body,
        );
        assertJsonAuthGuard(result, endpoint.label);
        evidence.push({
          label: endpoint.label,
          method: endpoint.method,
          path: endpoint.path,
          status: result.status,
          code: result.body?.code || null,
          auth_boundary: "user_session",
        });
      }
    },
  },
  {
    id: "PUBLIC-AUTH-030",
    title:
      "Generated model-hub provider asset and upload routes reject anonymous requests before credential or file work",
    tags: [
      "public",
      "auth",
      "guard",
      "model-hub",
      "provider-assets",
      "secrets",
      "tools",
      "tts-voices",
      "upload",
    ],
    public: true,
    async run({ apiBase, evidence }) {
      const guardUuid = "00000000-0000-0000-0000-000000000001";
      const endpoints = [
        {
          label: "PUT /model-hub/api-keys/{id}/",
          method: "PUT",
          path: apiPath("/model-hub/api-keys/{id}/", { id: guardUuid }),
          body: {},
        },
        {
          label: "PUT /model-hub/secrets/{id}/",
          method: "PUT",
          path: apiPath("/model-hub/secrets/{id}/", { id: guardUuid }),
          body: {},
        },
        {
          label: "GET /model-hub/tools/{id}/",
          method: "GET",
          path: apiPath("/model-hub/tools/{id}/", { id: guardUuid }),
        },
        {
          label: "PUT /model-hub/tools/{id}/",
          method: "PUT",
          path: apiPath("/model-hub/tools/{id}/", { id: guardUuid }),
          body: {},
        },
        {
          label: "PATCH /model-hub/tools/{id}/",
          method: "PATCH",
          path: apiPath("/model-hub/tools/{id}/", { id: guardUuid }),
          body: {},
        },
        {
          label: "DELETE /model-hub/tools/{id}/",
          method: "DELETE",
          path: apiPath("/model-hub/tools/{id}/", { id: guardUuid }),
        },
        {
          label: "GET /model-hub/tts-voices/{id}/",
          method: "GET",
          path: apiPath("/model-hub/tts-voices/{id}/", { id: guardUuid }),
        },
        {
          label: "PUT /model-hub/tts-voices/{id}/",
          method: "PUT",
          path: apiPath("/model-hub/tts-voices/{id}/", { id: guardUuid }),
          body: {},
        },
        {
          label: "PATCH /model-hub/tts-voices/{id}/",
          method: "PATCH",
          path: apiPath("/model-hub/tts-voices/{id}/", { id: guardUuid }),
          body: {},
        },
        {
          label: "DELETE /model-hub/tts-voices/{id}/",
          method: "DELETE",
          path: apiPath("/model-hub/tts-voices/{id}/", { id: guardUuid }),
        },
        {
          label: "POST /model-hub/upload-file/",
          method: "POST",
          path: apiPath("/model-hub/upload-file/"),
          body: {},
        },
      ];

      for (const endpoint of endpoints) {
        const result = await request(
          apiBase,
          endpoint.method,
          endpoint.path,
          endpoint.body,
        );
        assertJsonAuthGuard(result, endpoint.label);
        evidence.push({
          label: endpoint.label,
          method: endpoint.method,
          path: endpoint.path,
          status: result.status,
          code: result.body?.code || null,
          auth_boundary: "user_session",
        });
      }
    },
  },
  {
    id: "PUBLIC-AUTH-031",
    title:
      "Generated model-hub organization user routes reject anonymous requests before org membership lookup",
    tags: [
      "public",
      "auth",
      "guard",
      "model-hub",
      "organizations",
      "users",
      "annotation-queues",
    ],
    public: true,
    async run({ apiBase, evidence }) {
      const guardUuid = "00000000-0000-0000-0000-000000000001";
      const orgUsersPath = apiPath(
        "/model-hub/organizations/{organization_id}/users/",
        { organization_id: guardUuid },
      );
      const orgUserDetailPath = apiPath(
        "/model-hub/organizations/{organization_id}/users/{id}/",
        { organization_id: guardUuid, id: guardUuid },
      );
      const endpoints = [
        {
          label: "GET /model-hub/organizations/{organization_id}/users/",
          method: "GET",
          path: orgUsersPath,
        },
        {
          label: "POST /model-hub/organizations/{organization_id}/users/",
          method: "POST",
          path: orgUsersPath,
          body: {},
        },
        {
          label: "GET /model-hub/organizations/{organization_id}/users/{id}/",
          method: "GET",
          path: orgUserDetailPath,
        },
        {
          label: "PUT /model-hub/organizations/{organization_id}/users/{id}/",
          method: "PUT",
          path: orgUserDetailPath,
          body: {},
        },
        {
          label: "PATCH /model-hub/organizations/{organization_id}/users/{id}/",
          method: "PATCH",
          path: orgUserDetailPath,
          body: {},
        },
        {
          label:
            "DELETE /model-hub/organizations/{organization_id}/users/{id}/",
          method: "DELETE",
          path: orgUserDetailPath,
        },
      ];

      for (const endpoint of endpoints) {
        const result = await request(
          apiBase,
          endpoint.method,
          endpoint.path,
          endpoint.body,
        );
        assertJsonAuthGuard(result, endpoint.label);
        evidence.push({
          label: endpoint.label,
          method: endpoint.method,
          path: endpoint.path,
          status: result.status,
          code: result.body?.code || null,
          auth_boundary: "user_session",
        });
      }
    },
  },
  {
    id: "PUBLIC-AUTH-032",
    title:
      "Generated annotation queue review and discussion routes reject anonymous requests before queue/item work",
    tags: [
      "public",
      "auth",
      "guard",
      "model-hub",
      "annotation-queues",
      "review",
      "discussion",
    ],
    public: true,
    async run({ apiBase, evidence }) {
      const guardUuid = "00000000-0000-0000-0000-000000000001";
      const endpoints = [
        {
          label:
            "POST /model-hub/annotation-queues/{queue_id}/items/bulk-review/",
          method: "POST",
          path: apiPath(
            "/model-hub/annotation-queues/{queue_id}/items/bulk-review/",
            { queue_id: guardUuid },
          ),
          body: { action: "approve", item_ids: [guardUuid] },
        },
        {
          label:
            "PATCH /model-hub/annotation-queues/{queue_id}/items/{id}/discussion/comments/{comment_id}/",
          method: "PATCH",
          path: apiPath(
            "/model-hub/annotation-queues/{queue_id}/items/{id}/discussion/comments/{comment_id}/",
            { queue_id: guardUuid, id: guardUuid, comment_id: guardUuid },
          ),
          body: { comment: "updated anonymous comment" },
        },
        {
          label:
            "DELETE /model-hub/annotation-queues/{queue_id}/items/{id}/discussion/comments/{comment_id}/",
          method: "DELETE",
          path: apiPath(
            "/model-hub/annotation-queues/{queue_id}/items/{id}/discussion/comments/{comment_id}/",
            { queue_id: guardUuid, id: guardUuid, comment_id: guardUuid },
          ),
        },
      ];

      for (const endpoint of endpoints) {
        const result = await request(
          apiBase,
          endpoint.method,
          endpoint.path,
          endpoint.body,
        );
        assertJsonAuthGuard(result, endpoint.label);
        evidence.push({
          label: endpoint.label,
          method: endpoint.method,
          path: endpoint.path,
          status: result.status,
          code: result.body?.code || null,
          auth_boundary: "user_session",
        });
      }
    },
  },
  {
    id: "PUBLIC-AUTH-033",
    title:
      "Generated model-hub eval, experiment, dataset, and knowledge-base utility routes reject anonymous requests before domain work",
    tags: [
      "public",
      "auth",
      "guard",
      "model-hub",
      "evals",
      "experiments",
      "datasets",
      "knowledge-base",
    ],
    public: true,
    async run({ apiBase, evidence }) {
      const endpoints = [
        {
          label: "POST /model-hub/delete-eval-template/",
          method: "POST",
          path: apiPath("/model-hub/delete-eval-template/"),
          body: {},
        },
        {
          label: "POST /model-hub/duplicate-eval-template/",
          method: "POST",
          path: apiPath("/model-hub/duplicate-eval-template/"),
          body: { name: "anonymous duplicate eval" },
        },
        {
          label: "GET /model-hub/experiment-detail/",
          method: "GET",
          path: apiPath("/model-hub/experiment-detail/"),
        },
        {
          label: "POST /model-hub/get-column-values/",
          method: "POST",
          path: apiPath("/model-hub/get-column-values/"),
          body: {},
        },
        {
          label: "PATCH /model-hub/knowledge-base/",
          method: "PATCH",
          path: apiPath("/model-hub/knowledge-base/"),
          body: {},
        },
        {
          label: "GET /model-hub/metrics/by-column/",
          method: "GET",
          path: apiPath("/model-hub/metrics/by-column/"),
        },
      ];

      for (const endpoint of endpoints) {
        const result = await request(
          apiBase,
          endpoint.method,
          endpoint.path,
          endpoint.body,
        );
        assertJsonAuthGuard(result, endpoint.label);
        evidence.push({
          label: endpoint.label,
          method: endpoint.method,
          path: endpoint.path,
          status: result.status,
          code: result.body?.code || null,
          auth_boundary: "user_session",
        });
      }
    },
  },
  {
    id: "PUBLIC-AUTH-034",
    title:
      "Generated model-hub optimisation routes reject anonymous requests before dataset optimization work",
    tags: ["public", "auth", "guard", "model-hub", "datasets", "optimisation"],
    public: true,
    async run({ apiBase, evidence }) {
      const guardUuid = "00000000-0000-0000-0000-000000000001";
      const endpoints = [
        {
          label: "GET /model-hub/optimisation/",
          method: "GET",
          path: apiPath("/model-hub/optimisation/"),
        },
        {
          label: "POST /model-hub/optimisation/create/",
          method: "POST",
          path: apiPath("/model-hub/optimisation/create/"),
          body: {},
        },
        {
          label: "PUT /model-hub/optimisation/create/",
          method: "PUT",
          path: apiPath("/model-hub/optimisation/create/"),
          body: {},
        },
        {
          label: "POST /model-hub/optimisation/update/{id}/",
          method: "POST",
          path: apiPath("/model-hub/optimisation/update/{id}/", {
            id: guardUuid,
          }),
          body: {},
        },
        {
          label: "PUT /model-hub/optimisation/update/{id}/",
          method: "PUT",
          path: apiPath("/model-hub/optimisation/update/{id}/", {
            id: guardUuid,
          }),
          body: {},
        },
        {
          label: "GET /model-hub/optimisation/{id}/",
          method: "GET",
          path: apiPath("/model-hub/optimisation/{id}/", { id: guardUuid }),
        },
        {
          label: "GET /model-hub/optimisation/{id}/details/",
          method: "GET",
          path: apiPath("/model-hub/optimisation/{id}/details/", {
            id: guardUuid,
          }),
        },
      ];

      for (const endpoint of endpoints) {
        const result = await request(
          apiBase,
          endpoint.method,
          endpoint.path,
          endpoint.body,
        );
        assertJsonAuthGuard(result, endpoint.label);
        evidence.push({
          label: endpoint.label,
          method: endpoint.method,
          path: endpoint.path,
          status: result.status,
          code: result.body?.code || null,
          auth_boundary: "user_session",
        });
      }
    },
  },
  {
    id: "PUBLIC-AUTH-035",
    title:
      "Generated legacy optimize-dataset routes reject anonymous requests before optimizer work",
    tags: [
      "public",
      "auth",
      "guard",
      "model-hub",
      "datasets",
      "optimize-dataset",
    ],
    public: true,
    async run({ apiBase, evidence }) {
      const guardUuid = "00000000-0000-0000-0000-000000000001";
      const pageBody = { page: 1, limit: 10 };
      const columnConfigBody = { columns: [] };
      const endpoints = [
        {
          label: "GET /model-hub/optimize-dataset/",
          method: "GET",
          path: apiPath("/model-hub/optimize-dataset/"),
        },
        {
          label: "GET /model-hub/optimize-dataset/kb/{optim_id}/",
          method: "GET",
          path: apiPath("/model-hub/optimize-dataset/kb/{optim_id}/", {
            optim_id: guardUuid,
          }),
        },
        {
          label: "POST /model-hub/optimize-dataset/knowledge-base/",
          method: "POST",
          path: apiPath("/model-hub/optimize-dataset/knowledge-base/"),
          body: {},
        },
        {
          label: "GET /model-hub/optimize-dataset/{model_id}/",
          method: "GET",
          path: apiPath("/model-hub/optimize-dataset/{model_id}/", {
            model_id: guardUuid,
          }),
        },
        {
          label: "POST /model-hub/optimize-dataset/{model_id}/",
          method: "POST",
          path: apiPath("/model-hub/optimize-dataset/{model_id}/", {
            model_id: guardUuid,
          }),
          body: {},
        },
        {
          label: "GET /model-hub/optimize-dataset/{model_id}/column-config/",
          method: "GET",
          path: apiPath(
            "/model-hub/optimize-dataset/{model_id}/column-config/",
            { model_id: guardUuid },
          ),
        },
        {
          label: "POST /model-hub/optimize-dataset/{model_id}/column-config/",
          method: "POST",
          path: apiPath(
            "/model-hub/optimize-dataset/{model_id}/column-config/",
            { model_id: guardUuid },
          ),
          body: columnConfigBody,
        },
        {
          label:
            "GET /model-hub/optimize-dataset/{model_id}/column-config/prompt-template-explore/{optimization_id}/",
          method: "GET",
          path: apiPath(
            "/model-hub/optimize-dataset/{model_id}/column-config/prompt-template-explore/{optimization_id}/",
            { model_id: guardUuid, optimization_id: guardUuid },
          ),
        },
        {
          label:
            "POST /model-hub/optimize-dataset/{model_id}/column-config/prompt-template-explore/{optimization_id}/",
          method: "POST",
          path: apiPath(
            "/model-hub/optimize-dataset/{model_id}/column-config/prompt-template-explore/{optimization_id}/",
            { model_id: guardUuid, optimization_id: guardUuid },
          ),
          body: columnConfigBody,
        },
        {
          label:
            "GET /model-hub/optimize-dataset/{model_id}/column-config/right-answers/{optimization_id}/",
          method: "GET",
          path: apiPath(
            "/model-hub/optimize-dataset/{model_id}/column-config/right-answers/{optimization_id}/",
            { model_id: guardUuid, optimization_id: guardUuid },
          ),
        },
        {
          label:
            "POST /model-hub/optimize-dataset/{model_id}/column-config/right-answers/{optimization_id}/",
          method: "POST",
          path: apiPath(
            "/model-hub/optimize-dataset/{model_id}/column-config/right-answers/{optimization_id}/",
            { model_id: guardUuid, optimization_id: guardUuid },
          ),
          body: columnConfigBody,
        },
        {
          label:
            "POST /model-hub/optimize-dataset/{model_id}/prompt-template-explore/{optimization_id}/",
          method: "POST",
          path: apiPath(
            "/model-hub/optimize-dataset/{model_id}/prompt-template-explore/{optimization_id}/",
            { model_id: guardUuid, optimization_id: guardUuid },
          ),
          body: pageBody,
        },
        {
          label:
            "POST /model-hub/optimize-dataset/{model_id}/prompt-template-result/{optimization_id}/",
          method: "POST",
          path: apiPath(
            "/model-hub/optimize-dataset/{model_id}/prompt-template-result/{optimization_id}/",
            { model_id: guardUuid, optimization_id: guardUuid },
          ),
          body: {},
        },
        {
          label:
            "POST /model-hub/optimize-dataset/{model_id}/right-answers/{optimization_id}/",
          method: "POST",
          path: apiPath(
            "/model-hub/optimize-dataset/{model_id}/right-answers/{optimization_id}/",
            { model_id: guardUuid, optimization_id: guardUuid },
          ),
          body: pageBody,
        },
        {
          label:
            "GET /model-hub/optimize-dataset/{model_id}/{optimization_id}/",
          method: "GET",
          path: apiPath(
            "/model-hub/optimize-dataset/{model_id}/{optimization_id}/",
            { model_id: guardUuid, optimization_id: guardUuid },
          ),
        },
      ];

      for (const endpoint of endpoints) {
        const result = await request(
          apiBase,
          endpoint.method,
          endpoint.path,
          endpoint.body,
        );
        assertJsonAuthGuard(result, endpoint.label);
        evidence.push({
          label: endpoint.label,
          method: endpoint.method,
          path: endpoint.path,
          status: result.status,
          code: result.body?.code || null,
          auth_boundary: "user_session",
        });
      }
    },
  },
  {
    id: "PUBLIC-AUTH-036",
    title:
      "Generated model-hub performance and eval routes reject anonymous requests before analytics work",
    tags: [
      "public",
      "auth",
      "guard",
      "model-hub",
      "models",
      "performance",
      "evals",
    ],
    public: true,
    async run({ apiBase, evidence }) {
      const guardUuid = "00000000-0000-0000-0000-000000000001";
      const endpoints = [
        {
          label: "GET /model-hub/overview/",
          method: "GET",
          path: apiPath("/model-hub/overview/"),
        },
        {
          label: "POST /model-hub/performance/{id}/",
          method: "POST",
          path: apiPath("/model-hub/performance/{id}/", {
            id: guardUuid,
          }),
          body: {},
        },
        {
          label: "POST /model-hub/performance/detail/{id}/",
          method: "POST",
          path: apiPath("/model-hub/performance/detail/{id}/", {
            id: guardUuid,
          }),
          body: {},
        },
        {
          label: "POST /model-hub/performance/export/{id}/",
          method: "POST",
          path: apiPath("/model-hub/performance/export/{id}/", {
            id: guardUuid,
          }),
          body: {},
        },
        {
          label: "GET /model-hub/performance/options/{model_id}/",
          method: "GET",
          path: apiPath("/model-hub/performance/options/{model_id}/", {
            model_id: guardUuid,
          }),
        },
        {
          label: "GET /model-hub/performance/report/{model_id}/",
          method: "GET",
          path: apiPath("/model-hub/performance/report/{model_id}/", {
            model_id: guardUuid,
          }),
        },
        {
          label: "POST /model-hub/performance/report/{model_id}/",
          method: "POST",
          path: apiPath("/model-hub/performance/report/{model_id}/", {
            model_id: guardUuid,
          }),
          body: {},
        },
        {
          label: "DELETE /model-hub/performance/report/{model_id}/{report_id}/",
          method: "DELETE",
          path: apiPath(
            "/model-hub/performance/report/{model_id}/{report_id}/",
            { model_id: guardUuid, report_id: guardUuid },
          ),
        },
        {
          label: "POST /model-hub/performance/tag-distribution/{model_id}/",
          method: "POST",
          path: apiPath("/model-hub/performance/tag-distribution/{model_id}/", {
            model_id: guardUuid,
          }),
          body: {},
        },
        {
          label: "POST /model-hub/update-eval-template/",
          method: "POST",
          path: apiPath("/model-hub/update-eval-template/"),
          body: {},
        },
      ];

      for (const endpoint of endpoints) {
        const result = await request(
          apiBase,
          endpoint.method,
          endpoint.path,
          endpoint.body,
        );
        assertJsonAuthGuard(result, endpoint.label);
        evidence.push({
          label: endpoint.label,
          method: endpoint.method,
          path: endpoint.path,
          status: result.status,
          code: result.body?.code || null,
          auth_boundary: "user_session",
        });
      }
    },
  },
  {
    id: "PUBLIC-AUTH-037",
    title:
      "Generated tracer saved-view routes reject anonymous requests before saved-view work",
    tags: ["public", "auth", "guard", "tracer", "observe", "saved-views"],
    public: true,
    async run({ apiBase, evidence }) {
      const guardUuid = "00000000-0000-0000-0000-000000000001";
      const endpoints = [
        {
          label: "GET /tracer/saved-views/",
          method: "GET",
          path: apiPath("/tracer/saved-views/"),
        },
        {
          label: "POST /tracer/saved-views/",
          method: "POST",
          path: apiPath("/tracer/saved-views/"),
          body: {},
        },
        {
          label: "POST /tracer/saved-views/reorder/",
          method: "POST",
          path: apiPath("/tracer/saved-views/reorder/"),
          body: {},
        },
        {
          label: "GET /tracer/saved-views/{id}/",
          method: "GET",
          path: apiPath("/tracer/saved-views/{id}/", { id: guardUuid }),
        },
        {
          label: "PUT /tracer/saved-views/{id}/",
          method: "PUT",
          path: apiPath("/tracer/saved-views/{id}/", { id: guardUuid }),
          body: {},
        },
        {
          label: "PATCH /tracer/saved-views/{id}/",
          method: "PATCH",
          path: apiPath("/tracer/saved-views/{id}/", { id: guardUuid }),
          body: {},
        },
        {
          label: "DELETE /tracer/saved-views/{id}/",
          method: "DELETE",
          path: apiPath("/tracer/saved-views/{id}/", { id: guardUuid }),
        },
        {
          label: "POST /tracer/saved-views/{id}/duplicate/",
          method: "POST",
          path: apiPath("/tracer/saved-views/{id}/duplicate/", {
            id: guardUuid,
          }),
          body: {},
        },
      ];

      for (const endpoint of endpoints) {
        const result = await request(
          apiBase,
          endpoint.method,
          endpoint.path,
          endpoint.body,
        );
        assertJsonAuthGuard(result, endpoint.label);
        evidence.push({
          label: endpoint.label,
          method: endpoint.method,
          path: endpoint.path,
          status: result.status,
          code: result.body?.code || null,
          auth_boundary: "user_session",
        });
      }
    },
  },
  {
    id: "PUBLIC-AUTH-038",
    title:
      "Generated tracer shared-link routes reject anonymous requests before share management work",
    tags: ["public", "auth", "guard", "tracer", "observe", "shared-links"],
    public: true,
    async run({ apiBase, evidence }) {
      const guardUuid = "00000000-0000-0000-0000-000000000001";
      const endpoints = [
        {
          label: "GET /tracer/shared-links/",
          method: "GET",
          path: apiPath("/tracer/shared-links/"),
        },
        {
          label: "POST /tracer/shared-links/",
          method: "POST",
          path: apiPath("/tracer/shared-links/"),
          body: {},
        },
        {
          label: "GET /tracer/shared-links/{id}/",
          method: "GET",
          path: apiPath("/tracer/shared-links/{id}/", { id: guardUuid }),
        },
        {
          label: "PUT /tracer/shared-links/{id}/",
          method: "PUT",
          path: apiPath("/tracer/shared-links/{id}/", { id: guardUuid }),
          body: {},
        },
        {
          label: "PATCH /tracer/shared-links/{id}/",
          method: "PATCH",
          path: apiPath("/tracer/shared-links/{id}/", { id: guardUuid }),
          body: {},
        },
        {
          label: "DELETE /tracer/shared-links/{id}/",
          method: "DELETE",
          path: apiPath("/tracer/shared-links/{id}/", { id: guardUuid }),
        },
        {
          label: "POST /tracer/shared-links/{id}/access/",
          method: "POST",
          path: apiPath("/tracer/shared-links/{id}/access/", {
            id: guardUuid,
          }),
          body: {},
        },
        {
          label: "DELETE /tracer/shared-links/{id}/access/{access_id}/",
          method: "DELETE",
          path: apiPath("/tracer/shared-links/{id}/access/{access_id}/", {
            id: guardUuid,
            access_id: guardUuid,
          }),
        },
      ];

      for (const endpoint of endpoints) {
        const result = await request(
          apiBase,
          endpoint.method,
          endpoint.path,
          endpoint.body,
        );
        assertJsonAuthGuard(result, endpoint.label);
        evidence.push({
          label: endpoint.label,
          method: endpoint.method,
          path: endpoint.path,
          status: result.status,
          code: result.body?.code || null,
          auth_boundary: "user_session",
        });
      }
    },
  },
  {
    id: "PUBLIC-AUTH-039",
    title:
      "Generated tracer alert routes reject anonymous requests before monitor and log work",
    tags: ["public", "auth", "guard", "tracer", "observe", "alerts"],
    public: true,
    async run({ apiBase, evidence }) {
      const guardUuid = "00000000-0000-0000-0000-000000000001";
      const endpoints = [
        {
          label: "GET /tracer/user-alerts/",
          method: "GET",
          path: apiPath("/tracer/user-alerts/"),
        },
        {
          label: "GET /tracer/user-alerts/{id}/",
          method: "GET",
          path: apiPath("/tracer/user-alerts/{id}/", { id: guardUuid }),
        },
        {
          label: "PUT /tracer/user-alerts/{id}/",
          method: "PUT",
          path: apiPath("/tracer/user-alerts/{id}/", { id: guardUuid }),
          body: {},
        },
        {
          label: "DELETE /tracer/user-alerts/{id}/",
          method: "DELETE",
          path: apiPath("/tracer/user-alerts/{id}/", { id: guardUuid }),
        },
        {
          label: "GET /tracer/user-alert-logs/",
          method: "GET",
          path: apiPath("/tracer/user-alert-logs/"),
        },
        {
          label: "POST /tracer/user-alert-logs/",
          method: "POST",
          path: apiPath("/tracer/user-alert-logs/"),
          body: {},
        },
        {
          label: "GET /tracer/user-alert-logs/all/",
          method: "GET",
          path: apiPath("/tracer/user-alert-logs/all/"),
        },
        {
          label: "GET /tracer/user-alert-logs/{id}/",
          method: "GET",
          path: apiPath("/tracer/user-alert-logs/{id}/", { id: guardUuid }),
        },
        {
          label: "PUT /tracer/user-alert-logs/{id}/",
          method: "PUT",
          path: apiPath("/tracer/user-alert-logs/{id}/", { id: guardUuid }),
          body: {},
        },
        {
          label: "PATCH /tracer/user-alert-logs/{id}/",
          method: "PATCH",
          path: apiPath("/tracer/user-alert-logs/{id}/", { id: guardUuid }),
          body: {},
        },
        {
          label: "DELETE /tracer/user-alert-logs/{id}/",
          method: "DELETE",
          path: apiPath("/tracer/user-alert-logs/{id}/", {
            id: guardUuid,
          }),
        },
      ];

      for (const endpoint of endpoints) {
        const result = await request(
          apiBase,
          endpoint.method,
          endpoint.path,
          endpoint.body,
        );
        assertJsonAuthGuard(result, endpoint.label);
        evidence.push({
          label: endpoint.label,
          method: endpoint.method,
          path: endpoint.path,
          status: result.status,
          code: result.body?.code || null,
          auth_boundary: "user_session",
        });
      }
    },
  },
  {
    id: "PUBLIC-AUTH-040",
    title:
      "Generated tracer eval-task routes reject anonymous requests before task work",
    tags: ["public", "auth", "guard", "tracer", "observe", "tasks"],
    public: true,
    async run({ apiBase, evidence }) {
      const guardUuid = "00000000-0000-0000-0000-000000000001";
      const endpoints = [
        {
          label: "GET /tracer/eval-task/",
          method: "GET",
          path: apiPath("/tracer/eval-task/"),
        },
        {
          label: "GET /tracer/eval-task/{id}/",
          method: "GET",
          path: apiPath("/tracer/eval-task/{id}/", { id: guardUuid }),
        },
        {
          label: "PUT /tracer/eval-task/{id}/",
          method: "PUT",
          path: apiPath("/tracer/eval-task/{id}/", { id: guardUuid }),
          body: {},
        },
        {
          label: "DELETE /tracer/eval-task/{id}/",
          method: "DELETE",
          path: apiPath("/tracer/eval-task/{id}/", { id: guardUuid }),
        },
      ];

      for (const endpoint of endpoints) {
        const result = await request(
          apiBase,
          endpoint.method,
          endpoint.path,
          endpoint.body,
        );
        assertJsonAuthGuard(result, endpoint.label);
        evidence.push({
          label: endpoint.label,
          method: endpoint.method,
          path: endpoint.path,
          status: result.status,
          code: result.body?.code || null,
          auth_boundary: "user_session",
        });
      }
    },
  },
  {
    id: "PUBLIC-AUTH-041",
    title:
      "Generated tracer custom eval config routes reject anonymous requests before eval config work",
    tags: ["public", "auth", "guard", "tracer", "observe", "evals", "tasks"],
    public: true,
    async run({ apiBase, evidence }) {
      const guardUuid = "00000000-0000-0000-0000-000000000001";
      const endpoints = [
        {
          label: "GET /tracer/custom-eval-config/",
          method: "GET",
          path: apiPath("/tracer/custom-eval-config/"),
        },
        {
          label: "POST /tracer/custom-eval-config/",
          method: "POST",
          path: apiPath("/tracer/custom-eval-config/"),
          body: {},
        },
        {
          label: "POST /tracer/custom-eval-config/check_exists/",
          method: "POST",
          path: apiPath("/tracer/custom-eval-config/check_exists/"),
          body: {},
        },
        {
          label: "POST /tracer/custom-eval-config/get_custom_eval_by_name/",
          method: "POST",
          path: apiPath("/tracer/custom-eval-config/get_custom_eval_by_name/"),
          body: {},
        },
        {
          label: "GET /tracer/custom-eval-config/list_custom_eval_configs/",
          method: "GET",
          path: apiPath("/tracer/custom-eval-config/list_custom_eval_configs/"),
        },
        {
          label: "POST /tracer/custom-eval-config/run_evaluation/",
          method: "POST",
          path: apiPath("/tracer/custom-eval-config/run_evaluation/"),
          body: {},
        },
        {
          label: "GET /tracer/custom-eval-config/{id}/",
          method: "GET",
          path: apiPath("/tracer/custom-eval-config/{id}/", {
            id: guardUuid,
          }),
        },
        {
          label: "PUT /tracer/custom-eval-config/{id}/",
          method: "PUT",
          path: apiPath("/tracer/custom-eval-config/{id}/", {
            id: guardUuid,
          }),
          body: {},
        },
        {
          label: "PATCH /tracer/custom-eval-config/{id}/",
          method: "PATCH",
          path: apiPath("/tracer/custom-eval-config/{id}/", {
            id: guardUuid,
          }),
          body: {},
        },
      ];

      for (const endpoint of endpoints) {
        const result = await request(
          apiBase,
          endpoint.method,
          endpoint.path,
          endpoint.body,
        );
        assertJsonAuthGuard(result, endpoint.label);
        evidence.push({
          label: endpoint.label,
          method: endpoint.method,
          path: endpoint.path,
          status: result.status,
          code: result.body?.code || null,
          auth_boundary: "user_session",
        });
      }
    },
  },
  {
    id: "PUBLIC-AUTH-042",
    title:
      "Generated tracer dataset routes reject anonymous requests before observe dataset work",
    tags: ["public", "auth", "guard", "tracer", "observe", "datasets"],
    public: true,
    async run({ apiBase, evidence }) {
      const guardUuid = "00000000-0000-0000-0000-000000000001";
      const endpoints = [
        {
          label: "GET /tracer/dataset/",
          method: "GET",
          path: apiPath("/tracer/dataset/"),
        },
        {
          label: "POST /tracer/dataset/",
          method: "POST",
          path: apiPath("/tracer/dataset/"),
          body: {},
        },
        {
          label: "POST /tracer/dataset/add_to_existing_dataset/",
          method: "POST",
          path: apiPath("/tracer/dataset/add_to_existing_dataset/"),
          body: {},
        },
        {
          label: "POST /tracer/dataset/add_to_new_dataset/",
          method: "POST",
          path: apiPath("/tracer/dataset/add_to_new_dataset/"),
          body: {},
        },
        {
          label: "GET /tracer/dataset/{id}/",
          method: "GET",
          path: apiPath("/tracer/dataset/{id}/", { id: guardUuid }),
        },
        {
          label: "PUT /tracer/dataset/{id}/",
          method: "PUT",
          path: apiPath("/tracer/dataset/{id}/", { id: guardUuid }),
          body: {},
        },
        {
          label: "PATCH /tracer/dataset/{id}/",
          method: "PATCH",
          path: apiPath("/tracer/dataset/{id}/", { id: guardUuid }),
          body: {},
        },
        {
          label: "DELETE /tracer/dataset/{id}/",
          method: "DELETE",
          path: apiPath("/tracer/dataset/{id}/", { id: guardUuid }),
        },
      ];

      for (const endpoint of endpoints) {
        const result = await request(
          apiBase,
          endpoint.method,
          endpoint.path,
          endpoint.body,
        );
        assertJsonAuthGuard(result, endpoint.label);
        evidence.push({
          label: endpoint.label,
          method: endpoint.method,
          path: endpoint.path,
          status: result.status,
          code: result.body?.code || null,
          auth_boundary: "user_session",
        });
      }
    },
  },
  {
    id: "PUBLIC-AUTH-043",
    title:
      "Generated tracer observability provider routes reject anonymous requests before provider work",
    tags: [
      "public",
      "auth",
      "guard",
      "tracer",
      "observe",
      "agents",
      "providers",
    ],
    public: true,
    async run({ apiBase, evidence }) {
      const guardUuid = "00000000-0000-0000-0000-000000000001";
      const endpoints = [
        {
          label: "GET /tracer/observability-provider/",
          method: "GET",
          path: apiPath("/tracer/observability-provider/"),
        },
        {
          label: "POST /tracer/observability-provider/",
          method: "POST",
          path: apiPath("/tracer/observability-provider/"),
          body: {},
        },
        {
          label: "POST /tracer/observability-provider/verify_api_key/",
          method: "POST",
          path: apiPath("/tracer/observability-provider/verify_api_key/"),
          body: {},
        },
        {
          label: "POST /tracer/observability-provider/verify_assistant_id/",
          method: "POST",
          path: apiPath("/tracer/observability-provider/verify_assistant_id/"),
          body: {},
        },
        {
          label: "GET /tracer/observability-provider/{id}/",
          method: "GET",
          path: apiPath("/tracer/observability-provider/{id}/", {
            id: guardUuid,
          }),
        },
        {
          label: "PUT /tracer/observability-provider/{id}/",
          method: "PUT",
          path: apiPath("/tracer/observability-provider/{id}/", {
            id: guardUuid,
          }),
          body: {},
        },
        {
          label: "PATCH /tracer/observability-provider/{id}/",
          method: "PATCH",
          path: apiPath("/tracer/observability-provider/{id}/", {
            id: guardUuid,
          }),
          body: {},
        },
        {
          label: "DELETE /tracer/observability-provider/{id}/",
          method: "DELETE",
          path: apiPath("/tracer/observability-provider/{id}/", {
            id: guardUuid,
          }),
        },
      ];

      for (const endpoint of endpoints) {
        const result = await request(
          apiBase,
          endpoint.method,
          endpoint.path,
          endpoint.body,
        );
        assertJsonAuthGuard(result, endpoint.label);
        evidence.push({
          label: endpoint.label,
          method: endpoint.method,
          path: endpoint.path,
          status: result.status,
          code: result.body?.code || null,
          auth_boundary: "user_session",
        });
      }
    },
  },
  {
    id: "PUBLIC-AUTH-044",
    title:
      "Generated tracer replay session routes reject anonymous requests before replay work",
    tags: [
      "public",
      "auth",
      "guard",
      "tracer",
      "observe",
      "sessions",
      "scenarios",
    ],
    public: true,
    async run({ apiBase, evidence }) {
      const guardUuid = "00000000-0000-0000-0000-000000000001";
      const endpoints = [
        {
          label: "GET /tracer/replay-session/",
          method: "GET",
          path: apiPath("/tracer/replay-session/"),
        },
        {
          label: "POST /tracer/replay-session/",
          method: "POST",
          path: apiPath("/tracer/replay-session/"),
          body: {},
        },
        {
          label: "GET /tracer/replay-session/eval-configs/",
          method: "GET",
          path: apiPath("/tracer/replay-session/eval-configs/"),
        },
        {
          label: "GET /tracer/replay-session/{id}/",
          method: "GET",
          path: apiPath("/tracer/replay-session/{id}/", {
            id: guardUuid,
          }),
        },
        {
          label: "POST /tracer/replay-session/{id}/generate-scenario/",
          method: "POST",
          path: apiPath("/tracer/replay-session/{id}/generate-scenario/", {
            id: guardUuid,
          }),
          body: {},
        },
      ];

      for (const endpoint of endpoints) {
        const result = await request(
          apiBase,
          endpoint.method,
          endpoint.path,
          endpoint.body,
        );
        assertJsonAuthGuard(result, endpoint.label);
        evidence.push({
          label: endpoint.label,
          method: endpoint.method,
          path: endpoint.path,
          status: result.status,
          code: result.body?.code || null,
          auth_boundary: "user_session",
        });
      }
    },
  },
  {
    id: "PUBLIC-AUTH-045",
    title:
      "Generated tracer observe helper routes reject anonymous requests before helper work",
    tags: [
      "public",
      "auth",
      "guard",
      "tracer",
      "observe",
      "users",
      "annotations",
      "imagine",
    ],
    public: true,
    async run({ apiBase, evidence }) {
      const endpoints = [
        {
          label: "GET /tracer/get-annotation-labels/",
          method: "GET",
          path: apiPath("/tracer/get-annotation-labels/"),
        },
        {
          label: "GET /tracer/imagine-analysis/",
          method: "GET",
          path: apiPath("/tracer/imagine-analysis/"),
        },
        {
          label: "POST /tracer/imagine-analysis/",
          method: "POST",
          path: apiPath("/tracer/imagine-analysis/"),
          body: {},
        },
        {
          label: "GET /tracer/users/",
          method: "GET",
          path: apiPath("/tracer/users/"),
        },
        {
          label: "GET /tracer/users/get_code_example/",
          method: "GET",
          path: apiPath("/tracer/users/get_code_example/"),
        },
      ];

      for (const endpoint of endpoints) {
        const result = await request(
          apiBase,
          endpoint.method,
          endpoint.path,
          endpoint.body,
        );
        assertJsonAuthGuard(result, endpoint.label);
        evidence.push({
          label: endpoint.label,
          method: endpoint.method,
          path: endpoint.path,
          status: result.status,
          code: result.body?.code || null,
          auth_boundary: "user_session",
        });
      }
    },
  },
  {
    id: "PUBLIC-AUTH-046",
    title:
      "Hidden tracer chart and trace-annotation CRUD wrappers reject anonymous requests before hidden route guards",
    tags: [
      "public",
      "auth",
      "guard",
      "tracer",
      "observe",
      "hidden-contract",
      "dead-code",
    ],
    public: true,
    async run({ apiBase, evidence }) {
      const guardUuid = "00000000-0000-0000-0000-000000000001";
      const endpoints = [
        {
          label: "GET /tracer/charts/",
          method: "GET",
          path: "/tracer/charts/",
        },
        {
          label: "POST /tracer/charts/",
          method: "POST",
          path: "/tracer/charts/",
          body: {},
        },
        {
          label: "GET /tracer/charts/{id}/",
          method: "GET",
          path: `/tracer/charts/${guardUuid}/`,
        },
        {
          label: "PUT /tracer/charts/{id}/",
          method: "PUT",
          path: `/tracer/charts/${guardUuid}/`,
          body: {},
        },
        {
          label: "PATCH /tracer/charts/{id}/",
          method: "PATCH",
          path: `/tracer/charts/${guardUuid}/`,
          body: {},
        },
        {
          label: "DELETE /tracer/charts/{id}/",
          method: "DELETE",
          path: `/tracer/charts/${guardUuid}/`,
        },
        {
          label: "GET /tracer/trace-annotation/",
          method: "GET",
          path: "/tracer/trace-annotation/",
        },
        {
          label: "POST /tracer/trace-annotation/",
          method: "POST",
          path: "/tracer/trace-annotation/",
          body: {},
        },
        {
          label: "GET /tracer/trace-annotation/{id}/",
          method: "GET",
          path: `/tracer/trace-annotation/${guardUuid}/`,
        },
        {
          label: "PUT /tracer/trace-annotation/{id}/",
          method: "PUT",
          path: `/tracer/trace-annotation/${guardUuid}/`,
          body: {},
        },
        {
          label: "PATCH /tracer/trace-annotation/{id}/",
          method: "PATCH",
          path: `/tracer/trace-annotation/${guardUuid}/`,
          body: {},
        },
        {
          label: "DELETE /tracer/trace-annotation/{id}/",
          method: "DELETE",
          path: `/tracer/trace-annotation/${guardUuid}/`,
        },
      ];

      for (const endpoint of endpoints) {
        const result = await request(
          apiBase,
          endpoint.method,
          endpoint.path,
          endpoint.body,
        );
        assertJsonAuthGuard(result, endpoint.label);
        evidence.push({
          label: endpoint.label,
          method: endpoint.method,
          path: endpoint.path,
          status: result.status,
          code: result.body?.code || null,
          auth_boundary: "user_session_before_hidden_contract_guard",
        });
      }
    },
  },
  {
    id: "PUBLIC-AUTH-047",
    title:
      "Prompt run-evals selected-version route rejects anonymous requests before eval mutation guards",
    tags: ["public", "auth", "guard", "prompts", "workbench", "evals"],
    public: true,
    async run({ apiBase, evidence }) {
      const guardUuid = "00000000-0000-0000-0000-000000000001";
      const result = await request(
        apiBase,
        "POST",
        apiPath(
          "/model-hub/prompt-templates/{id}/run-evals-on-multiple-versions/",
          { id: guardUuid },
        ),
        {
          version_to_run: ["v1"],
          prompt_eval_config_ids: [guardUuid],
          run_index: 0,
        },
      );

      assertJsonAuthGuard(
        result,
        "POST /model-hub/prompt-templates/{id}/run-evals-on-multiple-versions/",
      );
      evidence.push({
        label:
          "POST /model-hub/prompt-templates/{id}/run-evals-on-multiple-versions/",
        method: "POST",
        path: apiPath(
          "/model-hub/prompt-templates/{id}/run-evals-on-multiple-versions/",
          { id: guardUuid },
        ),
        status: result.status,
        code: result.body?.code || null,
        run_index: 0,
        auth_boundary: "user_session_before_prompt_eval_mutation",
      });
    },
  },
  {
    id: "PUBLIC-SYSTEM-046",
    title:
      "Generated tracer public and system ingress routes return bounded JSON contracts",
    tags: [
      "public",
      "system",
      "tracer",
      "observe",
      "otlp",
      "webhook",
      "shared-links",
      "health",
    ],
    public: true,
    async run({ apiBase, evidence }) {
      const otlp = await request(
        apiBase,
        "POST",
        apiPath("/tracer/otlp/v1/traces"),
        {},
      );
      assertJsonAuthGuard(otlp, "POST /tracer/otlp/v1/traces");
      evidence.push({
        label: "POST /tracer/otlp/v1/traces",
        method: "POST",
        path: apiPath("/tracer/otlp/v1/traces"),
        status: otlp.status,
        code: otlp.body?.code || null,
        auth_boundary: "api_key_or_langfuse_basic",
      });

      const health = await request(
        apiBase,
        "GET",
        apiPath("/tracer/v1/health"),
      );
      assertStatus(health, 200, "GET /tracer/v1/health");
      assert(
        health.body?.status === "healthy" &&
          health.body?.service === "otlp-trace-receiver",
        `GET /tracer/v1/health payload mismatch: ${formatBody(health.body)}`,
      );
      assertNoSensitiveTokens(health.body, "GET /tracer/v1/health");
      evidence.push({
        label: "GET /tracer/v1/health",
        method: "GET",
        path: apiPath("/tracer/v1/health"),
        status: health.status,
        service: health.body?.service || null,
        auth_boundary: "public_health",
      });

      const shared = await request(
        apiBase,
        "GET",
        apiPath("/tracer/shared/{token}/", {
          token: "fagi-api-journey-missing-token",
        }),
      );
      assertSharedMissingTokenBoundary(shared, "GET /tracer/shared/{token}/");
      evidence.push({
        label: "GET /tracer/shared/{token}/",
        method: "GET",
        path: apiPath("/tracer/shared/{token}/", {
          token: "fagi-api-journey-missing-token",
        }),
        status: shared.status,
        code: shared.body?.code || null,
        detail: shared.body?.detail || null,
        auth_boundary: "public_share_token",
      });

      const webhook = await request(
        apiBase,
        "POST",
        apiPath("/tracer/webhook/"),
        {
          event: "call_analyzed",
          interaction_type: "voice",
          call: { agent_id: "fagi-api-journey-missing-agent" },
        },
      );
      assertTracerWebhookNoMatchBoundary(webhook, "POST /tracer/webhook/");
      evidence.push({
        label: "POST /tracer/webhook/",
        method: "POST",
        path: apiPath("/tracer/webhook/"),
        status: webhook.status,
        code: webhook.body?.code || null,
        detail: webhook.body?.detail || null,
        auth_boundary: "retell_signed_webhook_no_matching_agent",
      });
    },
  },
  {
    id: "PUBLIC-SYSTEM-047",
    title:
      "Tracer Retell webhook verifies signatures before dispatching call logs",
    tags: [
      "public",
      "system",
      "tracer",
      "observe",
      "webhook",
      "retell",
      "mutating",
      "db-audit",
    ],
    public: true,
    async run({ apiBase, evidence, cleanup, organizationId, workspaceId }) {
      requireMutations();
      assert(
        organizationId && workspaceId,
        "Set FUTURE_AGI_ORGANIZATION_ID and FUTURE_AGI_WORKSPACE_ID for the public webhook DB fixture.",
      );

      let fixture;
      try {
        fixture = await seedRetellWebhookFixture({
          organizationId,
          workspaceId,
        });
      } catch (error) {
        const reason = backendShellErrorSummary(error);
        evidence.push({
          label: "Retell webhook fixture setup",
          status: "blocked",
          error: reason,
        });
        skip(`Retell webhook fixture setup unavailable: ${reason}`);
      }
      cleanup.defer("hard delete Retell webhook fixture", () =>
        cleanupRetellWebhookFixture(fixture),
      );

      const payload = {
        event: "call_analyzed",
        interaction_type: "voice",
        call: {
          agent_id: fixture.assistant_id,
          call_id: `api-journey-call-${fixture.suffix}`,
          retell_extra_field: { journey: "PUBLIC-SYSTEM-047" },
        },
      };

      const invalidWebhook = await request(
        apiBase,
        "POST",
        apiPath("/tracer/webhook/"),
        payload,
        { headers: { "X-Retell-Signature": "bad-signature" } },
      );
      assertStatus(invalidWebhook, 400, "invalid Retell webhook signature");
      assertJsonError(
        invalidWebhook,
        "Invalid webhook signature",
        "invalid Retell webhook signature",
      );
      assertNoHtml500(invalidWebhook, "invalid Retell webhook signature");
      assertNoSensitiveTokens(
        invalidWebhook.body,
        "invalid Retell webhook signature",
      );

      const body = JSON.stringify(payload);
      const signedWebhook = await request(
        apiBase,
        "POST",
        apiPath("/tracer/webhook/"),
        payload,
        {
          headers: {
            "X-Retell-Signature": retellWebhookSignature(body, fixture.api_key),
          },
        },
      );
      assertStatus(signedWebhook, 200, "signed Retell webhook");
      assert(
        signedWebhook.body?.status === true &&
          String(signedWebhook.body?.result || "").includes("Processed: 1"),
        `signed Retell webhook payload mismatch: ${formatBody(
          signedWebhook.body,
        )}`,
      );
      assertNoSensitiveTokens(signedWebhook.body, "signed Retell webhook");

      const audit = await loadRetellWebhookFixtureAudit(fixture);
      assert(
        Number(audit.agent_count) === 1 &&
          Number(audit.provider_count) === 1 &&
          Number(audit.version_count) === 1,
        `Retell webhook fixture audit mismatch: ${JSON.stringify(audit)}`,
      );

      evidence.push({
        label: "POST /tracer/webhook/ signed Retell callback",
        method: "POST",
        path: apiPath("/tracer/webhook/"),
        invalid_status: invalidWebhook.status,
        signed_status: signedWebhook.status,
        fixture_agent_id: fixture.agent_id,
        fixture_provider_id: fixture.provider_id,
        fixture_project_id: fixture.project_id,
        audit,
      });
    },
  },
];

async function request(apiBase, method, pathName, body, options = {}) {
  const headers = {
    "X-Forwarded-For": nextPublicApiClientIp(),
    ...(body === undefined ? {} : { "Content-Type": "application/json" }),
    ...(options.headers || {}),
  };
  const response = await fetch(`${apiBase}${pathName}`, {
    method,
    redirect: options.redirect || "follow",
    headers,
    body: body === undefined ? undefined : JSON.stringify(body),
  });
  const responseText = await response.text();
  return {
    status: response.status,
    body: parseBody(responseText),
    contentType: response.headers.get("content-type") || "",
    location: response.headers.get("location") || "",
  };
}

function nextPublicApiClientIp() {
  publicApiRequestCounter += 1;
  const secondOctet = 64 + (publicApiRequestCounter % 64);
  const thirdOctet = Math.floor(publicApiRequestCounter / 64) % 256;
  const fourthOctet = (publicApiRequestCounter % 253) + 1;
  return `198.${secondOctet}.${thirdOctet}.${fourthOctet}`;
}

async function seedRetellWebhookFixture({ organizationId, workspaceId }) {
  const suffix = randomUUID().replaceAll("-", "").slice(0, 12);
  const fixture = {
    organization_id: organizationId,
    workspace_id: workspaceId,
    project_id: randomUUID(),
    provider_id: randomUUID(),
    agent_id: randomUUID(),
    version_id: randomUUID(),
    suffix,
    assistant_id: `api-journey-retell-${suffix}`,
    api_key: `retell-secret-${suffix}`,
  };
  const script = `
import json
from accounts.models.organization import Organization
from accounts.models.workspace import Workspace
from model_hub.models.ai_model import AIModel
from simulate.models import AgentDefinition, AgentVersion
from tracer.models.observability_provider import ObservabilityProvider, ProviderChoices
from tracer.models.project import Project

cfg = json.loads(${pythonString(JSON.stringify(fixture))})
organization = Organization.objects.get(id=cfg["organization_id"])
workspace = Workspace.no_workspace_objects.get(
    id=cfg["workspace_id"],
    organization=organization,
)
project = Project.no_workspace_objects.create(
    id=cfg["project_id"],
    name=f"API Journey Retell Webhook {cfg['suffix']}",
    organization=organization,
    workspace=workspace,
    model_type=AIModel.ModelTypes.GENERATIVE_LLM,
    trace_type="observe",
)
provider = ObservabilityProvider.no_workspace_objects.create(
    id=cfg["provider_id"],
    project=project,
    provider=ProviderChoices.RETELL,
    enabled=True,
    organization=organization,
    workspace=workspace,
    metadata={"assistant_id": cfg["assistant_id"], "journey": "PUBLIC-SYSTEM-047"},
)
agent = AgentDefinition.no_workspace_objects.create(
    id=cfg["agent_id"],
    agent_name=f"API Journey Retell Agent {cfg['suffix']}",
    agent_type=AgentDefinition.AgentTypeChoices.VOICE,
    inbound=False,
    description="Disposable public API journey Retell webhook fixture",
    assistant_id=cfg["assistant_id"],
    provider=ProviderChoices.RETELL,
    organization=organization,
    workspace=workspace,
    observability_provider=provider,
)
version = AgentVersion.no_workspace_objects.create(
    id=cfg["version_id"],
    agent_definition=agent,
    organization=organization,
    workspace=workspace,
    version_number=1,
    status=AgentVersion.StatusChoices.ACTIVE,
    description="Disposable public API journey Retell webhook version",
    commit_message="PUBLIC-SYSTEM-047",
    configuration_snapshot={
        "api_key": cfg["api_key"],
        "assistant_id": cfg["assistant_id"],
        "provider": ProviderChoices.RETELL,
    },
)
print(json.dumps({
    **cfg,
    "project_id": str(project.id),
    "provider_id": str(provider.id),
    "agent_id": str(agent.id),
    "version_id": str(version.id),
}))
`;
  return runBackendShellJson(script);
}

async function loadRetellWebhookFixtureAudit(fixture) {
  const script = `
import json
from simulate.models import AgentDefinition, AgentVersion
from tracer.models.observability_provider import ObservabilityProvider
from tracer.models.project import Project

cfg = json.loads(${pythonString(JSON.stringify(fixture))})
print(json.dumps({
    "project_count": Project.no_workspace_objects.filter(id=cfg["project_id"]).count(),
    "provider_count": ObservabilityProvider.no_workspace_objects.filter(id=cfg["provider_id"]).count(),
    "agent_count": AgentDefinition.no_workspace_objects.filter(id=cfg["agent_id"]).count(),
    "version_count": AgentVersion.no_workspace_objects.filter(id=cfg["version_id"]).count(),
}))
`;
  return runBackendShellJson(script);
}

async function cleanupRetellWebhookFixture(fixture) {
  const script = `
import json
from simulate.models import AgentDefinition, AgentVersion
from tracer.models.observability_provider import ObservabilityProvider
from tracer.models.project import Project

cfg = json.loads(${pythonString(JSON.stringify(fixture))})
AgentVersion.no_workspace_objects.filter(id=cfg["version_id"]).delete()
AgentDefinition.no_workspace_objects.filter(id=cfg["agent_id"]).delete()
ObservabilityProvider.no_workspace_objects.filter(id=cfg["provider_id"]).delete()
Project.no_workspace_objects.filter(id=cfg["project_id"]).delete()
print(json.dumps({
    "remaining_projects": Project.no_workspace_objects.filter(id=cfg["project_id"]).count(),
    "remaining_providers": ObservabilityProvider.no_workspace_objects.filter(id=cfg["provider_id"]).count(),
    "remaining_agents": AgentDefinition.no_workspace_objects.filter(id=cfg["agent_id"]).count(),
    "remaining_versions": AgentVersion.no_workspace_objects.filter(id=cfg["version_id"]).count(),
}))
`;
  const result = await runBackendShellJson(script);
  assert(
    Number(result.remaining_projects) === 0 &&
      Number(result.remaining_providers) === 0 &&
      Number(result.remaining_agents) === 0 &&
      Number(result.remaining_versions) === 0,
    `Retell webhook cleanup left residue: ${JSON.stringify(result)}`,
  );
  return result;
}

async function runBackendShellJson(script) {
  const python = process.env.API_JOURNEY_BACKEND_PYTHON || "python";
  let stdout;
  const configuredContainer = process.env.API_JOURNEY_BACKEND_CONTAINER;
  const containerCandidates = configuredContainer
    ? [configuredContainer]
    : ["futureagi-ws2-backend-1", "ws2-backend"];

  for (const container of containerCandidates) {
    try {
      const result = await execFileAsync(
        "docker",
        ["exec", container, python, "manage.py", "shell", "-c", script],
        { maxBuffer: 10 * 1024 * 1024 },
      );
      stdout = result.stdout;
      break;
    } catch (error) {
      if (configuredContainer || !isBackendContainerUnavailable(error)) {
        throw error;
      }
    }
  }

  if (!stdout) {
    const cwd =
      process.env.API_JOURNEY_BACKEND_CWD ||
      fileURLToPath(new URL("../../../../futureagi/", import.meta.url));
    const result = await execFileAsync(
      "uv",
      ["run", "python", "manage.py", "shell", "-c", script],
      { cwd, maxBuffer: 10 * 1024 * 1024 },
    );
    stdout = result.stdout;
  }
  const jsonLine = stdout
    .trim()
    .split("\n")
    .map((line) => line.trim())
    .filter(Boolean)
    .reverse()
    .find((line) => line.startsWith("{"));
  assert(jsonLine, "Backend shell returned no JSON object.");
  return JSON.parse(jsonLine);
}

function isBackendContainerUnavailable(error) {
  const message = `${error?.message || ""}\n${error?.stderr || ""}`;
  return (
    message.includes("No such container") ||
    message.includes("Cannot connect to the Docker daemon") ||
    message.includes("docker: command not found") ||
    message.includes("executable file not found")
  );
}

function retellWebhookSignature(body, apiKey) {
  const timestamp = Date.now();
  const digest = createHmac("sha256", apiKey)
    .update(`${body}${timestamp}`)
    .digest("hex");
  return `v=${timestamp},d=${digest}`;
}

function pythonString(value) {
  return JSON.stringify(String(value));
}

function parseBody(text) {
  if (!text) return null;
  try {
    return JSON.parse(text);
  } catch {
    return text;
  }
}

function assertStatus(result, expectedStatus, label) {
  assert(
    result.status === expectedStatus,
    `${label} expected HTTP ${expectedStatus}, saw ${result.status}: ${formatBody(
      result.body,
    )}`,
  );
}

function assertNoHtml500(result, label) {
  assert(
    result.status !== 500 || !String(result.contentType).includes("text/html"),
    `${label} returned HTML 500 instead of a JSON API error.`,
  );
}

function assertJsonError(result, expectedText, label) {
  assert(
    result.body && typeof result.body === "object",
    `${label} returned non-JSON body: ${formatBody(result.body)}`,
  );
  const haystack = jsonErrorHaystack(result);
  assert(
    haystack.includes(expectedText),
    `${label} missing expected error text ${JSON.stringify(
      expectedText,
    )}: ${JSON.stringify(result.body)}`,
  );
}

function assertJsonErrorCaseInsensitive(result, expectedText, label) {
  assert(
    result.body && typeof result.body === "object",
    `${label} returned non-JSON body: ${formatBody(result.body)}`,
  );
  const haystack = jsonErrorHaystack(result).toLowerCase();
  assert(
    haystack.includes(expectedText.toLowerCase()),
    `${label} missing expected error text ${JSON.stringify(
      expectedText,
    )}: ${JSON.stringify(result.body)}`,
  );
}

function assertJsonAuthGuard(result, label) {
  assert(
    [401, 403].includes(result.status),
    `${label} expected anonymous auth guard 401/403, saw ${result.status}: ${formatBody(
      result.body,
    )}`,
  );
  assertJsonErrorOneOf(
    result,
    [
      "Authentication credentials",
      "requires authentication",
      "No organization context",
    ],
    label,
  );
  assertNoHtml500(result, label);
  assertNoSensitiveTokens(result.body, label);
}

function assertJsonErrorOneOf(result, expectedTexts, label) {
  assert(
    result.body && typeof result.body === "object",
    `${label} returned non-JSON body: ${formatBody(result.body)}`,
  );
  const haystack = jsonErrorHaystack(result);
  assert(
    expectedTexts.some((expectedText) => haystack.includes(expectedText)),
    `${label} missing any expected error text ${JSON.stringify(
      expectedTexts,
    )}: ${JSON.stringify(result.body)}`,
  );
}

function assertJsonPermissionGuard(result, label) {
  assert(
    result.status === 403,
    `${label} expected anonymous permission guard 403, saw ${result.status}: ${formatBody(
      result.body,
    )}`,
  );
  assertJsonErrorCaseInsensitive(result, "permission", label);
  assertNoHtml500(result, label);
  assertNoSensitiveTokens(result.body, label);
}

function assertApiKeyOrPermissionGuard(result, label) {
  assert(
    [401, 403].includes(result.status),
    `${label} expected API-key/superuser guard 401/403, saw ${result.status}: ${formatBody(
      result.body,
    )}`,
  );
  assert(
    result.body && typeof result.body === "object",
    `${label} returned non-JSON body: ${formatBody(result.body)}`,
  );
  const haystack = jsonErrorHaystack(result).toLowerCase();
  assert(
    haystack.includes("api key") ||
      haystack.includes("permission") ||
      haystack.includes("authentication") ||
      haystack.includes("credentials") ||
      haystack.includes("not authenticated"),
    `${label} missing expected API-key or permission error text: ${JSON.stringify(
      result.body,
    )}`,
  );
  assertNoHtml500(result, label);
  assertNoSensitiveTokens(result.body, label);
}

function assertJsonPublicBoundary(result, label) {
  assert(
    [200, 400].includes(result.status),
    `${label} expected public JSON boundary 200/400, saw ${result.status}: ${formatBody(
      result.body,
    )}`,
  );
  assert(
    result.body && typeof result.body === "object",
    `${label} returned non-JSON body: ${formatBody(result.body)}`,
  );
  assertNoHtml500(result, label);
  assertNoSensitiveTokens(result.body, label);
}

function assertUsageLatestPricesBoundary(result, label) {
  assertJsonPublicBoundary(result, label);
  assert(
    typeof result.body.status === "boolean",
    `${label} missing boolean status: ${formatBody(result.body)}`,
  );

  if (result.status === 200) {
    assert(
      result.body.status === true,
      `${label} 200 response must set status=true: ${formatBody(result.body)}`,
    );
    assert(
      result.body.result &&
        typeof result.body.result === "object" &&
        !Array.isArray(result.body.result),
      `${label} 200 response must include a result pricing map: ${formatBody(
        result.body,
      )}`,
    );
    const entries = Object.entries(result.body.result);
    assert(entries.length > 0, `${label} returned an empty pricing map.`);
    for (const [key, value] of entries) {
      assert(
        typeof key === "string" && key.length > 0,
        `${label} returned an invalid pricing key: ${formatBody(result.body)}`,
      );
      assert(
        typeof value === "number" && Number.isFinite(value) && value >= 0,
        `${label} returned non-numeric pricing for ${key}: ${formatBody(
          result.body,
        )}`,
      );
    }
    return;
  }

  assert(
    result.status === 400,
    `${label} expected only 200 success or 400 pricing-source error, saw ${result.status}`,
  );
  assert(
    result.body.status === false &&
      result.body.code === "invalid" &&
      typeof result.body.detail === "string" &&
      result.body.detail.includes("Unable to fetch pricing data"),
    `${label} 400 response must be the bounded pricing-source error envelope: ${formatBody(
      result.body,
    )}`,
  );
}

function assertSharedMissingTokenBoundary(result, label) {
  assert(
    [404, 503].includes(result.status),
    `${label} expected JSON 404 not_found or 503 service_unavailable, saw ${
      result.status
    }: ${formatBody(result.body)}`,
  );
  assert(
    result.body && typeof result.body === "object",
    `${label} returned non-JSON body: ${formatBody(result.body)}`,
  );
  assertNoHtml500(result, label);
  assertNoSensitiveTokens(result.body, label);

  if (result.status === 404) {
    assertJsonError(result, "Shared link not found", label);
    assert(
      result.body?.code === "not_found",
      `${label} expected not_found code, saw ${formatBody(result.body)}`,
    );
    return;
  }

  assert(
    result.body?.type === "service_unavailable" &&
      result.body?.code === "service_unavailable" &&
      String(result.body?.detail || "").includes(
        "Shared link resolver is temporarily unavailable",
      ),
    `${label} expected bounded shared-link resolver outage envelope: ${formatBody(
      result.body,
    )}`,
  );
}

function assertTracerWebhookNoMatchBoundary(result, label) {
  assert(
    [400, 503].includes(result.status),
    `${label} expected JSON 400 no-match or 503 service_unavailable, saw ${
      result.status
    }: ${formatBody(result.body)}`,
  );
  assert(
    result.body && typeof result.body === "object",
    `${label} returned non-JSON body: ${formatBody(result.body)}`,
  );
  assertNoHtml500(result, label);
  assertNoSensitiveTokens(result.body, label);

  if (result.status === 400) {
    assertJsonError(result, "No matching agent definition found", label);
    assert(
      result.body?.code === "invalid",
      `${label} expected invalid code, saw ${formatBody(result.body)}`,
    );
    return;
  }

  assert(
    result.body?.type === "service_unavailable" &&
      result.body?.code === "service_unavailable" &&
      String(result.body?.detail || "").includes(
        "Webhook agent lookup is temporarily unavailable",
      ),
    `${label} expected bounded webhook agent lookup outage envelope: ${formatBody(
      result.body,
    )}`,
  );
}

function assertWebhookSecretGuard(result, label) {
  assert(
    result.status === 400,
    `${label} expected webhook secret validation 400, saw ${result.status}: ${formatBody(
      result.body,
    )}`,
  );
  assertJsonErrorCaseInsensitive(result, "webhook secret", label);
  assertNoHtml500(result, label);
  assertNoSensitiveTokens(result.body, label);
}

function assertLiveKitInternalBearerGuard(result, label) {
  assert(
    result.status === 401,
    `${label} expected internal bearer guard 401, saw ${result.status}: ${formatBody(
      result.body,
    )}`,
  );
  assertJsonErrorCaseInsensitive(result, "Missing Bearer token", label);
  assertNoHtml500(result, label);
  assertNoSensitiveTokens(redactSafeBearerErrorText(result.body), label);
}

function assertLiveKitWebhookGuard(result, label) {
  assert(
    result.status === 401,
    `${label} expected signed webhook guard 401, saw ${result.status}: ${formatBody(
      result.body,
    )}`,
  );
  assertJsonErrorCaseInsensitive(result, "Missing Authorization header", label);
  assert(
    !result.body?.code || result.body.code === "not_authenticated",
    `${label} expected not_authenticated webhook code, saw ${JSON.stringify(
      result.body?.code,
    )}`,
  );
  assertNoHtml500(result, label);
  assertNoSensitiveTokens(result.body, label);
}

function assertSamlPublicRedirect(result, label) {
  assert(
    [302, 400].includes(result.status),
    `${label} expected SAML redirect/client boundary 302/400, saw ${result.status}: ${formatBody(
      result.body,
    )}`,
  );
  assertNoHtml500(result, label);
  if (result.status === 302) {
    assert(result.location, `${label} returned 302 without a Location header`);
  } else {
    assert(
      result.body && typeof result.body === "object",
      `${label} returned non-JSON client error: ${formatBody(result.body)}`,
    );
  }
  assertNoSensitiveTokens(
    { body: result.body, location: result.location },
    label,
  );
}

function assertHtmlClientError(result, expectedText, label) {
  assert(
    [400, 401, 403, 404].includes(result.status),
    `${label} expected HTML client error, saw ${result.status}: ${formatBody(
      result.body,
    )}`,
  );
  assertNoHtml500(result, label);
  assert(
    String(result.contentType).includes("text/html"),
    `${label} expected text/html response, saw ${result.contentType}`,
  );
  assert(
    typeof result.body === "string" && result.body.includes(expectedText),
    `${label} missing expected text ${JSON.stringify(
      expectedText,
    )}: ${formatBody(result.body)}`,
  );
  assertNoSensitiveTokens(result.body, label);
}

function assertJsonClientError(result, expectedText, label) {
  assert(
    [400, 401, 403, 404].includes(result.status),
    `${label} expected JSON client error, saw ${result.status}: ${formatBody(
      result.body,
    )}`,
  );
  assertJsonError(result, expectedText, label);
  assertNoHtml500(result, label);
  assertNoSensitiveTokens(result.body, label);
}

function jsonErrorHaystack(result) {
  return [
    result.body.error,
    result.body.detail,
    result.body.message,
    result.body.result,
    result.body.details ? JSON.stringify(result.body.details) : null,
  ]
    .filter(Boolean)
    .join(" ");
}

function assertOAuthError(result, expectedError, label) {
  assert(
    result.body?.error === expectedError,
    `${label} expected OAuth error ${expectedError}, saw ${JSON.stringify(
      result.body,
    )}`,
  );
}

function assertNoSensitiveTokens(value, label) {
  const serialized = JSON.stringify(value || {});
  assert(
    !/(access_token|refresh_token|client_secret|Bearer\s+[A-Za-z0-9._-]+)/i.test(
      serialized,
    ),
    `${label} exposed token-like data: ${serialized}`,
  );
}

function redactSafeBearerErrorText(value) {
  return JSON.parse(
    JSON.stringify(value || {}).replace(/Bearer token/g, "Bearer"),
  );
}

function formatBody(body) {
  if (typeof body === "string") return body.slice(0, 500);
  return JSON.stringify(body).slice(0, 500);
}

function firstLine(value) {
  return String(value || "")
    .split("\n")[0]
    .slice(0, 500);
}

function backendShellErrorSummary(error) {
  const lines = `${error?.stderr || ""}\n${error?.message || ""}`
    .split("\n")
    .map((line) => line.trim())
    .filter(Boolean);
  return (
    lines.find((line) =>
      /OperationalError|failed to resolve|No such container|docker/i.test(line),
    ) ||
    firstLine(error?.message) ||
    "backend shell command failed"
  ).slice(0, 500);
}
