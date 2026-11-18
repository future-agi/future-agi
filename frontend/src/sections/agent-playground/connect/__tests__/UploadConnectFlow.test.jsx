import { beforeEach, describe, expect, it, vi } from "vitest";
import {
  fireEvent,
  renderWithRouter,
  screen,
  waitFor,
  within,
} from "src/utils/test-utils";
import UploadConnectFlow from "../UploadConnectFlow";

const mockConnect = vi.fn();
const mockUpload = vi.fn();
const startScanState = vi.hoisted(() => ({
  mutate: () => {},
  isPending: false,
}));
const scanState = vi.hoisted(() => ({
  data: null,
  isError: false,
  error: null,
  refetch: () => {},
}));

vi.mock("react-router-dom", async () => {
  const actual = await vi.importActual("react-router-dom");
  return { ...actual, useNavigate: () => vi.fn() };
});

vi.mock("src/api/agents/agentConnect", () => ({
  useStartScan: () => ({ ...startScanState }),
  useAgentScan: () => ({ ...scanState }),
  useConnectAgent: () => ({ mutate: mockConnect, isPending: false }),
}));

vi.mock("src/pages/dashboard/harness/sourceUpload", () => ({
  prepareSourceFolder: (files) => ({
    name: files[0].name,
    files,
    totalBytes: 10,
  }),
  uploadSourceFolder: (...args) => mockUpload(...args),
  uploadErrorText: () => "The upload failed",
}));

vi.mock("src/auth/hooks", async (importOriginal) => ({
  ...(await importOriginal()),
  useAuthContext: () => ({ role: "Member" }),
}));

const FOLDER = { name: "support-agent", sourceId: "src-1", files: 3 };

const startAt = (scan) => {
  scanState.data = scan;
  sessionStorage.setItem(
    "agents.connect.upload.draft",
    JSON.stringify({ stage: "agents", folder: FOLDER, scanId: "scan-1" }),
  );
  renderWithRouter(<UploadConnectFlow />);
};

const connectBody = () => {
  fireEvent.click(screen.getByRole("button", { name: /^Next/ }));
  fireEvent.click(screen.getByRole("button", { name: /^Connect agent/ }));
  return mockConnect.mock.calls[0][0];
};

const pickFolder = (name) =>
  fireEvent.change(document.querySelector('input[type="file"]'), {
    target: { files: [new File(["x"], name)] },
  });

const pick = (name) =>
  fireEvent.click(
    within(screen.getByTestId(`found-agent-${name}`)).getByRole("radio"),
  );

beforeEach(() => {
  sessionStorage.clear();
  mockConnect.mockReset();
  mockUpload.mockReset();
  startScanState.mutate = vi.fn();
  startScanState.isPending = false;
  scanState.data = null;
  scanState.isError = false;
  scanState.error = null;
  scanState.refetch = () => {};
});

describe("UploadConnectFlow", () => {
  it("never offers to name a folder inside the upload", () => {
    startAt({
      status: "completed",
      agents: [
        { name: "support", subfolder: "", entry: "agent.py", envNames: [] },
      ],
    });

    expect(screen.queryByText(/Name its folder/)).toBeNull();
    expect(
      screen.getByText("Connect the uploaded folder anyway"),
    ).toBeInTheDocument();
  });

  it("connects the whole upload when the agent isn't listed", () => {
    startAt({ status: "completed", agents: [] });

    fireEvent.click(screen.getByText("Connect the uploaded folder anyway"));
    expect(screen.getByTestId("connect-whole-upload")).toHaveTextContent(
      "support-agent",
    );

    expect(connectBody()).toEqual({
      kind: "archive",
      archive_artifact_id: "src-1",
      name: "support-agent",
      env_values: {},
    });
  });

  it("connects the whole upload after a failed scan", () => {
    startAt({ status: "failed", failure: { message: "No entry file" } });

    expect(screen.queryByText(/folder yourself/)).toBeNull();
    fireEvent.click(screen.getByText("Connect the uploaded folder anyway"));

    expect(connectBody()).not.toHaveProperty("subfolder");
  });

  it("sends no folder for an agent the scan found", () => {
    startAt({
      status: "completed",
      agents: [
        {
          name: "support",
          subfolder: "agents/support",
          entry: "a.py",
          envNames: [],
        },
      ],
    });

    fireEvent.click(screen.getByRole("radio"));

    expect(connectBody()).not.toHaveProperty("subfolder");
  });

  it("says the scan couldn't be checked and retries, instead of spinning", () => {
    scanState.isError = true;
    scanState.error = { message: "Network Error" };
    scanState.refetch = vi.fn();
    startAt(undefined);

    expect(screen.queryByText(/Reading the upload/)).toBeNull();
    expect(screen.getByText("Couldn't check on the scan")).toBeInTheDocument();
    expect(screen.getByText("Network Error")).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "Retry" }));
    expect(scanState.refetch).toHaveBeenCalledTimes(1);
  });

  it("connects the entry file of the agent picked", () => {
    startAt({
      status: "completed",
      agents: [
        { name: "support", subfolder: "", entry: "support.py", envNames: [] },
        { name: "billing", subfolder: "", entry: "billing.py", envNames: [] },
      ],
    });

    pick("billing");

    expect(connectBody()).toMatchObject({
      archive_artifact_id: "src-1",
      entry: "billing.py",
    });
  });

  it("sends only the values of the agent picked", () => {
    startAt({
      status: "completed",
      agents: [
        { name: "x", subfolder: "", entry: "x.py", envNames: ["X_KEY"] },
        { name: "y", subfolder: "", entry: "y.py", envNames: ["Y_KEY"] },
      ],
    });

    pick("x");
    fireEvent.click(screen.getByRole("button", { name: /^Next/ }));
    fireEvent.change(screen.getByTestId("env-value-X_KEY"), {
      target: { value: "secret-x" },
    });
    fireEvent.click(screen.getByRole("button", { name: /^Back/ }));
    pick("y");

    expect(connectBody().env_values).toEqual({});
  });

  it("holds the folder while the agents are being found", () => {
    startScanState.isPending = true;
    sessionStorage.setItem(
      "agents.connect.upload.draft",
      JSON.stringify({ stage: "source", folder: FOLDER }),
    );
    renderWithRouter(<UploadConnectFlow />);

    expect(screen.getByTestId("connect-upload-dropzone")).toBeDisabled();
  });

  it("forgets the first upload once another folder is picked", async () => {
    mockUpload
      .mockResolvedValueOnce({ source_id: "src-1" })
      .mockRejectedValueOnce(new Error("rejected"));
    renderWithRouter(<UploadConnectFlow />);
    const findAgents = () =>
      screen.getByRole("button", { name: "Find agents" });

    pickFolder("first");
    await waitFor(() => expect(findAgents()).toBeEnabled());
    pickFolder("second");
    expect(
      await screen.findByTestId("connect-upload-dropzone-error"),
    ).toHaveTextContent("The upload failed");

    expect(findAgents()).toBeDisabled();
    fireEvent.click(findAgents());
    expect(startScanState.mutate).not.toHaveBeenCalled();
  });
});
