import PropTypes from "prop-types";
import { describe, it, expect, vi, beforeEach } from "vitest";
import { render, screen } from "src/utils/test-utils";
import useFalconStore from "../store/useFalconStore";
import FalconAIHeader from "../components/FalconAIHeader";

// The left menu calls this page "Chat"; the page must read the same way, with
// Falcon AI named as the assistant on it.

function MockIconify({ icon, ...props }) {
  return <span data-testid="iconify" data-icon={icon} {...props} />;
}

MockIconify.propTypes = {
  icon: PropTypes.string.isRequired,
};

vi.mock("src/components/iconify", () => ({
  default: MockIconify,
}));

vi.mock("../hooks/useFalconContext", () => ({
  useFalconContext: () => ({ page: "general" }),
}));

vi.mock("../hooks/useFalconAPI", () => ({
  fetchConversations: vi.fn(),
  getConversation: vi.fn(),
}));

vi.mock("../components/SkillEditorDialog", () => ({
  default: () => null,
}));

beforeEach(() => {
  useFalconStore.getState().resetAll();
});

describe("FalconAIHeader naming", () => {
  it("titles the full page Chat, with Falcon AI as the assistant", () => {
    render(<FalconAIHeader mode="fullpage" />);

    expect(screen.getByRole("heading", { name: "Chat" })).toBeInTheDocument();
    expect(screen.getByText("with Falcon AI")).toBeInTheDocument();
    expect(screen.queryByRole("heading", { name: "Falcon AI" })).toBeNull();
  });

  it("shows the chat's own title once it has one", () => {
    useFalconStore
      .getState()
      .setConversations([{ id: "conv-1", title: "Weekly error summary" }]);
    useFalconStore.getState().setCurrentConversation("conv-1");
    render(<FalconAIHeader mode="fullpage" />);

    expect(
      screen.getByRole("heading", { name: "Weekly error summary" }),
    ).toBeInTheDocument();
    expect(screen.queryByText("with Falcon AI")).toBeNull();
  });

  it("keeps the Falcon AI name in the sidebar opened by the Falcon AI button", () => {
    render(<FalconAIHeader mode="sidebar" />);

    expect(
      screen.getByRole("heading", { name: "Falcon AI" }),
    ).toBeInTheDocument();
    expect(screen.queryByText("with Falcon AI")).toBeNull();
  });
});
