import fs from "node:fs/promises";
import os from "node:os";
import path from "node:path";
import { afterEach, describe, expect, it } from "vitest";
import {
  checkApiInventoryDocs,
  parseCsv,
} from "./check-api-inventory-docs.mjs";

describe("api inventory docs checker", () => {
  const cleanup = [];

  afterEach(async () => {
    for (const item of cleanup.splice(0).reverse()) {
      await fs.rm(item, { force: true, recursive: true });
    }
  });

  it("parses quoted CSV fields with commas and newlines", () => {
    expect(parseCsv('a,b\n"one, two","line 1\nline 2"\n')).toEqual([
      ["a", "b"],
      ["one, two", "line 1\nline 2"],
    ]);
  });

  it("passes when every OpenAPI operation is inventoried and reports non-Swagger extras", async () => {
    const { auditDocsPath, extraAllowlistPath, inventoryPath, swaggerPath } =
      await makeFixture({
        swagger: {
          paths: {
            "/accounts/user-info/": {
              get: { operationId: "accounts_user_info" },
            },
            "/tracer/project/": {
              post: { operationId: "tracer_project_create" },
            },
          },
        },
        inventoryRows: [
          row({
            endpoint_name: "accounts_user_info",
            method: "GET",
            path: "/accounts/user-info/",
          }),
          row({
            endpoint_name: "tracer_project_create",
            method: "POST",
            path: "/tracer/project/",
          }),
          row({
            endpoint_name: "tracer_hidden_delete",
            method: "DELETE",
            path: "/tracer/project/",
            status: "hidden from generated contract",
            notes: "Dead-code audit tracks this non-Swagger route.",
          }),
        ],
        extraAllowlist: [
          {
            method: "DELETE",
            path: "/tracer/project/",
            reason: "Hidden route tracked by dead-code audit.",
            audit_refs: ["AUD-111"],
          },
        ],
        auditRows: ["AUD-111"],
      });

    const result = await checkApiInventoryDocs({
      auditDocsPath,
      extraAllowlistPath,
      inventoryPath,
      swaggerPath,
    });

    expect(result).toMatchObject({
      status: "passed",
      openapi_operation_count: 2,
      inventory_row_count: 3,
      inventory_unique_operation_count: 3,
      missing_openapi_operation_count: 0,
      extra_inventory_operation_count: 1,
      allowed_extra_inventory_operation_count: 1,
      unallowlisted_extra_inventory_operation_count: 0,
      invalid_extra_allowlist_audit_ref_count: 0,
      duplicate_inventory_operation_count: 0,
    });
    expect(result.extra_inventory_operations[0]).toMatchObject({
      method: "DELETE",
      path: "/tracer/project/",
    });
  });

  it("fails on missing OpenAPI operations, duplicate inventory rows, invalid rows, and strict extras", async () => {
    const { auditDocsPath, extraAllowlistPath, inventoryPath, swaggerPath } =
      await makeFixture({
        swagger: {
          paths: {
            "/accounts/user-info/": {
              get: { operationId: "accounts_user_info" },
            },
            "/tracer/project/": {
              post: { operationId: "tracer_project_create" },
            },
          },
        },
        inventoryRows: [
          row({
            endpoint_name: "accounts_user_info",
            method: "GET",
            path: "/accounts/user-info/",
          }),
          row({
            endpoint_name: "duplicate_user_info",
            method: "GET",
            path: "/accounts/user-info/",
          }),
          row({
            endpoint_name: "extra_hidden_route",
            method: "DELETE",
            path: "/hidden/route/",
          }),
          row({
            endpoint_name: "invalid_method",
            method: "FETCH",
            path: "/bad/method/",
          }),
        ],
        extraAllowlist: [
          {
            method: "DELETE",
            path: "/hidden/route/",
            reason: "Hidden fixture route.",
            audit_refs: ["AUD-001"],
          },
        ],
        auditRows: ["AUD-001"],
      });

    const result = await checkApiInventoryDocs({
      auditDocsPath,
      extraAllowlistPath,
      inventoryPath,
      swaggerPath,
      strictExtra: true,
    });

    expect(result).toMatchObject({
      status: "failed",
      missing_openapi_operation_count: 1,
      extra_inventory_operation_count: 2,
      allowed_extra_inventory_operation_count: 1,
      unallowlisted_extra_inventory_operation_count: 1,
      duplicate_inventory_operation_count: 1,
      invalid_inventory_row_count: 1,
    });
    expect(result.missing_openapi_operations[0]).toMatchObject({
      method: "POST",
      path: "/tracer/project/",
    });
    expect(result.duplicate_inventory_operations[0].rows).toHaveLength(2);
  });

  it("fails when non-Swagger inventory rows are not explicitly allowlisted", async () => {
    const { auditDocsPath, extraAllowlistPath, inventoryPath, swaggerPath } =
      await makeFixture({
        swagger: {
          paths: {
            "/accounts/user-info/": {
              get: { operationId: "accounts_user_info" },
            },
          },
        },
        inventoryRows: [
          row({
            endpoint_name: "accounts_user_info",
            method: "GET",
            path: "/accounts/user-info/",
          }),
          row({
            endpoint_name: "extra_hidden_route",
            method: "DELETE",
            path: "/hidden/route/",
          }),
        ],
        extraAllowlist: [
          {
            method: "GET",
            path: "/stale/allowlist/",
            reason: "Old hidden route that should be removed from allowlist.",
            audit_refs: ["AUD-OLD"],
          },
          {
            method: "FETCH",
            path: "/bad/allowlist/",
            reason: "",
            audit_refs: [],
          },
        ],
        auditRows: ["AUD-OLD"],
      });

    const result = await checkApiInventoryDocs({
      auditDocsPath,
      extraAllowlistPath,
      inventoryPath,
      swaggerPath,
    });

    expect(result).toMatchObject({
      status: "failed",
      extra_inventory_operation_count: 1,
      unallowlisted_extra_inventory_operation_count: 1,
      stale_extra_allowlist_operation_count: 2,
      invalid_extra_allowlist_operation_count: 1,
    });
    expect(result.unallowlisted_extra_inventory_operations[0]).toMatchObject({
      method: "DELETE",
      path: "/hidden/route/",
    });
  });

  it("fails when allowlisted extras lack a real audit row reference", async () => {
    const { auditDocsPath, extraAllowlistPath, inventoryPath, swaggerPath } =
      await makeFixture({
        swagger: {
          paths: {
            "/accounts/user-info/": {
              get: { operationId: "accounts_user_info" },
            },
          },
        },
        inventoryRows: [
          row({
            endpoint_name: "accounts_user_info",
            method: "GET",
            path: "/accounts/user-info/",
          }),
          row({
            endpoint_name: "extra_hidden_route",
            method: "DELETE",
            path: "/hidden/route/",
          }),
          row({
            endpoint_name: "extra_without_audit",
            method: "POST",
            path: "/missing/audit/",
          }),
        ],
        extraAllowlist: [
          {
            method: "DELETE",
            path: "/hidden/route/",
            reason: "Hidden fixture route.",
            audit_refs: ["AUD-999"],
          },
          {
            method: "POST",
            path: "/missing/audit/",
            reason: "Hidden fixture route without an audit id.",
            audit_refs: ["DFE-123"],
          },
        ],
        auditRows: ["AUD-001"],
      });

    const result = await checkApiInventoryDocs({
      auditDocsPath,
      extraAllowlistPath,
      inventoryPath,
      swaggerPath,
    });

    expect(result).toMatchObject({
      status: "failed",
      extra_inventory_operation_count: 2,
      allowed_extra_inventory_operation_count: 2,
      invalid_extra_allowlist_audit_ref_count: 2,
    });
    expect(result.invalid_extra_allowlist_audit_refs).toEqual(
      expect.arrayContaining([
        expect.objectContaining({
          method: "DELETE",
          path: "/hidden/route/",
          problem: "unknown_audit_ref",
          audit_ref: "AUD-999",
        }),
        expect.objectContaining({
          method: "POST",
          path: "/missing/audit/",
          problem: "missing_audit_ref",
        }),
      ]),
    );
  });

  it("fails when audit ids are duplicated or inventory refs unknown audit rows", async () => {
    const { auditDocsPath, extraAllowlistPath, inventoryPath, swaggerPath } =
      await makeFixture({
        swagger: {
          paths: {
            "/accounts/user-info/": {
              get: { operationId: "accounts_user_info" },
            },
          },
        },
        inventoryRows: [
          row({
            endpoint_name: "accounts_user_info",
            method: "GET",
            path: "/accounts/user-info/",
            evidence: "Covered by AUD-001 and AUD-404.",
            notes: "Follow-up tracked in AUD-405.",
          }),
        ],
        auditRows: ["AUD-001", "AUD-001"],
      });

    const result = await checkApiInventoryDocs({
      auditDocsPath,
      extraAllowlistPath,
      inventoryPath,
      swaggerPath,
    });

    expect(result).toMatchObject({
      status: "failed",
      duplicate_audit_id_count: 1,
      inventory_audit_ref_count: 3,
      invalid_inventory_audit_ref_count: 2,
    });
    expect(result.duplicate_audit_ids[0]).toMatchObject({
      audit_id: "AUD-001",
    });
    expect(result.duplicate_audit_ids[0].rows).toHaveLength(2);
    expect(result.invalid_inventory_audit_refs).toEqual(
      expect.arrayContaining([
        expect.objectContaining({
          line: 2,
          endpoint_name: "accounts_user_info",
          field: "evidence",
          audit_ref: "AUD-404",
        }),
        expect.objectContaining({
          line: 2,
          endpoint_name: "accounts_user_info",
          field: "notes",
          audit_ref: "AUD-405",
        }),
      ]),
    );
  });

  it("fails when inventory source_file refs are stale or not repo-relative code paths", async () => {
    const {
      auditDocsPath,
      extraAllowlistPath,
      inventoryPath,
      repositoryRoot,
      swaggerPath,
    } = await makeFixture({
      swagger: {
        paths: {
          "/accounts/user-info/": {
            get: { operationId: "accounts_user_info" },
          },
          "/tracer/users/": {
            get: { operationId: "tracer_users_list" },
          },
        },
      },
      inventoryRows: [
        row({
          endpoint_name: "accounts_user_info",
          method: "GET",
          path: "/accounts/user-info/",
          source_file: "futureagi/accounts/views/user.py",
        }),
        row({
          endpoint_name: "tracer_users_list",
          method: "GET",
          path: "/tracer/users/",
          source_file:
            "futureagi/tracer/services/query_builders/users.py; docs/not-code.md",
        }),
      ],
      sourceFiles: ["futureagi/accounts/views/user.py"],
    });

    const result = await checkApiInventoryDocs({
      auditDocsPath,
      extraAllowlistPath,
      inventoryPath,
      repositoryRoot,
      swaggerPath,
    });

    expect(result).toMatchObject({
      status: "failed",
      source_file_ref_count: 3,
      missing_source_file_ref_count: 1,
      invalid_source_file_ref_count: 1,
    });
    expect(result.missing_source_file_refs[0]).toMatchObject({
      line: 3,
      endpoint_name: "tracer_users_list",
      source_file: "futureagi/tracer/services/query_builders/users.py",
    });
    expect(result.invalid_source_file_refs[0]).toMatchObject({
      line: 3,
      endpoint_name: "tracer_users_list",
      source_file: "docs/not-code.md",
      problem: "not_repo_relative_source_file",
    });
  });

  it("fails when frontend_call_site refs are stale or escape the repo root", async () => {
    const {
      auditDocsPath,
      extraAllowlistPath,
      inventoryPath,
      repositoryRoot,
      swaggerPath,
    } = await makeFixture({
      swagger: {
        paths: {
          "/model-hub/ai-filter/": {
            post: { operationId: "model_hub_ai_filter" },
          },
          "/tracer/dashboard/{id}/": {
            get: { operationId: "tracer_dashboard_read" },
          },
        },
      },
      inventoryRows: [
        row({
          endpoint_name: "model_hub_ai_filter",
          method: "POST",
          path: "/model-hub/ai-filter/",
          frontend_call_site:
            "frontend/src/hooks/use-ai-filter.js; generated API client; frontend/src/utils/axios.js)",
        }),
        row({
          endpoint_name: "tracer_dashboard_read",
          method: "GET",
          path: "/tracer/dashboard/{id}/",
          frontend_call_site:
            "frontend/src/sections/dashboards/DashboardDetailView; frontend/../../outside.js",
        }),
      ],
      sourceFiles: [
        "frontend/src/hooks/use-ai-filter.js",
        "frontend/src/utils/axios.js",
      ],
    });

    const result = await checkApiInventoryDocs({
      auditDocsPath,
      extraAllowlistPath,
      inventoryPath,
      repositoryRoot,
      swaggerPath,
    });

    expect(result).toMatchObject({
      status: "failed",
      frontend_call_site_ref_count: 4,
      missing_frontend_call_site_ref_count: 1,
      invalid_frontend_call_site_ref_count: 1,
    });
    expect(result.missing_frontend_call_site_refs[0]).toMatchObject({
      line: 3,
      endpoint_name: "tracer_dashboard_read",
      frontend_call_site:
        "frontend/src/sections/dashboards/DashboardDetailView",
    });
    expect(result.invalid_frontend_call_site_refs[0]).toMatchObject({
      line: 3,
      endpoint_name: "tracer_dashboard_read",
      frontend_call_site: "frontend/../../outside.js",
      problem: "escapes_repository_root",
    });
  });

  async function makeFixture({
    auditRows = [],
    extraAllowlist = [],
    swagger,
    inventoryRows,
    sourceFiles = [],
  }) {
    const root = await fs.mkdtemp(path.join(os.tmpdir(), "api-inventory-"));
    cleanup.push(root);
    const auditDocsPath = path.join(root, "07-audit.csv");
    const swaggerPath = path.join(root, "swagger.json");
    const inventoryPath = path.join(root, "00-api-inventory.csv");
    const extraAllowlistPath = path.join(root, "extra-allowlist.json");
    await fs.writeFile(
      auditDocsPath,
      `audit_id,status\n${auditRows.map((id) => `${id},verified`).join("\n")}\n`,
    );
    await fs.writeFile(swaggerPath, `${JSON.stringify(swagger, null, 2)}\n`);
    await fs.writeFile(
      inventoryPath,
      `${HEADER.join(",")}\n${inventoryRows.join("\n")}\n`,
    );
    await fs.writeFile(
      extraAllowlistPath,
      `${JSON.stringify(extraAllowlist, null, 2)}\n`,
    );
    for (const sourceFile of sourceFiles) {
      const sourcePath = path.join(root, sourceFile);
      await fs.mkdir(path.dirname(sourcePath), { recursive: true });
      await fs.writeFile(sourcePath, "\n");
    }
    return {
      auditDocsPath,
      extraAllowlistPath,
      inventoryPath,
      repositoryRoot: root,
      swaggerPath,
    };
  }
});

const HEADER = [
  "feature_area",
  "endpoint_name",
  "method",
  "path",
  "source_file",
  "frontend_call_site",
  "real_user_action",
  "auth_user",
  "status",
  "last_tested",
  "evidence",
  "notes",
];

function row(values) {
  return HEADER.map((name) => csvCell(values[name] || "")).join(",");
}

function csvCell(value) {
  const text = String(value);
  if (!/[",\n]/.test(text)) return text;
  return `"${text.replaceAll('"', '""')}"`;
}
