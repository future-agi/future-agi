import { describe, expect, it, vi } from "vitest";
import userEvent from "@testing-library/user-event";
import { cleanup, render, screen, within } from "@testing-library/react";
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

describe("FileTree generated context settings (UX iteration 5, LIB3)", () => {
  const files = [
    { path: "AGENTS.md" },
    { path: ".futureagi/context.json", readOnly: true },
    { path: "skills/refunds/SKILL.md", readOnly: true },
    { path: "references/policy.md" },
  ];
  it("shows the generated descriptor as Context settings, keeps its path and its file name for assistive tech", () => {
    render(<FileTree files={files} selectedPath="AGENTS.md" onSelect={vi.fn()} generatedFolders={[".futureagi"]} collapsedFolders={["skills"]} />);
    const item = screen.getByRole("treeitem", { name: "context.json" });
    expect(item).toHaveTextContent("Context settings (generated)");
    expect(item).toHaveAttribute("title", ".futureagi/context.json");
    expect(item).toHaveAttribute("aria-description", ".futureagi/context.json");
    // UXF-16: the editor groups platform files like the read view, not as a ".futureagi Generated" folder.
    expect(screen.queryByText("Generated")).not.toBeInTheDocument();
    expect(screen.getByRole("treeitem", { name: "Other files" })).toHaveAttribute("aria-expanded", "true");
  });
  it("closes only the folders it is told to, so the generated settings stay visible", () => {
    render(<FileTree files={files} selectedPath="AGENTS.md" onSelect={vi.fn()} generatedFolders={[".futureagi"]} collapsedFolders={["skills"]} />);
    expect(screen.getByRole("treeitem", { name: "skills" })).toHaveAttribute("aria-expanded", "false");
    expect(screen.getByRole("treeitem", { name: ".futureagi" })).toHaveAttribute("aria-expanded", "true");
    expect(screen.queryByRole("treeitem", { name: "SKILL.md" })).not.toBeInTheDocument();
    expect(screen.getByRole("treeitem", { name: "policy.md" })).toBeVisible();
  });
  it("keeps generated files closed by default for other callers, and leaves a context.json elsewhere alone", async () => {
    const user = userEvent.setup();
    render(<FileTree files={files} selectedPath="AGENTS.md" onSelect={vi.fn()} generatedFolders={[".futureagi"]} />);
    const group = screen.getByRole("treeitem", { name: "Other files" });
    expect(group).toHaveAttribute("aria-expanded", "false");
    expect(screen.queryByRole("treeitem", { name: ".futureagi" })).not.toBeInTheDocument();
    await user.click(group);
    expect(screen.getByRole("treeitem", { name: ".futureagi" })).toBeInTheDocument();
    cleanup();
    render(<FileTree files={[{ path: "config/context.json" }]} selectedPath="config/context.json" onSelect={vi.fn()} generatedFolders={[".futureagi"]} />);
    expect(screen.getByRole("treeitem", { name: "context.json" })).toHaveTextContent("context.json");
    expect(screen.getByRole("treeitem", { name: "context.json" })).not.toHaveAttribute("title");
  });
});

describe("FileTree generated files in the draft editor (UXF-16)", () => {
  // Draft files carry no origin; only the generated folders say what the platform added.
  const draft = [
    { path: "AGENTS.md" },
    { path: "references/policy.md" },
    { path: "skills/refunds/SKILL.md", readOnly: true },
    { path: ".futureagi/context.json", readOnly: true },
  ];
  const props = { files: draft, onSelect: vi.fn(), pinned: ["AGENTS.md"], generatedFolders: [".futureagi"], collapsedFolders: ["skills"] };

  it("puts generated files under Other files: Added by Future AGI, last, as in the read view", () => {
    render(<FileTree {...props} selectedPath="AGENTS.md" />);
    const top = screen.getAllByRole("treeitem").filter((item) => item.getAttribute("aria-level") === "1").map((item) => item.getAttribute("aria-label"));
    expect(top).toEqual(["AGENTS.md", "references", "skills", "Other files"]);
    const group = screen.getByRole("treeitem", { name: "Other files" });
    expect(group).toHaveAccessibleDescription("Added by Future AGI so apps load the right versions.");
    expect(screen.getByRole("treeitem", { name: ".futureagi" })).toHaveAttribute("aria-level", "2");
    expect(screen.queryByText("Generated")).not.toBeInTheDocument();
  });

  it("keeps owned and linked files where they were, with their own paths", async () => {
    const user = userEvent.setup();
    const onSelect = vi.fn();
    render(<FileTree {...props} onSelect={onSelect} selectedPath="AGENTS.md" />);
    expect(screen.queryByRole("treeitem", { name: "Instructions" })).not.toBeInTheDocument();
    expect(screen.queryByRole("treeitem", { name: "Linked skills" })).not.toBeInTheDocument();
    await user.click(screen.getByRole("treeitem", { name: "policy.md" }));
    expect(onSelect).toHaveBeenLastCalledWith("references/policy.md");
    await user.click(screen.getByRole("treeitem", { name: "context.json" }));
    expect(onSelect).toHaveBeenLastCalledWith(".futureagi/context.json");
  });

  it("opens the group when a generated file is selected from elsewhere", () => {
    const { rerender } = render(<FileTree {...props} collapsedFolders={[".futureagi"]} selectedPath="AGENTS.md" />);
    expect(screen.getByRole("treeitem", { name: "Other files" })).toHaveAttribute("aria-expanded", "false");
    rerender(<FileTree {...props} collapsedFolders={[".futureagi"]} selectedPath=".futureagi/context.json" />);
    expect(screen.getByRole("treeitem", { name: "Other files" })).toHaveAttribute("aria-expanded", "true");
    expect(screen.getByRole("treeitem", { name: "context.json" })).toHaveAttribute("aria-selected", "true");
  });

  it("leaves a plain skill tree ungrouped", () => {
    render(<FileTree files={[{ path: "SKILL.md" }, { path: "references/policy.md" }]} selectedPath="SKILL.md" onSelect={vi.fn()} />);
    expect(screen.queryByRole("treeitem", { name: "Other files" })).not.toBeInTheDocument();
    expect(screen.getAllByRole("treeitem")[0]).toHaveAttribute("aria-label", "SKILL.md");
  });
});
