import { beforeEach, describe, expect, it, vi } from "vitest";
import { fireEvent, render, screen, waitFor } from "src/utils/test-utils";
import BuildEnvironmentDialog from "../BuildEnvironmentDialog";

const mockNavigate = vi.fn();
const mockSnackbar = vi.fn();
const mockCreateJob = vi.fn();
const preflight = { current: {} };

vi.mock("react-router-dom", async (importOriginal) => ({
  ...(await importOriginal()),
  useNavigate: () => mockNavigate,
}));

vi.mock("notistack", async (importOriginal) => ({
  ...(await importOriginal()),
  useSnackbar: () => ({ enqueueSnackbar: mockSnackbar }),
}));

vi.mock("src/api/agent-definition/agent-definition-version", () => ({
  useAgentDefinitionVersions: () => ({
    data: {
      pages: [
        {
          results: [
            {
              id: "v1",
              version_number: 1,
              version_name_display: "v1",
              is_active: true,
              preflight_status: "passed",
              source: { kind: "github", repository: "acme/agent" },
            },
          ],
        },
      ],
    },
  }),
}));

vi.mock("src/api/agents/agentDetail", async (importOriginal) => ({
  ...(await importOriginal()),
  useAgentVersion: () => ({ data: undefined }),
  useBuildPreflight: () => preflight.current,
}));

vi.mock("src/api/harness/harness", () => ({
  createHarnessJob: (...args) => mockCreateJob(...args),
  harnessIdempotencyKey: () => "key-1",
}));

const AGENT = { id: "a1", name: "Support", activeVersionId: "v1" };

const renderDialog = (onClose = vi.fn()) => {
  render(<BuildEnvironmentDialog agent={AGENT} open onClose={onClose} />);
  return onClose;
};

const buildButton = () =>
  screen.getByRole("button", { name: "Build environment" });

beforeEach(() => {
  mockNavigate.mockReset();
  mockSnackbar.mockReset();
  mockCreateJob.mockReset();
});

describe("BuildEnvironmentDialog", () => {
  it("won't build when the preflight finds a problem", () => {
    preflight.current = {
      data: {
        ok: false,
        checks: [
          { name: "code", status: "failed", message: "No code is stored" },
        ],
      },
    };
    renderDialog();
    expect(screen.getByText("No code is stored")).toBeInTheDocument();
    expect(screen.getByTestId("build-env-reason")).toHaveTextContent(
      "Fix what the preflight found first",
    );
    expect(buildButton()).toBeDisabled();
  });

  it("shows why the preflight failed and retries it", () => {
    const refetch = vi.fn();
    preflight.current = {
      isError: true,
      error: { response: { data: { detail: "GitHub is not reachable" } } },
      refetch,
    };
    renderDialog();
    expect(screen.getByTestId("build-env-reason")).toHaveTextContent(
      "GitHub is not reachable",
    );
    expect(buildButton()).toBeDisabled();
    fireEvent.click(screen.getByRole("button", { name: "Retry" }));
    expect(refetch).toHaveBeenCalledTimes(1);
  });

  it("builds from the chosen version and opens the new environment", async () => {
    preflight.current = { data: { ok: true, checks: [] } };
    mockCreateJob.mockResolvedValue({ job: { job_id: "job-9" } });
    const onClose = renderDialog();

    fireEvent.click(buildButton());

    await waitFor(() => expect(mockNavigate).toHaveBeenCalledTimes(1));
    const [payload, key] = mockCreateJob.mock.calls[0];
    expect(payload.metadata).toMatchObject({
      agent_definition_id: "a1",
      agent_version_id: "v1",
      name: "Support environment",
    });
    expect(key).toBe("key-1");
    expect(mockNavigate.mock.calls[0][0]).toContain("job-9");
    expect(onClose).toHaveBeenCalled();
  });

  it("says why a build was refused and stays open", async () => {
    preflight.current = { data: { ok: true, checks: [] } };
    mockCreateJob.mockRejectedValue({
      response: { data: { detail: "Usage limit reached" } },
    });
    const onClose = renderDialog();

    fireEvent.click(buildButton());

    await waitFor(() =>
      expect(mockSnackbar).toHaveBeenCalledWith("Usage limit reached", {
        variant: "error",
      }),
    );
    expect(mockNavigate).not.toHaveBeenCalled();
    expect(onClose).not.toHaveBeenCalled();
  });
});
