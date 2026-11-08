import { afterEach, describe, expect, it, vi } from "vitest";
import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { ThemeProvider, createTheme } from "@mui/material/styles";
import { dsPalette, dsTypography } from "src/theme/ds-tokens";
import UseSkillPanel from "./UseSkillPanel";

vi.mock("src/config-global", () => ({ HOST_API: "https://api.example.test" }));
vi.mock("src/components/iconify", () => ({ default: () => null }));
vi.mock("../components/VersionSelect", () => ({
  default: vi.fn(({ value, onChange }) => (
    <select
      aria-label="Pinned version"
      value={value}
      onChange={(event) => onChange(Number(event.target.value))}
    >
      <option value="1">v1</option>
      <option value="2">v2</option>
    </select>
  )),
}));
const skill = {
  id: "skill-a",
  name: "support-policy",
  latestVersion: 2,
  labels: [
    { name: "production", version: 1 },
    { name: "canary", version: 2 },
  ],
  tags: ["support", "policy"],
};
const theme = createTheme({
  palette: dsPalette("light"),
  typography: dsTypography,
});
function show(overrides = {}) {
  return render(
    <ThemeProvider theme={theme}>
      <MemoryRouter>
        <UseSkillPanel skill={{ ...skill, ...overrides }} />
      </MemoryRouter>
    </ThemeProvider>,
  );
}
async function selectLabel(label) {
  fireEvent.mouseDown(
    screen.getByRole("combobox", { name: "Use label or version" }),
  );
  fireEvent.click(
    await screen.findByRole("option", { name: label, exact: true }),
  );
}
afterEach(cleanup);

describe("Use skill panel", () => {
  it("wraps both install commands without inserting newlines into the copied shell command", () => {
    const writeText = vi.fn();
    Object.defineProperty(navigator, "clipboard", { configurable: true, value: { writeText } });
    const { container } = show({ name: "support-policy-" + "a".repeat(48), tags: ["very-long-tag-" + "b".repeat(48)] });
    const snippets = [...container.querySelectorAll("pre")];
    const copyButtons = screen.getAllByRole("button", { name: "Copy code" });
    expect(snippets).toHaveLength(2);
    snippets.forEach((snippet, index) => {
      expect(snippet).toHaveStyle({ whiteSpace: "pre-wrap", overflowWrap: "anywhere", wordBreak: "break-word" });
      expect(snippet.textContent).not.toContain("\n");
      expect(snippet.textContent).toContain("&& rm");
      fireEvent.click(copyButtons[index]);
      expect(writeText).toHaveBeenLastCalledWith(snippet.textContent);
    });
  });
  it("uses HOST_API and environment variables, with an API keys settings link", () => {
    const { container } = show();
    const command = container.querySelector("pre").textContent;
    expect(command).toContain(
      "https://api.example.test/model-hub/skills/download/?names=support-policy&label=production",
    );
    expect(command).toContain("$FI_API_KEY");
    expect(command).toContain("$FI_SECRET_KEY");
    expect(command).toContain("mkdir -p .claude/skills");
    expect(
      screen.getByRole("link", { name: "Manage API keys" }),
    ).toHaveAttribute("href", "/dashboard/settings/api_keys");
    expect(screen.getByText(/do not make them read-only/)).toBeInTheDocument();
  });
  it.each([
    ["Claude Code", ".claude/skills", "~/.claude/skills"],
    ["Codex", ".agents/skills", "~/.agents/skills"],
    ["Cursor", ".cursor/skills", "~/.cursor/skills"],
    ["Gemini CLI", ".gemini/skills", "~/.gemini/skills"],
    ["VS Code", ".github/skills", "~/.copilot/skills"],
  ])(
    "builds project and personal commands for %s",
    (client, project, personal) => {
      const { container } = show();
      fireEvent.click(screen.getByRole("tab", { name: client }));
      expect(container.querySelector("pre")).toHaveTextContent(
        `mkdir -p ${project}`,
      );
      fireEvent.click(screen.getByRole("tab", { name: "Personal" }));
      expect(container.querySelector("pre")).toHaveTextContent(
        `mkdir -p ${personal}`,
      );
    },
  );
  it("pins a numeric version without a label and disables multi-skill version downloads", async () => {
    const { container } = show();
    await selectLabel("Pinned version");
    fireEvent.change(
      screen.getByRole("combobox", { name: "Pinned version", exact: true }),
      { target: { value: "1" } },
    );
    const command = container.querySelector("pre").textContent;
    expect(command).toContain("names=support-policy&version=1");
    expect(command).not.toContain("label=");
    expect(
      screen.getByText(/Version numbers are specific to one skill/),
    ).toBeInTheDocument();
    expect(container.querySelectorAll("pre")).toHaveLength(1);
  });
  it("warns about missing labels without silently substituting latest", async () => {
    const { container } = show();
    await selectLabel("staging");
    expect(screen.getByText(/no staging label/)).toBeInTheDocument();
    expect(container.querySelector("pre")).toHaveTextContent("label=staging");
    await selectLabel("latest");
    expect(
      screen.queryByText(/do not fall back to latest/),
    ).not.toBeInTheDocument();
    expect(container.querySelector("pre")).toHaveTextContent("label=latest");
  });
  it("supports custom labels and tag commands without retaining names", async () => {
    const { container } = show();
    await selectLabel("canary");
    expect(container.querySelectorAll("pre")[1]).toHaveTextContent(
      "tag=support&label=canary",
    );
    expect(container.querySelectorAll("pre")[1].textContent).not.toContain(
      "names=",
    );
    fireEvent.mouseDown(screen.getByLabelText("Tag"));
    fireEvent.click(
      await screen.findByRole("option", { name: "policy", exact: true }),
    );
    expect(container.querySelectorAll("pre")[1]).toHaveTextContent(
      "tag=policy&label=canary",
    );
  });
  it.each([
    ["cURL", "curl -fsSL"],
    ["Python", 'os.environ["FI_API_KEY"]'],
    ["TypeScript", "process.env.FI_API_KEY"],
    ["MCP", "read_skill_file"],
  ])(
    "shows the %s integration with the selected immutable version",
    async (client, expected) => {
      const { container } = show();
      await selectLabel("Pinned version");
      fireEvent.click(screen.getByRole("tab", { name: client }));
      const command = container.querySelector("pre").textContent;
      expect(command).toContain(expected);
      expect(command).toContain(
        client === "MCP" ? '"version": 2' : "version=2",
      );
      expect(
        screen.queryByRole("tab", { name: "Personal" }),
      ).not.toBeInTheDocument();
      expect(
        screen.queryByText("Install every skill with a tag"),
      ).not.toBeInTheDocument();
    },
  );
  it("shows publish guidance for an unpublished skill", () => {
    show({ latestVersion: null });
    expect(
      screen.getByText("Publish before using this skill"),
    ).toBeInTheDocument();
    expect(screen.queryByRole("tab")).not.toBeInTheDocument();
  });
  it("does not offer an empty tag install", () => {
    show({ tags: [] });
    expect(
      screen.queryByText("Install every skill with a tag"),
    ).not.toBeInTheDocument();
  });
});
