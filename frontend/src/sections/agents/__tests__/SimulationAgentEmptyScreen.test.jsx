import { describe, it, expect, vi } from "vitest";
import { render, screen, fireEvent } from "src/utils/test-utils";
import SimulationAgentEmptyScreen from "../SimulationAgentEmptyScreen";

vi.mock("src/auth/hooks", () => ({
  useAuthContext: () => ({ role: "Owner" }),
}));
vi.mock("src/utils/Mixpanel", () => ({
  trackEvent: vi.fn(),
  handleOnDocsClicked: vi.fn(),
  Events: {},
  PropertyName: {},
}));
vi.mock("react-router", async (importOriginal) => ({
  ...(await importOriginal()),
  useNavigate: () => vi.fn(),
}));
vi.mock("src/components/iconify", () => ({ default: () => null }));

const LOOM_URL = "https://www.loom.com/embed/f13e7911fb5c4ec583c72dc20acbc83a";
// The same video is titled this way on the agent-definition create steps.
const VIDEO_TITLE = "How to create agent definition";

describe("Agent definitions empty page video", () => {
  it("is titled for agent definitions, not Knowledge Base", () => {
    render(<SimulationAgentEmptyScreen />);
    expect(
      screen.getByRole("group", { name: VIDEO_TITLE }),
    ).toBeInTheDocument();
    expect(screen.queryByText("Knowledge Base")).toBeNull();
  });

  it("loads the same video under the new title", () => {
    const { container } = render(<SimulationAgentEmptyScreen />);
    fireEvent.click(
      screen.getByRole("button", { name: "Load external content" }),
    );
    const frame = container.querySelector("iframe");
    expect(frame).toHaveAttribute("src", LOOM_URL);
    expect(frame).toHaveAttribute("title", VIDEO_TITLE);
  });
});
