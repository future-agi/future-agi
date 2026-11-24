import { beforeEach, describe, expect, it, vi } from "vitest";
import userEvent from "@testing-library/user-event";
import { render, screen } from "src/utils/test-utils";

const caps = vi.hoisted(() => ({ codingAgent: true }));
vi.mock("src/hooks/useCapabilities", async (importOriginal) => {
  const actual = await importOriginal();
  return {
    ...actual,
    useCapabilities: () => ({
      data: { coding_agent_attempts_enabled: caps.codingAgent },
      isLoading: false,
      isError: false,
    }),
    useFeatureAllowed: () => ({ allowed: true, isLoading: false }),
  };
});

vi.mock("src/components/iconify", () => ({
  default: ({ icon }) => <span data-testid="iconify" data-icon={icon} />,
}));

const { default: ClientSetupGuide } = await import("../ClientSetupGuide");
const { default: CodingAgentSetup } = await import("../CodingAgentSetup");

const MCP_URL = "https://api.example.test/mcp";

beforeEach(() => {
  caps.codingAgent = true;
});

describe("Codex in Connect Your IDE", () => {
  it("has a Codex tab with its config.toml table and the OAuth login", async () => {
    const user = userEvent.setup();
    render(<ClientSetupGuide config={{ mcp_url: MCP_URL }} />);
    await user.click(screen.getByRole("tab", { name: /Codex/ }));
    expect(screen.getByText("~/.codex/config.toml")).toBeInTheDocument();
    expect(
      screen.getByText(
        (_, el) =>
          el?.textContent === `[mcp_servers.futureagi]\nurl = "${MCP_URL}"` &&
          el.children.length === 1,
      ),
    ).toBeInTheDocument();
    expect(
      screen.getByText(
        (_, el) =>
          el?.textContent === "codex mcp login futureagi" &&
          el.children.length === 1,
      ),
    ).toBeInTheDocument();
  });

  it("keeps the Claude Code and Cursor tabs", () => {
    render(<ClientSetupGuide config={{ mcp_url: MCP_URL }} />);
    expect(
      screen.getByRole("tab", { name: /Claude Code/ }),
    ).toBeInTheDocument();
    expect(screen.getByRole("tab", { name: /Cursor/ })).toBeInTheDocument();
  });
});

describe("IDE & coding agents", () => {
  it("is hidden while the setting is off", () => {
    caps.codingAgent = false;
    render(<CodingAgentSetup />);
    expect(screen.queryByTestId("coding-agent-setup")).not.toBeInTheDocument();
  });

  it("gives the fagi install and login steps, and says what an attempt is", () => {
    render(<CodingAgentSetup />);
    const section = screen.getByTestId("coding-agent-setup");
    expect(section).toHaveTextContent("IDE & coding agents");
    expect(section).toHaveTextContent("npm install -g @future-agi/cli");
    expect(section).toHaveTextContent("npx @future-agi/cli");
    expect(section).toHaveTextContent("fagi login");
    expect(section).toHaveTextContent(
      "checks it on cases your agent wasn't given",
    );
    expect(section).not.toHaveTextContent(/can't read|cannot read/i);
  });
});
