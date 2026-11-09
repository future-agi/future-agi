import React, { forwardRef } from "react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { cleanup, fireEvent, screen } from "@testing-library/react";
import { renderPage, MockAgGrid } from "../../guardrails/__tests__/helpers";
import GatewaySettingsSection from "../GatewaySettingsSection";
import ConfigHistoryDrawer from "../ConfigHistoryDrawer";
import ServerMark from "../../mcp/components/ServerMark";
import globalCss from "src/global.css?raw";

const mocks = vi.hoisted(() => ({
  get: vi.fn(),
  post: vi.fn(),
  level: 8,
  workspaceRole: "workspace_member",
}));
vi.mock("src/utils/axios", async (original) => {
  const actual = await original();
  return {
    ...actual,
    default: { ...actual.default, get: mocks.get, post: mocks.post },
  };
});
vi.mock("src/contexts/OrganizationContext", () => ({
  useOrganization: () => ({ orgLevel: mocks.level }),
}));
vi.mock("src/contexts/WorkspaceContext", () => ({
  useWorkspace: () => ({ currentWorkspaceRole: mocks.workspaceRole }),
}));
vi.mock("notistack", () => ({ enqueueSnackbar: vi.fn() }));
vi.mock("ag-grid-react", () => ({
  AgGridReact: forwardRef(function Grid(props, _ref) {
    return <MockAgGrid {...props} />;
  }),
}));
vi.mock("src/sections/gateway/context/useGatewayContext", () => ({
  useGatewayContext: () => ({
    gatewayId: "gw-1",
    gateway: { status: "healthy", name: "Local" },
    isLoading: false,
  }),
}));
vi.mock("src/sections/gateway/settings/EmailAlertsCard", () => ({
  default: () => null,
}));

let saved;
let pipeline;
beforeEach(() => {
  cleanup();
  mocks.level = 8;
  mocks.workspaceRole = "workspace_member";
  pipeline = { enabled: false, fail_open: false };
  saved = {
    rules: [
      {
        name: "tool-permissions",
        mode: "sync",
        enabled: true,
        action: "block",
        threshold: 0.8,
        config: {
          provider: "tool_permission",
          mode: "denylist",
          tools: "qa_blocked_*",
        },
      },
    ],
  };
  mocks.get.mockReset().mockImplementation((url) => {
    const result = url.includes("org-configs/active")
      ? { guardrails: saved }
      : url.includes("/config/")
        ? { guardrail_pipeline: pipeline }
        : [];
    return Promise.resolve({ data: { result } });
  });
  mocks.post
    .mockReset()
    .mockResolvedValue({ data: { result: { gateway_synced: true } } });
});

describe("MCP audit regression flows", () => {
  // Health Check and Reload Config stay Owner or Admin only (gateway-lead
  // ruling GL-1029-2): a Member or a Viewer, even one who writes to the
  // workspace, is shown them disabled with the role they need, and nothing
  // is posted.
  it.each([
    [1, "workspace_viewer"],
    [3, "workspace_member"],
    [3, "workspace_admin"],
  ])(
    "does not offer health/reload to org level %s (%s)",
    (level, workspaceRole) => {
      mocks.level = level;
      mocks.workspaceRole = workspaceRole;
      renderPage(<GatewaySettingsSection />);
      for (const name of ["Health Check", "Reload Config"]) {
        const button = screen.getByRole("button", { name });
        expect(button).toBeDisabled();
        expect(
          button.closest('[aria-label="Owner or Admin only"]'),
        ).not.toBeNull();
        fireEvent.click(button);
      }
      expect(mocks.post).not.toHaveBeenCalled();
    },
  );

  it.each([8, 15])(
    "keeps the settings controls enabled for an Owner or Admin (level %s)",
    (level) => {
      mocks.level = level;
      renderPage(<GatewaySettingsSection />);
      for (const name of ["Health Check", "Reload Config"]) {
        const button = screen.getByRole("button", { name });
        expect(button).toBeEnabled();
        expect(button.closest('[aria-label="Owner or Admin only"]')).toBeNull();
      }
    },
  );

  it.each([401, 403])(
    "does not classify permission status %s as gateway downtime",
    async (status) => {
      mocks.post.mockRejectedValue({
        statusCode: status,
        message: "Write access denied to this workspace",
      });
      renderPage(<GatewaySettingsSection />);
      await screen.findByText("Local");
      fireEvent.click(screen.getByRole("button", { name: "Health Check" }));
      expect(
        await screen.findByText(/Health check not authorized/),
      ).toBeInTheDocument();
      expect(screen.queryByText(/Gateway did not answer/)).toBeNull();
    },
  );

  it("caps the history drawer to the viewport including padding", async () => {
    renderPage(<ConfigHistoryDrawer open onClose={() => {}} />);
    const paper = (await screen.findByText("Config History")).closest(
      ".MuiDrawer-paper",
    );
    // eed996c35c moved Config History into the shared GatewayDetailDrawer:
    // its paper is capped at 100% and carries no padding of its own (the
    // header and body pad inside it), so no padding can push past the cap.
    expect(getComputedStyle(paper).maxWidth).toBe("100%");
    expect(getComputedStyle(paper).paddingLeft || "0px").toBe("0px");
    expect(getComputedStyle(paper).paddingRight || "0px").toBe("0px");
  });

  it("does not import third-party font stylesheets", () => {
    expect(globalCss).not.toMatch(/@import\s+url\(["']https?:/);
  });

  // The app-wide Iconify bundle belongs to main, not the MCP repair. Keep
  // this lane's offline assertion on its section-local catalog logo instead.
  it("renders the MCP catalog logo from a bundled asset", () => {
    const { container } = renderPage(
      <ServerMark id="github" url="https://api.githubcopilot.com/mcp" />,
    );
    expect(container.querySelector("img")).toHaveAttribute(
      "src",
      "/assets/icons/app/ic_github_grey.svg",
    );
  });
});
