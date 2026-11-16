import { beforeEach, describe, expect, it, vi } from "vitest";
import { fireEvent, renderWithRouter, screen } from "src/utils/test-utils";
import UploadConnectFlow from "../UploadConnectFlow";

const mockConnect = vi.fn();
const scanState = vi.hoisted(() => ({ data: null }));

vi.mock("react-router-dom", async () => {
  const actual = await vi.importActual("react-router-dom");
  return { ...actual, useNavigate: () => vi.fn() };
});

vi.mock("src/api/agents/agentConnect", () => ({
  useStartScan: () => ({ mutate: vi.fn(), isPending: false }),
  useAgentScan: () => ({ data: scanState.data, isError: false }),
  useConnectAgent: () => ({ mutate: mockConnect, isPending: false }),
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

beforeEach(() => {
  sessionStorage.clear();
  mockConnect.mockReset();
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
});
