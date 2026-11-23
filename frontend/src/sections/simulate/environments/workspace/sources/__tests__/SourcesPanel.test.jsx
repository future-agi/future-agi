import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { MemoryRouter } from "react-router-dom";
import { beforeEach, describe, expect, it, vi } from "vitest";
import SourcesPanel from "../SourcesPanel";

const api = vi.hoisted(() => ({ get: vi.fn(), post: vi.fn() }));
vi.mock("src/utils/axios", () => ({ default: api }));
vi.mock("src/api/knowledge-base/files", () => ({
  useKnowledgeBaseList: () => ({ data: [], isLoading: false }),
}));
vi.mock("../../../hooks/useSimulationAccess", () => ({
  default: () => ({ canEdit: true }),
}));

function mount(state) {
  return render(
    <MemoryRouter
      initialEntries={[{ pathname: "/env", search: "?tab=world", state }]}
    >
      <QueryClientProvider
        client={
          new QueryClient({ defaultOptions: { queries: { retry: false } } })
        }
      >
        <SourcesPanel env={{ id: "env-1" }} />
      </QueryClientProvider>
    </MemoryRouter>,
  );
}

const limits = {
  enabled: true,
  formats: ["txt"],
  max_files: 100,
  max_file_bytes: 26214400,
  max_total_bytes: 209715200,
};
const basis = {
  environment_version_id: "env-v1",
  draft: null,
  upload: limits,
  knowledge_base: limits,
};
const prepared = {
  import_ref: "server-signed",
  revision: 2,
  state: "ready",
  draft_digest: "sha256:prepared",
  capabilities: basis,
  draft: {
    draft_digest: "sha256:prepared",
    sources: [
      {
        filename: "reference.txt",
        pin: { source_id: "reference", role: "reference" },
      },
    ],
  },
};

const ambiguous = {
  ...prepared,
  import_ref: "unstaged-review",
  state: "needs_repair",
  draft_digest: "",
  draft: {
    destination_tables: ["customers", "departments"],
    diagnostics: [{ code: "source_schema_ambiguous", severity: "blocking" }],
  },
};

const reviewStorageKey = "harness-environment-source-review:env-1";
const serverReview = {
  id: prepared.import_ref,
  revision: prepared.revision,
  state: prepared.state,
  draft_digest: prepared.draft_digest,
  preview: prepared.draft,
  scope: { kind: "environment", id: "env-1", ...basis },
};

beforeEach(() => {
  sessionStorage.clear();
  api.get.mockReset().mockImplementation(async (path) => ({
    data: path.endsWith("/capabilities/") ? basis : { sources: [] },
  }));
  api.post.mockReset();
});

describe("SourcesPanel", () => {
  it("prepares an upload, accepts the signed review, and reloads the persisted draft", async () => {
    let accepted = false;
    api.get.mockImplementation(async (path) => ({
      data: path.endsWith("/capabilities/")
        ? basis
        : {
            draft: accepted,
            sources: accepted
              ? [{ source_id: "reference", purpose: "reference" }]
              : [],
          },
    }));
    api.post.mockImplementation(async (path) => {
      if (path.endsWith("/accept/")) {
        accepted = true;
        return {
          data: {
            draft: { id: "draft", revision: 1 },
            validation: { status: "structural_only" },
          },
        };
      }
      return { data: prepared };
    });
    const rendering = mount();
    await waitFor(() =>
      expect(screen.getByRole("button", { name: "Upload file" })).toBeEnabled(),
    );
    fireEvent.change(screen.getByLabelText("Environment source files"), {
      target: { files: [new File(["An approved guide"], "reference.txt")] },
    });
    expect(await screen.findByText("reference.txt")).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "Add to draft" }));
    expect(
      await screen.findByText(/Source added to the Environment draft/),
    ).toBeInTheDocument();
    expect(api.post).toHaveBeenLastCalledWith(
      "/simulate/api/harness-environments/env-1/sources/imports/accept/",
      expect.objectContaining({
        import_ref: "server-signed",
        expected_environment_version_id: "env-v1",
        expected_draft: null,
        expected_draft_digest: "sha256:prepared",
        expected_revision: 2,
      }),
    );
    rendering.unmount();
    mount();
    expect(await screen.findByText("Draft")).toBeInTheDocument();
  });

  it("passes a Knowledge Base page selection as explicit file IDs", async () => {
    api.post.mockResolvedValue({ data: prepared });
    mount({
      environmentSourceSelection: {
        kb_id: "authorized-kb",
        file_ids: ["ready-file"],
      },
    });
    expect(await screen.findByText("reference.txt")).toBeInTheDocument();
    expect(api.post).toHaveBeenCalledExactlyOnceWith(
      "/simulate/api/harness-environments/env-1/sources/imports/prepare/",
      {
        expected_environment_version_id: "env-v1",
        expected_draft: null,
        selection: {
          kind: "knowledge_base",
          kb_id: "authorized-kb",
          file_ids: ["ready-file"],
        },
      },
    );
  });

  it.each(["upload", "Knowledge Base"])(
    "repairs a durable %s after reloading and without sending the source again",
    async (method) => {
      const selection = { kb_id: "authorized-kb", file_ids: ["ready-file"] };
      const reviewPath =
        "/simulate/api/harness-source-reviews/unstaged-review/";
      api.get.mockImplementation(async (path) => ({
        data: path.endsWith("/capabilities/")
          ? basis
          : path === reviewPath
            ? {
                ...serverReview,
                id: ambiguous.import_ref,
                state: ambiguous.state,
                draft_digest: null,
                preview: ambiguous.draft,
              }
            : { sources: [] },
      }));
      api.post
        .mockResolvedValueOnce({ data: ambiguous })
        .mockRejectedValueOnce(new Error("Source preparation timed out"))
        .mockResolvedValueOnce({
          data: { ...serverReview, id: ambiguous.import_ref, revision: 3 },
        });
      const isUpload = method === "upload";
      const file = new File(["name\nApproved owner"], "customers.csv");
      const rendering = mount(
        isUpload ? undefined : { environmentSourceSelection: selection },
      );
      if (isUpload) {
        await waitFor(() =>
          expect(
            screen.getByRole("button", { name: "Upload file" }),
          ).toBeEnabled(),
        );
        fireEvent.change(screen.getByLabelText("Environment source files"), {
          target: { files: [file] },
        });
      }
      expect(
        await screen.findByRole("combobox", { name: "Destination table" }),
      ).toBeInTheDocument();
      expect(sessionStorage.getItem(reviewStorageKey)).toBe(
        ambiguous.import_ref,
      );
      expect(api.post).toHaveBeenCalledTimes(1);
      rendering.unmount();

      mount();
      const select = await screen.findByRole("combobox", {
        name: "Destination table",
      });
      const prepare = screen.getByRole("button", {
        name: "Prepare for this table",
      });
      expect(prepare).toBeDisabled();
      expect(screen.queryByRole("button", { name: "Add to draft" })).toBeNull();
      fireEvent.mouseDown(select);
      fireEvent.click(screen.getByRole("option", { name: "customers" }));
      fireEvent.click(prepare);
      expect(
        await screen.findByText("Source preparation timed out"),
      ).toBeInTheDocument();
      expect(
        screen.getByRole("combobox", { name: "Destination table" }),
      ).toHaveTextContent("customers");
      fireEvent.click(prepare);
      expect(await screen.findByText("reference.txt")).toBeInTheDocument();
      expect(
        screen.getByRole("button", { name: "Add to draft" }),
      ).toBeEnabled();

      expect(api.get).toHaveBeenCalledWith(reviewPath);
      const body = {
        expected_revision: 2,
        target_table: "customers",
        idempotency_key: "unstaged-review:2:0",
      };
      expect(api.post).toHaveBeenCalledTimes(3);
      expect(api.post).toHaveBeenNthCalledWith(
        2,
        `${reviewPath}reprepare/`,
        body,
      );
      expect(api.post).toHaveBeenNthCalledWith(
        3,
        `${reviewPath}reprepare/`,
        body,
      );
      api.post.mockResolvedValueOnce({
        data: { validation: { status: "structural_only" } },
      });
      fireEvent.click(screen.getByRole("button", { name: "Add to draft" }));
      expect(
        await screen.findByText(/Source added to the Environment draft/),
      ).toBeInTheDocument();
      expect(api.post).toHaveBeenLastCalledWith(
        "/simulate/api/harness-environments/env-1/sources/imports/accept/",
        expect.objectContaining({
          import_ref: ambiguous.import_ref,
          expected_revision: 3,
          expected_draft_digest: prepared.draft_digest,
        }),
      );
    },
  );

  it("reopens an unaccepted review from the server after reloading", async () => {
    api.post.mockResolvedValue({ data: prepared });
    api.get.mockImplementation(async (path) => ({
      data: path.endsWith("/capabilities/")
        ? basis
        : path.startsWith("/simulate/api/harness-source-reviews/")
          ? serverReview
          : { sources: [] },
    }));
    const rendering = mount();
    await waitFor(() =>
      expect(screen.getByRole("button", { name: "Upload file" })).toBeEnabled(),
    );
    fireEvent.change(screen.getByLabelText("Environment source files"), {
      target: { files: [new File(["A guide"], "reference.txt")] },
    });
    expect(await screen.findByText("reference.txt")).toBeInTheDocument();
    expect(sessionStorage.getItem(reviewStorageKey)).toBe(prepared.import_ref);
    rendering.unmount();

    mount();
    expect(await screen.findByText("reference.txt")).toBeInTheDocument();
    expect(api.get).toHaveBeenCalledWith(
      "/simulate/api/harness-source-reviews/server-signed/",
    );
    expect(api.post).toHaveBeenCalledTimes(1);
    fireEvent.click(screen.getByRole("button", { name: "Cancel" }));
    expect(sessionStorage.getItem(reviewStorageKey)).toBeNull();
  });

  it("blocks acceptance if a reloaded review belongs to an older Environment version", async () => {
    sessionStorage.setItem(reviewStorageKey, prepared.import_ref);
    api.get.mockImplementation(async (path) => ({
      data: path.endsWith("/capabilities/")
        ? basis
        : path.startsWith("/simulate/api/harness-source-reviews/")
          ? {
              ...serverReview,
              scope: {
                ...serverReview.scope,
                environment_version_id: "env-old",
              },
            }
          : { sources: [] },
    }));
    mount();
    expect(
      await screen.findByText(/The Environment changed/),
    ).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Add to draft" })).toBeDisabled();
    expect(api.post).not.toHaveBeenCalled();
  });
});
