import {
  act,
  fireEvent,
  render,
  screen,
  waitFor,
} from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { MemoryRouter } from "react-router-dom";
import { beforeEach, describe, expect, it, vi } from "vitest";
import DesignForm from "../DesignForm";
import DesignProgress from "../DesignProgress";

const mocks = vi.hoisted(() => ({
  create: vi.fn(),
  resume: vi.fn(),
  upload: vi.fn(),
  navigate: vi.fn(),
}));

vi.mock("src/api/harness/releases", () => ({
  createHarnessDesign: mocks.create,
  continueHarnessDesign: mocks.resume,
  uploadHarnessDesignDocument: mocks.upload,
  useHarnessReleases: () => ({ data: [] }),
}));
vi.mock("../ReleaseGallery", () => ({
  HostedAgentDialog: () => null,
  UseReleaseDialog: () => null,
}));
vi.mock("src/sections/simulate/environments/hooks/useSimulationAccess", () => ({
  default: () => ({ canEdit: true }),
}));
vi.mock(
  "src/sections/simulate/environments/workspace/sources/KnowledgeBasePicker",
  () => ({
    default: ({ onPick }) => (
      <button
        type="button"
        onClick={() =>
          onPick({
            kb: { id: "kb-1", name: "Policies" },
            whole_collection: true,
          })
        }
      >
        Add Policies knowledge base
      </button>
    ),
  }),
);
vi.mock("react-router-dom", async (original) => ({
  ...(await original()),
  useNavigate: () => mocks.navigate,
}));

function form(template) {
  return render(
    <MemoryRouter>
      <QueryClientProvider client={new QueryClient()}>
        <DesignForm template={template} />
      </QueryClientProvider>
    </MemoryRouter>,
  );
}

const requirements = () =>
  fireEvent.change(screen.getByLabelText("Requirements"), {
    target: { value: "Check refund eligibility using the approved policy." },
  });

const pick = (name) => {
  const file = new File(["data"], name);
  fireEvent.change(screen.getByTestId("build-files"), {
    target: { files: [file] },
  });
  return file;
};

describe("DesignForm", () => {
  beforeEach(() => Object.values(mocks).forEach((mock) => mock.mockReset()));

  it("starts a real design from the canonical template and uploaded document", async () => {
    const documentId = "9e3b1f4a-2c6d-4e8f-a0b1-c2d3e4f5a6b7";
    const suiteId = "17be9639-4157-4b75-9d52-7675b8aa7ecb";
    let finish;
    mocks.upload.mockImplementation((_file, { onProgress }) => {
      onProgress(42);
      return new Promise((resolve) => {
        finish = resolve;
      });
    });
    mocks.create.mockResolvedValue({ job: { job_id: "real-job" } });

    form({ name: "Support desk", suites: [{ id: suiteId, name: "Base" }] });
    requirements();
    const pdf = pick("refund-policy.pdf");
    expect(mocks.upload).toHaveBeenCalledWith(
      pdf,
      expect.objectContaining({
        signal: expect.any(AbortSignal),
        onProgress: expect.any(Function),
      }),
    );
    const submit = screen.getByRole("button", { name: "Customize" });
    expect(submit).toBeDisabled();
    expect(
      screen.getByRole("progressbar", { name: "Uploading refund-policy.pdf" }),
    ).toHaveAttribute("aria-valuenow", "42");
    await act(async () => finish({ id: documentId, name: pdf.name }));
    expect(submit).toBeEnabled();
    fireEvent.click(submit);
    await waitFor(() =>
      expect(mocks.create).toHaveBeenCalledWith({
        name: "Support desk (customized)",
        requirements: "Check refund eligibility using the approved policy.",
        modality: "chat",
        template_suite_id: suiteId,
        source_selection: {
          document_ids: [documentId],
          document_purposes: {},
          knowledge_base: [],
        },
        idempotency_key: expect.any(String),
      }),
    );
    expect(mocks.navigate).toHaveBeenCalledWith(
      "/dashboard/simulate/environments/real-job",
    );
  });

  it("aborts the in-flight request when its file is removed and retries a failed request", async () => {
    mocks.upload.mockImplementationOnce(() => new Promise(() => {}));
    mocks.upload.mockRejectedValueOnce({ statusCode: 503 });
    mocks.upload.mockResolvedValueOnce({ id: "uploaded-id" });
    form();

    pick("cancel.pdf");
    const { signal } = mocks.upload.mock.calls[0][1];
    fireEvent.click(
      screen.getByRole("button", { name: /(?:cancel|remove).*cancel.pdf/i }),
    );
    expect(signal.aborted).toBe(true);
    pick("retry.csv");
    fireEvent.click(
      await screen.findByRole("button", { name: /retry.*retry.csv/i }),
    );
    await waitFor(() => expect(mocks.upload).toHaveBeenCalledTimes(3));
    expect(mocks.upload.mock.calls[1][0]).toBe(mocks.upload.mock.calls[2][0]);
  });

  it("starts scratch design review with a policy upload and Knowledge Base snapshot", async () => {
    const documentId = "76469425-7b3d-40f9-8146-dd2f318345f0";
    mocks.upload.mockResolvedValue({ id: documentId });
    mocks.create.mockResolvedValue({
      job: {
        job_id: "review-job",
        metadata: { source_review: { id: "review-1", state: "ready" } },
      },
    });
    form();
    fireEvent.change(screen.getByLabelText("Name"), {
      target: { value: "Refunds" },
    });
    requirements();
    pick("policy.pdf");
    fireEvent.click(
      await screen.findByRole("checkbox", { name: "Use policy.pdf as policy" }),
    );
    fireEvent.click(
      screen.getByRole("button", { name: "Select Knowledge Base" }),
    );
    fireEvent.click(
      screen.getByRole("button", { name: "Add Policies knowledge base" }),
    );
    fireEvent.click(screen.getByRole("button", { name: "Design environment" }));
    await waitFor(() =>
      expect(mocks.create).toHaveBeenCalledWith(
        expect.objectContaining({
          source_selection: {
            document_ids: [documentId],
            document_purposes: { [documentId]: "policy" },
            knowledge_base: [
              { kind: "knowledge_base", kb_id: "kb-1", whole_collection: true },
            ],
          },
        }),
      ),
    );
    expect(mocks.navigate).toHaveBeenCalledWith(
      "/dashboard/simulate/environments/review-job",
    );
  });

  it("continues a server-paused design and opens the next hosted job", async () => {
    mocks.resume.mockResolvedValue({ job: { job_id: "resumed-job" } });
    render(
      <MemoryRouter>
        <QueryClientProvider client={new QueryClient()}>
          <DesignProgress
            job={{ job_id: "waiting-job", metadata: { design: {} } }}
            stage="failed"
            failure={{ code: "design_waiting" }}
          />
        </QueryClientProvider>
      </MemoryRouter>,
    );
    fireEvent.click(screen.getByRole("button", { name: "Continue" }));
    await waitFor(() =>
      expect(mocks.resume).toHaveBeenCalledWith("waiting-job", {
        idempotency_key: expect.any(String),
      }),
    );
    expect(mocks.navigate).toHaveBeenCalledWith(
      "/dashboard/simulate/environments/resumed-job",
    );
  });
});
