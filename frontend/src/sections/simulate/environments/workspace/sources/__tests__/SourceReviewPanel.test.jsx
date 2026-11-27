import { fireEvent, render, screen } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { beforeEach, describe, expect, it, vi } from "vitest";
import SourceReviewPanel from "../SourceReviewPanel";
import { sourceReviewKey } from "src/api/simulate-environments/sourceImports";

const api = vi.hoisted(() => ({ get: vi.fn(), post: vi.fn() }));
vi.mock("src/utils/axios", () => ({ default: api }));

const path = "/simulate/api/harness-source-reviews/review-1/";
const preview = {
  draft_digest: "sha256:before",
  sources: [
    {
      pin: { source_id: "orders", role: "seed_records" },
      filename: "orders.csv",
      fields: [{ source_field: "order_id" }],
      row_count: 1,
    },
  ],
};
const repair = {
  id: "review-1",
  scope: { kind: "design", id: "job-1", schema_digest: "sha256:schema" },
  state: "needs_repair",
  revision: 2,
  draft_digest: preview.draft_digest,
  preview,
};
const policyId = "src_3bdd:page:1:statement";
const orderRule = "Responses must include “order number”";
const refundRule = "Responses must include “Refund ID”";
const policyPreview = {
  ...preview,
  sources: [
    {
      pin: { source_id: "src_3bdd", role: "policy" },
      filename: "refund-rules.pdf",
      statements: [
        'The response must contain "order number".',
        "Refunds are paid back within 5 business days.",
        'Always include "Refund ID" in every reply.',
      ].map((text, i) => ({
        page: 1,
        text,
        statement_id: `${policyId}:${[2, 4, 5][i]}`,
        text_truncated: false,
      })),
    },
  ],
  proposals: {
    policy_rules: [
      [2, "supported", orderRule],
      [4, "unsupported"],
      [5, "supported", refundRule],
    ].map(([n, status, title]) => ({
      rule_id: `${policyId}:${n}:rule`,
      statement_id: `${policyId}:${n}`,
      status,
      ...(title && { title }),
    })),
  },
};
let serverReview;

beforeEach(() => {
  serverReview = repair;
  api.get.mockReset().mockImplementation(async () => ({ data: serverReview }));
  api.post.mockReset();
});

function mount(onAccepted, cachedReview, compact = false) {
  const client = new QueryClient({
    defaultOptions: { queries: { retry: false } },
  });
  if (cachedReview)
    client.setQueryData(sourceReviewKey("review-1"), cachedReview);
  return render(
    <QueryClientProvider client={client}>
      <SourceReviewPanel
        reviewId="review-1"
        ownerId="job-1"
        canEdit
        compact={compact}
        acceptLabel="Approve sources and start build"
        onAccepted={onAccepted}
      />
    </QueryClientProvider>,
  );
}

describe("Prebuild source review", () => {
  it("reauthorizes the server preview before displaying cached source details", async () => {
    api.get.mockRejectedValueOnce(new Error("This review is not available."));
    mount(undefined, repair);
    expect(screen.queryByText("orders.csv")).toBeNull();
    expect(await screen.findByRole("alert")).toHaveTextContent(
      "This review is not available.",
    );
    expect(screen.queryByText("orders.csv")).toBeNull();
  });

  it("keeps inferred-schema edits out of a review pinned to a template table", async () => {
    serverReview = {
      ...repair,
      scope: { ...repair.scope, target_table: "orders" },
    };
    mount();
    expect(await screen.findByText("orders.csv")).toBeInTheDocument();
    expect(
      screen.queryByRole("combobox", { name: "Identify records by" }),
    ).toBeNull();
    expect(screen.queryByRole("button", { name: "Prepare again" })).toBeNull();
    expect(screen.getByRole("button", { name: "Cancel review" })).toBeEnabled();
  });

  it("repairs inferred fields and launches only after approving the new revision", async () => {
    const onAccepted = vi.fn();
    api.post
      .mockImplementationOnce(async () => {
        serverReview = {
          ...repair,
          scope: { ...repair.scope, schema_digest: "sha256:new-schema" },
          state: "ready",
          revision: 3,
          draft_digest: "sha256:repaired",
          preview: {
            ...preview,
            draft_digest: "sha256:repaired",
            proposals: {
              field_mappings: [
                {
                  source_id: "orders",
                  mapping: {
                    source_field: "order_id",
                    target_field: "order_id",
                    is_stable_id: true,
                  },
                },
              ],
            },
          },
        };
        return { data: serverReview };
      })
      .mockImplementationOnce(async () => {
        serverReview = { ...serverReview, state: "accepted", revision: 4 };
        return { data: serverReview };
      });
    mount(onAccepted);
    expect(await screen.findByText("orders.csv")).toBeInTheDocument();
    expect(
      screen.queryByRole("button", { name: "Approve sources and start build" }),
    ).toBeNull();
    fireEvent.mouseDown(
      screen.getByRole("combobox", { name: "Identify records by" }),
    );
    fireEvent.click(screen.getByRole("option", { name: "order_id" }));
    fireEvent.keyDown(screen.getByRole("option", { name: "order_id" }), {
      key: "Escape",
    });
    fireEvent.click(screen.getByRole("button", { name: "Prepare again" }));
    const approve = await screen.findByRole("button", {
      name: "Approve sources and start build",
    });
    expect(onAccepted).not.toHaveBeenCalled();
    expect(api.post).toHaveBeenNthCalledWith(1, `${path}reprepare/`, {
      expected_revision: 2,
      idempotency_key: expect.any(String),
      schema_repairs: [{ source_id: "orders", identity_fields: ["order_id"] }],
    });

    fireEvent.click(approve);
    expect(
      await screen.findByText("Source review accepted."),
    ).toBeInTheDocument();
    expect(api.post).toHaveBeenNthCalledWith(
      2,
      `${path}accept/`,
      expect.objectContaining({
        decisions: expect.objectContaining({
          record_reviews: [
            expect.objectContaining({
              audience: { tested_agent: true, participant_ids: [] },
            }),
          ],
        }),
        expected_revision: 3,
        expected_schema_digest: "sha256:new-schema",
        expected_draft_digest: "sha256:repaired",
      }),
    );
    expect(onAccepted).toHaveBeenCalledExactlyOnceWith(serverReview, {
      reviewId: serverReview.id,
      ...api.post.mock.calls[1][1],
    });
  });
  it("keeps a four-page source collapsed until the reviewer asks to see the text", async () => {
    // An accepted review can't be edited, but its text can still be read.
    serverReview = {
      ...repair,
      state: "accepted",
      preview: {
        ...preview,
        sources: [
          {
            pin: { source_id: "doc", role: "reference" },
            filename: "Refund policy.pdf",
            statements: [
              "Refund policy",
              "First business rule.",
              "Second business rule.",
              "Third business rule.",
            ].map((text, i) => ({
              text,
              page: i + 1,
              statement_id: String(i),
            })),
          },
        ],
      },
    };
    mount(null, null, true);
    expect(await screen.findByText("Refund policy.pdf")).toBeVisible();
    expect(screen.queryByText("First business rule.")).toBeNull();
    const show = screen.getByRole("button", { name: "Show text" });
    expect(show).toBeEnabled();
    fireEvent.click(show);
    expect(await screen.findByText("First business rule.")).toBeVisible();
    expect(screen.getByText("4 pages · 11 words")).toBeVisible();
    expect(screen.queryByText("Refund policy")).toBeNull();
  });
  it("shows safe row and column counts before a preselected destination", async () => {
    serverReview = {
      ...repair,
      scope: { ...repair.scope, kind: "environment" },
      preview: {
        ...preview,
        sources: [
          {
            pin: { source_id: "orders", role: "seed_records" },
            filename: "orders.csv",
            row_count: 13,
            field_count: 4,
          },
        ],
        destination_tables: ["customers", "orders"],
        suggested_target_table: "orders",
      },
    };
    mount();
    const counts = await screen.findByText(
      "Structured scenario data · 13 rows · 4 columns",
    );
    const table = screen.getByRole("combobox", { name: "Scenario data table" });
    expect(
      counts.compareDocumentPosition(table) & Node.DOCUMENT_POSITION_FOLLOWING,
    ).toBeTruthy();
    expect(table).toHaveTextContent("orders");
    expect(
      screen.getByRole("button", { name: "Prepare for this table" }),
    ).toBeEnabled();
  });
  it("checkable rules show under Rules, ticked by default, and an unticked one is sent as reference_only", async () => {
    serverReview = { ...repair, state: "ready", preview: policyPreview };
    api.post.mockResolvedValue({
      data: { ...serverReview, state: "accepted", revision: 3 },
    });
    mount(null, null, true);
    const keep = await screen.findByRole("checkbox", { name: orderRule });
    const drop = screen.getByRole("checkbox", { name: refundRule });
    expect(keep).toBeChecked();
    expect(drop).toBeChecked();
    expect(screen.getAllByText("From refund-rules.pdf, page 1")).toHaveLength(
      2,
    );
    fireEvent.click(drop);
    fireEvent.click(
      screen.getByRole("button", { name: "Approve sources and start build" }),
    );
    await screen.findByText("Source review accepted.");
    expect(
      api.post.mock.calls[0][1].decisions.policy_reviews[0].rules.map(
        ({ proposal, decision }) => [proposal.rule_id, decision],
      ),
    ).toEqual([
      [`${policyId}:2:rule`, "accepted"],
      [`${policyId}:4:rule`, "reference_only"],
      [`${policyId}:5:rule`, "reference_only"],
    ]);
  });

  it("an accepted review ticks only accepted_rule_ids", async () => {
    serverReview = {
      ...repair,
      state: "accepted",
      preview: {
        ...policyPreview,
        sources: [
          {
            ...policyPreview.sources[0],
            accepted_rule_ids: [`${policyId}:5:rule`],
          },
        ],
      },
    };
    mount();
    expect(
      await screen.findByRole("checkbox", { name: refundRule }),
    ).toBeChecked();
    expect(screen.getByRole("checkbox", { name: orderRule })).not.toBeChecked();
  });

  it("a withheld file says how many pages it has, not that it has no text", async () => {
    serverReview = {
      ...repair,
      state: "ready",
      preview: {
        ...preview,
        sources: [
          {
            pin: { source_id: "kb", role: "reference" },
            filename: "Returns handbook",
            origin: { kind: "knowledge_base", kb_id: "kb-1" },
            statements: [1, 2, 2].map((page, i) => ({
              page,
              statement_id: `kb:${i}`,
            })),
          },
        ],
      },
    };
    mount();
    expect(
      await screen.findByText("2 pages · text not shown in this review"),
    ).toBeVisible();
    expect(
      screen.getByRole("link", { name: "Returns handbook" }),
    ).toHaveAttribute("href", expect.stringContaining("kb-1"));
  });

  it.each([
    ["Remove personal details and prepare again", "redact_text_sources"],
    ["Replace with synthetic contacts", "synthetic_text_sources"],
  ])("repairs a flagged text source with %s", async (label, field) => {
    serverReview = {
      ...repair,
      preview: {
        ...preview,
        sources: [
          {
            pin: { source_id: "private", role: "reference" },
            filename: "Requests.txt",
          },
        ],
        diagnostics: [
          {
            code: "source_privacy_unresolved",
            severity: "blocking",
            span: { source_id: "private", page: 3 },
          },
        ],
      },
    };
    api.post.mockResolvedValue({
      data: { ...serverReview, state: "ready", revision: 3 },
    });
    mount();
    expect(await screen.findByText(/detected on page 3/)).toBeVisible();
    expect(screen.getByText("Personal data, your choice")).toBeVisible();
    fireEvent.click(screen.getByRole("button", { name: label }));
    await screen.findByRole("button", {
      name: "Approve sources and start build",
    });
    expect(api.post).toHaveBeenCalledWith(`${path}reprepare/`, {
      expected_revision: 2,
      idempotency_key: expect.any(String),
      [field]: ["private"],
    });
  });

  it("groups empty columns and shows the identity suggestion next to its repair", async () => {
    const dates = ["2026-07-14", "2026-07-15", "2026-07-16"];
    serverReview = {
      ...repair,
      preview: {
        ...preview,
        sources: [
          {
            ...preview.sources[0],
            fields: [
              {
                source_field: "order_id",
                inferred_type: "string",
                null_count: 0,
                unique_count: 1,
              },
              ...dates.map((source_field) => ({
                source_field,
                inferred_type: "string",
                null_count: 1,
                unique_count: 0,
              })),
            ],
          },
        ],
        destination_schema: {
          repair_choices: [
            ...dates.map((name) => ({
              source_id: "orders",
              decision: "keep_text",
              fields: [name],
            })),
            {
              source_id: "orders",
              decision: "choose_identity",
              fields: ["order_id", ...dates],
            },
          ],
        },
        diagnostics: dates.map(() => ({
          code: "source_schema_needs_repair",
          severity: "blocking",
          span: { source_id: "orders" },
        })),
      },
    };
    mount();
    expect(await screen.findByText(/3 empty columns/)).toBeVisible();
    expect(screen.getByText(/Suggested: order_id/)).toBeVisible();
    expect(screen.queryByText("source schema needs repair")).toBeNull();
    fireEvent.click(screen.getByRole("button", { name: /Treat all 3/ }));
    expect(screen.getByRole("button", { name: "Prepare again" })).toBeEnabled();
  });
  it("changes the destination of the same ready review without allowing acceptance of old mappings", async () => {
    serverReview = {
      ...repair,
      state: "ready",
      scope: {
        ...repair.scope,
        kind: "environment",
        target_table: "customers",
      },
      preview: {
        ...preview,
        destination_tables: ["customers", "orders"],
        suggested_target_table: "orders",
      },
    };
    api.post.mockResolvedValue({
      data: {
        ...serverReview,
        revision: 3,
        scope: { ...serverReview.scope, target_table: "orders" },
      },
    });
    mount();
    fireEvent.mouseDown(
      await screen.findByRole("combobox", { name: "Scenario data table" }),
    );
    fireEvent.click(screen.getByRole("option", { name: "orders" }));
    expect(
      screen.getByRole("button", { name: /Add \d+ changes?/ }),
    ).toBeDisabled();
    fireEvent.click(
      screen.getByRole("button", { name: "Prepare for this table" }),
    );
    await screen.findByText(/Table: orders/);
    expect(api.post).toHaveBeenCalledWith(`${path}reprepare/`, {
      expected_revision: 2,
      idempotency_key: expect.any(String),
      target_table: "orders",
    });
  });
  it("shows safe reasons beside rejected row numbers", async () => {
    serverReview = {
      ...repair,
      preview: {
        ...preview,
        sources: [
          {
            ...preview.sources[0],
            rejected_rows: [3],
            rejected_count: 1,
            rejected_row_details: [
              { row: 3, reasons: ["Found 3 cells, expected 2."] },
            ],
          },
        ],
      },
    };
    mount();
    expect(
      await screen.findByText(/Row 3: Found 3 cells, expected 2/),
    ).toBeVisible();
  });
  it("blocks a review without a stable ID until the reviewer picks a unique mapped column", async () => {
    serverReview = {
      ...repair,
      state: "ready",
      preview: {
        ...preview,
        sources: [
          {
            ...preview.sources[0],
            row_count: 2,
            fields: [
              {
                source_field: "item",
                inferred_type: "string",
                null_count: 0,
                unique_count: 2,
              },
              {
                source_field: "status",
                inferred_type: "string",
                null_count: 0,
                unique_count: 1,
              },
            ],
          },
        ],
        proposals: {
          field_mappings: ["item", "status"].map((name) => ({
            source_id: "orders",
            mapping: {
              source_field: name,
              target_field: name,
              data_type: "string",
              is_stable_id: false,
            },
          })),
        },
      },
    };
    api.post.mockResolvedValue({
      data: { ...serverReview, state: "accepted", revision: 3 },
    });
    mount();
    const approve = await screen.findByRole("button", {
      name: "Approve sources and start build",
    });
    expect(approve).toBeDisabled();
    expect(
      screen.getByRole("checkbox", { name: "status is a stable ID" }),
    ).toBeDisabled();
    fireEvent.click(
      screen.getByRole("checkbox", { name: "item is a stable ID" }),
    );
    expect(approve).toBeEnabled();
    fireEvent.click(approve);
    await screen.findByText("Source review accepted.");
    expect(
      api.post.mock.calls[0][1].decisions.record_reviews[0].fields[0],
    ).toMatchObject({ source_field: "item", is_stable_id: true });
  });
  it("keeps clipped policy wording as a reference passage without a tick", async () => {
    const text = "The response must contain " + "X".repeat(1974);
    serverReview = {
      ...repair,
      state: "ready",
      preview: {
        ...preview,
        sources: [
          {
            pin: { source_id: "p", role: "policy" },
            filename: "Policy.pdf",
            statements: [
              { statement_id: "s", page: 1, text, text_truncated: true },
            ],
          },
        ],
        proposals: {
          policy_rules: [
            {
              rule_id: "r",
              statement_id: "s",
              status: "supported",
              title: "Responses must include “X”",
            },
          ],
        },
      },
    };
    mount();
    expect(await screen.findByText(`${text}…`)).toBeVisible();
    expect(
      screen.queryByRole("checkbox", { name: "Responses must include “X”" }),
    ).toBeNull();
  });
});
