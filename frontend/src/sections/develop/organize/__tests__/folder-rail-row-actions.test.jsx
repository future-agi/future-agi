// The folder rail's ⋯ is a design-system row action: its own button next to
// the folder's row (never inside it, so it never moves the count), named
// for its folder, shown on row hover or focus, and it says it opens a menu
// and whether that menu is open. Every row reads its label then its count.
import React from "react";
import { afterEach, describe, expect, it, vi } from "vitest";
import {
  cleanup,
  fireEvent,
  render,
  screen,
  within,
} from "src/utils/test-utils";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import FolderRail from "../FolderRail";

afterEach(cleanup);

const PROPS = {
  folders: [{ path: "hello", count: 0, total: 0 }],
  allCount: 1,
  starredCount: 0,
  noFolderCount: 1,
  pick: { kind: "all" },
};

const mount = (props) =>
  render(
    <QueryClientProvider client={new QueryClient()}>
      <FolderRail {...PROPS} onPick={() => {}} {...props} />
    </QueryClientProvider>,
  );

describe("FolderRail row actions", () => {
  it("every row reads its label then its count", () => {
    mount({ onAction: () => {}, canEdit: true });
    const nav = screen.getByRole("navigation", { name: "Folders" });
    expect(
      within(nav)
        .getAllByRole("button")
        .filter((b) => !/^Folder actions/.test(b.getAttribute("aria-label")))
        .map((b) => b.textContent),
    ).toEqual(["All datasets1", "Starred0", "hello0", "No folder1"]);
  });

  it("the folder ⋯ is its own row action that reports its menu", () => {
    const onAction = vi.fn();
    mount({ onAction, canEdit: true });
    const row = screen.getByRole("button", { name: /^hello/ });
    const more = screen.getByRole("button", {
      name: "Folder actions for hello",
    });
    expect(row.contains(more)).toBe(false);
    expect(more).toHaveClass("row-hover-action");
    expect(more).toHaveAttribute("aria-haspopup", "menu");
    expect(more).toHaveAttribute("aria-expanded", "false");

    fireEvent.click(more);
    expect(more).toHaveAttribute("aria-expanded", "true");
    const menu = screen.getByRole("menu", {
      name: "Folder actions for hello",
    });
    expect(
      within(menu)
        .getAllByRole("menuitem")
        .map((item) => item.textContent),
    ).toEqual(["Rename folder…", "Move folder to…", "Remove folder"]);

    fireEvent.click(screen.getByRole("menuitem", { name: "Rename folder…" }));
    expect(onAction).toHaveBeenCalledWith("rename", "hello");
    expect(more).toHaveAttribute("aria-expanded", "false");
  });

  it("read-only people get no ⋯", () => {
    mount({ onAction: () => {} });
    expect(screen.queryByRole("button", { name: /Folder actions/ })).toBeNull();
  });
});
