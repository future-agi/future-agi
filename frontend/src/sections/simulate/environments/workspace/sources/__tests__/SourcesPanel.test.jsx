import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { MemoryRouter } from "react-router-dom";
import { describe, expect, it, vi } from "vitest";
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
      }),
    );
    rendering.unmount();
    mount();
    expect(await screen.findByText("Draft")).toBeInTheDocument();
  });

  it("passes a Knowledge Base page selection as explicit file IDs", async () => {
    api.get.mockImplementation(async (path) => ({
      data: path.endsWith("/capabilities/") ? basis : { sources: [] },
    }));
    api.post.mockReset().mockResolvedValue({ data: prepared });
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
});
