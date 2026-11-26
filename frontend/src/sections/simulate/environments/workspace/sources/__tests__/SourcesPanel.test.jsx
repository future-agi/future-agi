import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { MemoryRouter } from "react-router-dom";
import { beforeEach, describe, expect, it, vi } from "vitest";
import SourcesSection from "../SourcesSection";
import WorkspaceDraftBar from "../../drafts/WorkspaceDraftBar";
import { validateContractedResponse } from "src/api/contracts/openapi-contract";

const api = vi.hoisted(() => ({
  get: vi.fn(),
  post: vi.fn(),
  upload: vi.fn(),
}));
vi.mock("src/utils/axios", () => ({ default: api }));
vi.mock("src/api/harness/releases", async (original) => ({
  ...(await original()),
  uploadHarnessDesignDocument: api.upload,
}));
vi.mock("../../../hooks/useSimulationAccess", () => ({
  default: () => ({ canEdit: true }),
}));
vi.mock("../KnowledgeBasePicker", () => ({
  default: ({ onPick }) => (
    <button
      type="button"
      onClick={() =>
        onPick({
          kb: { id: "kb-1", name: "Guides" },
          files: [{ id: "ready-file" }],
        })
      }
    >
      Add a ready KB file
    </button>
  ),
}));

function mount(state, withDraft = false, inline = false) {
  return render(
    <MemoryRouter
      initialEntries={[{ pathname: "/env", search: "?tab=world", state }]}
    >
      <QueryClientProvider
        client={
          new QueryClient({
            defaultOptions: {
              queries: { retry: false },
            },
          })
        }
      >
        {(inline || withDraft) && (
          <WorkspaceDraftBar
            env={{ id: "env-1", versioning: { enabled: true } }}
          />
        )}
        <SourcesSection env={{ id: "env-1" }} />
      </QueryClientProvider>
    </MemoryRouter>,
  );
}

const indexPath = "/simulate/api/harness-source-reviews/";
const reviewPath = `${indexPath}review-1/`;
const storageKey = "harness-environment-source-review:env-1";
const basis = {
  environment_version_id: "env-v1",
  draft: { id: null, revision: 0 },
};
const scope = {
  kind: "environment",
  id: "env-1",
  schema_digest: "sha256:schema",
  ...basis,
};
const ready = {
  id: "review-1",
  state: "ready",
  revision: 3,
  scope,
  draft_digest: "sha256:prepared",
  preview: {
    draft_digest: "sha256:prepared",
    sources: [
      {
        filename: "orders.csv",
        pin: { source_id: "orders", role: "seed_records" },
      },
      {
        filename: "policy.pdf",
        pin: { source_id: "policy", role: "policy" },
        statements: [
          {
            statement_id: "statement-1",
            page: 2,
            text: "Refunds are allowed for 15 days.",
          },
        ],
      },
    ],
    proposals: {
      field_mappings: [
        {
          source_id: "orders",
          mapping: {
            source_field: "email",
            target_field: "email",
            personal_data: true,
            is_stable_id: true,
          },
        },
      ],
      policy_rules: [
        {
          rule_id: "rule-1",
          statement_id: "statement-1",
          kind: "eligibility",
          check_type: "state_predicate",
          status: "supported",
        },
      ],
    },
  },
};
const needsTable = {
  ...ready,
  state: "needs_repair",
  revision: 2,
  draft_digest: null,
  preview: { destination_tables: ["orders", "departments"] },
};
const reading = {
  ...ready,
  state: "reading",
  revision: 1,
  draft_digest: null,
  preview: {},
};
let serverReview;
let currentBasis;
let sources;

beforeEach(() => {
  sessionStorage.clear();
  serverReview = null;
  currentBasis = basis;
  sources = [];
  api.upload.mockReset();
  api.post.mockReset();
  api.get.mockReset().mockImplementation(async (path) => ({
    data: path.endsWith("/drafts/review/")
      ? {
          drafts: { environment: { id: "draft-1", revision: 1 } },
          changes: [
            {
              id: "file:policy",
              kind: "file",
              key: "policy.pdf",
              piece: "environment",
              action: "added",
              summary: "Add policy.pdf",
              decision: "accepted",
              sources: [],
              fields: [],
              patch: [],
              depends_on: [],
              revalidate: [],
            },
          ],
        }
      : path.endsWith("/capabilities/")
        ? currentBasis
        : path === reviewPath
          ? serverReview
          : { draft: Boolean(sources.length), sources },
  }));
});

const pickFiles = (files) =>
  fireEvent.change(screen.getByTestId("build-files"), { target: { files } });

describe("SourcesSection", () => {
  it("validates the scenario generation envelope and its citation array", () => {
    const reviewId = "3646e7d3-b8fe-46f4-aab9-fd666246f879";
    const generation = {
      id: "c5fc6a4c-5cb0-479b-8c4e-ca734315ea2b",
      review_id: reviewId,
      source_digest: "sha256:snapshot",
      error: null,
    };
    const expectValid = (data) => {
      const validation = validateContractedResponse({
        config: {
          method: "get",
          url: `${indexPath}${reviewId}/scenario-generation/`,
        },
        status: 200,
        data,
      });
      expect(
        validation,
        JSON.stringify(validation.error?.details),
      ).toMatchObject({
        ok: true,
      });
      expect(validation.skipped).toBeUndefined();
    };
    expectValid({
      ...generation,
      state: "queued",
      generated_scenarios: [],
      suite_draft: null,
    });
    expectValid({
      ...generation,
      state: "completed",
      generated_scenarios: [
        {
          scenario_key: "refund-window",
          name: "Refund review",
          citations: [{ source_id: "approved-source", page: 2 }],
        },
      ],
      suite_draft: { id: "47afcb70-ec98-4f5e-9be6-aa36dcc2cb50", revision: 1 },
    });
  });

  it("automatically prepares uploaded files and keeps the same request on a transport retry", async () => {
    api.upload.mockImplementation(async (file, { onProgress }) => {
      onProgress(42);
      return { id: `uploaded-${file.name}` };
    });
    api.post
      .mockRejectedValueOnce(new Error("The request timed out"))
      .mockImplementationOnce(async () => {
        serverReview = ready;
        return { data: ready };
      })
      .mockImplementationOnce(async () => {
        serverReview = { ...ready, state: "accepted", revision: 4 };
        sources = [
          {
            source_id: "orders",
            filename: "orders.csv",
            unsaved: true,
            purpose: "seed_records",
          },
        ];
        return { data: serverReview };
      });
    const view = mount();
    const add = screen.getByRole("button", { name: "Add source" });
    await waitFor(() => expect(add).toBeEnabled());
    fireEvent.click(add);
    fireEvent.click(
      screen.getByRole("checkbox", { name: "Use PDFs as policy rules" }),
    );
    fireEvent.click(screen.getByRole("menuitem", { name: /Upload files/ }));
    const csv = new File(["email\nalice@example.test"], "orders.csv");
    const policy = new File(["15-day policy"], "policy.pdf");
    pickFiles([csv, policy]);
    expect(await screen.findByText("The request timed out")).toBeVisible();
    fireEvent.click(screen.getByRole("button", { name: "Try again" }));
    expect(
      await screen.findByRole("heading", { name: "Review sources" }),
    ).toBeInTheDocument();

    const [, body] = api.post.mock.calls[0];
    expect(body).toEqual({
      scope: {
        kind: "environment",
        id: "env-1",
        environment_version_id: "env-v1",
        expected_draft: basis.draft,
      },
      document_ids: ["uploaded-orders.csv", "uploaded-policy.pdf"],
      document_purposes: { "uploaded-policy.pdf": "policy" },
      knowledge_base: [],
      idempotency_key: expect.any(String),
    });
    expect(api.post).toHaveBeenNthCalledWith(2, indexPath, body);
    expect(api.upload).toHaveBeenNthCalledWith(
      1,
      csv,
      expect.objectContaining({ signal: expect.any(AbortSignal) }),
    );
    expect(api.upload).toHaveBeenNthCalledWith(2, policy, expect.anything());

    view.unmount();
    mount();
    const rule = await screen.findByRole("checkbox", {
      name: /Refunds are allowed/,
    });
    fireEvent.click(rule);
    fireEvent.click(screen.getByRole("button", { name: "Add to draft" }));
    expect(
      await screen.findByText("Source review accepted."),
    ).toBeInTheDocument();
    expect(await screen.findByText("Draft")).toBeInTheDocument();
    expect(api.post).toHaveBeenNthCalledWith(
      3,
      `${reviewPath}accept/`,
      expect.objectContaining({
        expected_revision: 3,
        expected_draft_digest: "sha256:prepared",
        expected_schema_digest: "sha256:schema",
        idempotency_key: expect.any(String),
        decisions: expect.objectContaining({
          record_reviews: [
            expect.objectContaining({
              personal_data_mode: "synthetic_replace",
            }),
          ],
          policy_reviews: [
            expect.objectContaining({
              rules: [expect.objectContaining({ decision: "accepted" })],
            }),
          ],
        }),
      }),
    );
    expect(api.post).toHaveBeenCalledTimes(3);
  });

  it("polls a reading review after reload and cancels with the server revision", async () => {
    sessionStorage.setItem(storageKey, reading.id);
    serverReview = reading;
    api.post.mockImplementation(async () => {
      serverReview = { ...needsTable, state: "cancelled", revision: 3 };
      return { data: serverReview };
    });
    mount();
    expect(await screen.findByText("Reading sources…")).toBeInTheDocument();
    serverReview = needsTable;
    expect(
      await screen.findByRole(
        "combobox",
        { name: "Scenario data table" },
        { timeout: 3000 },
      ),
    ).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "Cancel review" }));
    await waitFor(() => expect(sessionStorage.getItem(storageKey)).toBeNull());
    expect(api.post).toHaveBeenCalledExactlyOnceWith(`${reviewPath}cancel/`, {
      expected_revision: 2,
      expected_draft_digest: null,
    });
  });

  it("reprepares the same review after reload without resending files", async () => {
    sessionStorage.setItem(storageKey, needsTable.id);
    serverReview = needsTable;
    api.post
      .mockRejectedValueOnce(new Error("The request timed out"))
      .mockImplementationOnce(async () => {
        serverReview = ready;
        return { data: ready };
      });
    mount();
    const table = await screen.findByRole("combobox", {
      name: "Scenario data table",
    });
    const prepare = screen.getByRole("button", {
      name: "Prepare for this table",
    });
    expect(prepare).toBeDisabled();
    fireEvent.mouseDown(table);
    fireEvent.click(screen.getByRole("option", { name: "orders" }));
    fireEvent.click(prepare);
    expect(
      await screen.findByText("The request timed out"),
    ).toBeInTheDocument();
    fireEvent.click(prepare);
    expect(await screen.findByText("orders.csv")).toBeInTheDocument();
    const repair = {
      expected_revision: 2,
      target_table: "orders",
      idempotency_key: expect.any(String),
    };
    expect(api.post).toHaveBeenNthCalledWith(
      1,
      `${reviewPath}reprepare/`,
      repair,
    );
    expect(api.post.mock.calls[0]).toEqual(api.post.mock.calls[1]);
    expect(api.upload).not.toHaveBeenCalled();
  });

  it("automatically prepares a selection from the Knowledge Base page", async () => {
    api.post.mockImplementation(async () => {
      serverReview = ready;
      return { data: ready };
    });
    mount({
      environmentSourceSelection: { kb_id: "kb-1", file_ids: ["ready-file"] },
    });
    await screen.findByRole("heading", { name: "Review sources" });
    expect(api.post).toHaveBeenCalledExactlyOnceWith(
      indexPath,
      expect.objectContaining({
        knowledge_base: [
          { kind: "knowledge_base", kb_id: "kb-1", file_ids: ["ready-file"] },
        ],
      }),
    );
  });

  it("opens a saved file and shows only that file's accepted review and citations", async () => {
    sources = [
      {
        source_id: "policy",
        filename: "policy.pdf",
        purpose: "policy",
        review_id: "review-1",
        unsaved: false,
      },
    ];
    serverReview = {
      ...ready,
      state: "accepted",
      scenario_generation: {
        state: "completed",
        generated_scenarios: [
          {
            scenario_key: "refund",
            name: "Refund request at day 15",
            citations: [{ source_id: "policy", page: 2 }],
          },
          {
            scenario_key: "orders",
            name: "Different source case",
            citations: [{ source_id: "orders", row: 1 }],
          },
        ],
      },
    };
    const fallback = api.get.getMockImplementation();
    api.get.mockImplementation((path) =>
      path.endsWith("/scenario-generation/")
        ? Promise.resolve({ data: serverReview.scenario_generation })
        : fallback(path),
    );
    mount();
    fireEvent.click(await screen.findByRole("button", { name: "policy.pdf" }));
    expect(
      await screen.findByText("Refunds are allowed for 15 days."),
    ).toBeVisible();
    fireEvent.click(screen.getByRole("tab", { name: "Used by" }));
    expect(await screen.findByText("Refund request at day 15")).toBeVisible();
    expect(screen.queryByText("Different source case")).toBeNull();
    expect(screen.getByText("Page 2")).toBeVisible();
  });

  it("displays KB passage locations and the original link without rendering cached excerpts", async () => {
    const origin = { kind: "knowledge_base", kb_id: "kb-1" };
    sources = [
      {
        source_id: "policy",
        filename: "policy.pdf",
        purpose: "reference",
        review_id: "review-1",
        unsaved: false,
        origin,
      },
    ];
    serverReview = {
      ...ready,
      state: "accepted",
      preview: {
        ...ready.preview,
        sources: [
          {
            ...ready.preview.sources[1],
            origin,
            statements: [
              { page: 1, statement_id: "one", text: "Private original quote" },
              { page: 1, statement_id: "two" },
            ],
          },
        ],
      },
    };
    mount();
    fireEvent.click(await screen.findByRole("button", { name: "policy.pdf" }));
    expect(await screen.findByText("Page 1 · 2 passages")).toBeVisible();
    expect(
      screen.getByRole("link", { name: "Read the original in Knowledge Base" }),
    ).toHaveAttribute("href", "/dashboard/knowledge/kb-1");
    expect(screen.queryByText("Private original quote")).toBeNull();
  });
  it("shows the accepted mapping and actual rejected row reason in file details", async () => {
    const file = {
      source_id: "orders",
      filename: "orders.csv",
      purpose: "seed_records",
      review_id: "review-1",
      unsaved: false,
      visibility: "agent_visible",
      updated_at: "2026-11-26T01:00:00Z",
      rejected_count: 1,
    };
    sources = [file];
    serverReview = {
      ...ready,
      state: "accepted",
      preview: {
        ...ready.preview,
        sources: [
          {
            ...ready.preview.sources[0],
            row_count: 2,
            fields: [{ source_field: "order_id", inferred_type: "string" }],
            accepted_mappings: [
              {
                source_field: "order_id",
                target_field: "order_number",
                data_type: "string",
                is_stable_id: true,
              },
            ],
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
    fireEvent.click(await screen.findByRole("button", { name: "orders.csv" }));
    expect(await screen.findByText(/Found 3 cells, expected 2/)).toBeVisible();
    fireEvent.click(screen.getByRole("tab", { name: "Mapping" }));
    expect(screen.getByText("Accepted field mappings")).toBeVisible();
    expect(screen.getByText(/order_id → order_number/)).toBeVisible();
    expect(screen.queryByText(/email → email/)).toBeNull();
  });

  it("opens a build-from-scratch checkpoint through its owning design without polling environment generation", async () => {
    sources = [
      {
        source_id: "policy",
        filename: "policy.pdf",
        purpose: "reference",
        review_id: "review-1",
        review_owner_id: "design-1",
        used_by: [
          {
            scenario_key: "return-filing",
            name: "File a return",
            citations: [{ source_id: "policy", page: 2 }],
          },
        ],
        unsaved: false,
      },
    ];
    serverReview = {
      ...ready,
      state: "accepted",
      scope: { id: "design-1", kind: "design" },
    };
    mount();
    fireEvent.click(await screen.findByRole("button", { name: "policy.pdf" }));
    expect(
      await screen.findByText("Refunds are allowed for 15 days."),
    ).toBeVisible();
    fireEvent.click(screen.getByRole("tab", { name: "Used by" }));
    expect(screen.getByText("File a return")).toBeVisible();
    expect(
      api.get.mock.calls.some(([path]) =>
        path.endsWith("/scenario-generation/"),
      ),
    ).toBe(false);
  });

  it("recovers a failed generation after reload, keeps Save held and retries the existing review", async () => {
    sources = [
      {
        source_id: "policy",
        filename: "policy.pdf",
        purpose: "policy",
        review_id: "review-1",
        unsaved: true,
      },
    ];
    let generation = {
      id: "generation-1",
      state: "failed",
      error: "source_writer_failed",
    };
    serverReview = {
      ...ready,
      state: "accepted",
      receipt: { draft: { id: "draft-1", revision: 1 } },
      scenario_generation: generation,
    };
    const fallback = api.get.getMockImplementation();
    api.get.mockImplementation((path) => {
      if (path.endsWith("/scenario-generation/"))
        return Promise.resolve({ data: generation });
      if (path.endsWith("/drafts/"))
        return Promise.resolve({
          data: {
            environment: { id: "draft-1", parent_id: "env-v1", revision: 1 },
            suite: null,
          },
        });
      if (path.endsWith("/sources/"))
        return Promise.resolve({
          data: {
            sources,
            draft: true,
            scenario_generation: generation,
            generation_review_id: "review-1",
          },
        });
      return fallback(path);
    });
    api.post.mockImplementation(async () => {
      generation = { ...generation, state: "queued", error: null };
      return { data: generation };
    });
    mount(undefined, false, true);
    fireEvent.click(
      await screen.findByRole("button", { name: "Review and save" }),
    );
    expect(
      await screen.findByRole("button", { name: /^Save version\./ }),
    ).toHaveAttribute("aria-disabled", "true");
    expect(screen.getByText("Sources added")).toBeVisible();
    fireEvent.click(screen.getByRole("button", { name: "Close", exact: true }));
    fireEvent.click(
      await screen.findByRole("button", { name: "Review sources" }),
    );
    expect(
      await screen.findByText(/scenario writer could not finish/),
    ).toBeVisible();
    fireEvent.click(screen.getByRole("button", { name: "Retry generation" }));
    await waitFor(() =>
      expect(api.post).toHaveBeenCalledExactlyOnceWith(
        reviewPath + "scenario-generation/",
        {},
      ),
    );
  });

  it("explains why records without a matching field cannot be added", async () => {
    sessionStorage.setItem(storageKey, ready.id);
    serverReview = {
      ...ready,
      preview: {
        ...ready.preview,
        proposals: { ...ready.preview.proposals, field_mappings: [] },
      },
    };
    mount();
    expect(
      await screen.findByText(/No fields in orders.csv match/),
    ).toBeVisible();
    expect(screen.getByRole("button", { name: "Add to draft" })).toBeDisabled();
    expect(api.post).not.toHaveBeenCalled();
  });

  it("blocks an older Environment basis and permits cancelling the stale review", async () => {
    sessionStorage.setItem(storageKey, ready.id);
    serverReview = ready;
    currentBasis = { ...basis, environment_version_id: "env-v2" };
    mount();
    expect(
      await screen.findByText(/The Environment changed/),
    ).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Add to draft" })).toBeDisabled();
    expect(screen.getByRole("button", { name: "Cancel review" })).toBeEnabled();
    expect(api.post).not.toHaveBeenCalled();
  });

  it("holds Save through generation and shows grounded scenarios with source citations", async () => {
    sessionStorage.setItem(storageKey, ready.id);
    let generation = { id: "generation-1", state: "queued" };
    serverReview = {
      ...ready,
      state: "accepted",
      receipt: { draft: { id: "draft-1", revision: 1 } },
      scenario_generation: generation,
    };
    const fallback = api.get.getMockImplementation();
    api.get.mockImplementation(async (path) => {
      if (path === `${reviewPath}scenario-generation/`)
        return { data: generation };
      if (path.endsWith("/drafts/")) {
        return {
          data: {
            environment: { id: "draft-1", parent_id: "env-v1", revision: 1 },
            suite:
              generation.state === "completed"
                ? { id: "suite-1", parent_id: "suite-v1", revision: 1 }
                : null,
          },
        };
      }
      return fallback(path);
    });
    mount(undefined, true);
    expect(
      await screen.findByText("Scenario generation is queued."),
    ).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "Close", exact: true }));
    fireEvent.click(
      await screen.findByRole("button", { name: "Review and save" }),
    );
    expect(
      await screen.findByRole("button", { name: /^Save version\./ }),
    ).toHaveAttribute("aria-disabled", "true");

    generation = {
      ...generation,
      state: "completed",
      suite_draft: { id: "suite-1", revision: 1 },
      generated_scenarios: [
        {
          scenario_key: "refund-window",
          name: "Refund within the approved 15-day window",
          citations: [{ source_id: "policy", page: 2 }],
        },
      ],
    };
    expect(
      await within(screen.getByRole("dialog", { name: /review changes/i })).findByText(
        "Refund within the approved 15-day window",
        {},
        { timeout: 3000 },
      ),
    ).toBeInTheDocument();
    await waitFor(() =>
      expect(
        screen.getByRole("button", { name: "Save version", exact: true }),
      ).not.toHaveAttribute("aria-disabled"),
    );
    fireEvent.click(screen.getByRole("button", { name: "Close", exact: true }));
    fireEvent.click(
      await screen.findByRole("button", { name: "Review sources" }),
    );
    expect(await screen.findByText("policy.pdf · Page 2")).toBeInTheDocument();
    expect(api.post).not.toHaveBeenCalled();
  });
});
