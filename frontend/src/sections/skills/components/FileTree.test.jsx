import { describe, expect, it, vi } from "vitest";
import userEvent from "@testing-library/user-event";
import { render, screen } from "@testing-library/react";
import FileTree from "./FileTree";

vi.mock("src/components/ModalWrapper/ModalWrapper", () => ({ default: () => null }));
vi.mock("src/components/custom-dialog", () => ({ ConfirmDialog: () => null }));
const files = [{ path: "SKILL.md" }, { path: "references/policy.md" }];

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
