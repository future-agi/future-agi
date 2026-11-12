import { describe, it, expect, vi } from "vitest";
import { render, screen, fireEvent } from "src/utils/test-utils";
import SimulationScenarioEmptyScreen from "../SimulationScenarioEmptyScreen";

vi.mock("src/auth/hooks", () => ({
  useAuthContext: () => ({ role: "Owner" }),
}));
vi.mock("src/utils/Mixpanel", () => ({
  trackEvent: vi.fn(),
  Events: {},
  PropertyName: {},
}));
vi.mock("src/components/iconify", () => ({ default: () => null }));

const LOOM_URL = "https://www.loom.com/embed/f13e7911fb5c4ec583c72dc20acbc83a";
const VIDEO_TITLE = "How to create scenarios";

// The how-to video on the empty Scenarios page. Before it loads, the page
// shows a click-to-load card headed with the video's title.
describe("Scenarios empty page video", () => {
  it("is titled for Scenarios, not Knowledge Base", () => {
    render(<SimulationScenarioEmptyScreen />);
    expect(
      screen.getByRole("group", { name: VIDEO_TITLE }),
    ).toBeInTheDocument();
    expect(screen.queryByText("Knowledge Base")).toBeNull();
  });

  it("loads the same video under the new title", () => {
    const { container } = render(<SimulationScenarioEmptyScreen />);
    fireEvent.click(
      screen.getByRole("button", { name: "Load external content" }),
    );
    const frame = container.querySelector("iframe");
    expect(frame).toHaveAttribute("src", LOOM_URL);
    expect(frame).toHaveAttribute("title", VIDEO_TITLE);
  });
});
