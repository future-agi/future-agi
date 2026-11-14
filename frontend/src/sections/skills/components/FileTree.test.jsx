import { describe, expect, it, vi } from "vitest";
import userEvent from "@testing-library/user-event";
import { render, screen, within } from "@testing-library/react";
import { iconExists } from "@iconify/react";
import { LIMITS } from "../constants";
import FileTree from "./FileTree";

vi.mock("src/components/ModalWrapper/ModalWrapper", () => ({ default: () => null }));
vi.mock("src/components/custom-dialog", () => ({ ConfirmDialog: () => null }));
const files = [{ path: "SKILL.md" }, { path: "references/policy.md" }];

describe("FileTree file actions", () => {
  it("renders three accessible, offline icon buttons in a non-wrapping row", async () => {
    const user = userEvent.setup();
    render(<FileTree files={files} selectedPath="references/policy.md" onSelect={vi.fn()} onCreate={vi.fn()} onRename={vi.fn()} onDelete={vi.fn()} readOnly={false} />);
    const actions = screen.getByRole("group", { name: "File actions" });
    expect(actions).toHaveStyle({ flexWrap: "nowrap", flexDirection: "row" });
    const names = ["Add file", "Rename file", "Delete file"];
    const icons = ["solar:file-add-linear", "solar:pen-linear", "solar:trash-bin-trash-linear"];
    expect(within(actions).getAllByRole("button")).toHaveLength(3);
    for (const [index, name] of names.entries()) {
      const button = within(actions).getByRole("button", { name });
      expect(button).toHaveClass("MuiIconButton-root");
      expect(button.textContent).toBe("");
      expect(button.querySelector("svg")).not.toBeNull();
      expect(iconExists(icons[index])).toBe(true);
      await user.tab();
      expect(button).toHaveFocus();
      expect(await screen.findByRole("tooltip")).toHaveTextContent(name);
    }
  });

  it("keeps protected-root and file-limit actions disabled with explanatory tooltips", async () => {
    const user = userEvent.setup();
    render(<FileTree files={Array.from({ length: LIMITS.MAX_FILES }, (_, index) => ({ path: index ? `file-${index}.md` : "SKILL.md" }))} selectedPath="SKILL.md" onSelect={vi.fn()} onCreate={vi.fn()} onRename={vi.fn()} onDelete={vi.fn()} readOnly={false} />);
    const create = screen.getByRole("button", { name: "Add file" });
    expect(create).toBeDisabled();
    expect(screen.getByRole("button", { name: "Rename file" })).toBeDisabled();
    expect(screen.getByRole("button", { name: "Delete file" })).toBeDisabled();
    expect(screen.queryByRole("button", { name: "New folder" })).not.toBeInTheDocument();
    await user.hover(create.parentElement);
    expect(await screen.findByRole("tooltip")).toHaveTextContent("File limit reached.");
  });

  it("disables unavailable callbacks and hides the toolbar in read-only mode", () => {
    const { rerender } = render(<FileTree files={files} onSelect={vi.fn()} readOnly={false} />);
    for (const button of within(screen.getByRole("group", { name: "File actions" })).getAllByRole("button")) expect(button).toBeDisabled();
    rerender(<FileTree files={files} onSelect={vi.fn()} />);
    expect(screen.queryByRole("group", { name: "File actions" })).not.toBeInTheDocument();
  });
});

describe("FileTree keyboard activation", () => {
  it("toggles a folder exactly once per full Space press and release", async () => {
    const user = userEvent.setup();
    render(<FileTree files={files} selectedPath="SKILL.md" onSelect={vi.fn()} />);
    await user.tab();
    await user.keyboard("{ArrowDown}");
    const folder = screen.getByRole("treeitem", { name: "references" });
    expect(folder).toHaveFocus();
    expect(folder).toHaveAttribute("aria-expanded", "true");
    await user.keyboard(" ");
    expect(folder).toHaveAttribute("aria-expanded", "false");
    expect(screen.queryByRole("treeitem", { name: "policy.md" })).not.toBeInTheDocument();
    await user.keyboard(" ");
    expect(folder).toHaveAttribute("aria-expanded", "true");
    expect(screen.getByRole("treeitem", { name: "policy.md" })).toBeInTheDocument();
  });

  it("selects a file once per Space press without breaking Enter", async () => {
    const user = userEvent.setup();
    const onSelect = vi.fn();
    render(<FileTree files={files} selectedPath="SKILL.md" onSelect={onSelect} />);
    await user.tab();
    await user.keyboard(" ");
    expect(onSelect).toHaveBeenCalledTimes(1);
    expect(onSelect).toHaveBeenCalledWith("SKILL.md");
    await user.keyboard("{Enter}");
    expect(onSelect).toHaveBeenCalledTimes(2);
  });
});

it('groups context files and reveals the selected linked file without changing its path', () => {
  const contextFiles = [{ path: 'AGENTS.md', origin: { kind: 'owned' } }, { path: 'skills/policy/SKILL.md', origin: { kind: 'linked' } }, { path: '.futureagi/context.json', origin: { kind: 'generated' } }];
  const select = vi.fn();
  const { rerender } = render(<FileTree files={contextFiles} selectedPath="AGENTS.md" onSelect={select} />);
  expect(screen.getByRole('treeitem', { name: 'Linked skills' })).toHaveAttribute('aria-expanded', 'false');
  expect(screen.queryByRole('treeitem', { name: 'SKILL.md' })).not.toBeInTheDocument();
  rerender(<FileTree files={contextFiles} selectedPath="skills/policy/SKILL.md" onSelect={select} />);
  expect(screen.getByRole('treeitem', { name: 'Linked skills' })).toHaveAttribute('aria-expanded', 'true');
  expect(screen.getByRole('treeitem', { name: 'SKILL.md' })).toHaveAttribute('aria-selected', 'true');
});

describe("FileTree groups", () => {
  const grouped = [
    { path: "AGENTS.md", origin: { kind: "owned" } },
    { path: ".futureagi/context.json", origin: { kind: "generated" } },
  ];
  it("calls platform-added files Other files, explains the group, and keeps the paths one open away", async () => {
    const user = userEvent.setup();
    render(<FileTree files={grouped} selectedPath="AGENTS.md" onSelect={vi.fn()} />);
    const group = screen.getByRole("treeitem", { name: "Other files" });
    expect(screen.queryByText("Generated files")).not.toBeInTheDocument();
    expect(screen.getByText("Added by Future AGI so apps load the right versions.")).toBeVisible();
    expect(group).toHaveAccessibleDescription("Added by Future AGI so apps load the right versions.");
    await user.click(group);
    expect(screen.getByRole("treeitem", { name: ".futureagi" })).toBeInTheDocument();
  });
});
