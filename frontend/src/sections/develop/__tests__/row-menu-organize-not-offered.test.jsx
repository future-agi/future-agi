// A dataset row's ⋮ and right-click menu (one item list) where the
// deployment does not offer dataset metadata or attaching datasets to
// agents: Metadata… and Agents… stay, disabled, each with the shared line
// under the label and as its description, for editors and viewers alike; a
// press reaches nothing. Move to folder… keeps working.
import React from "react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { cleanup, fireEvent, screen, within } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render } from "src/utils/test-utils";
import { NOT_IN_RELEASE } from "src/components/NotInRelease/notInRelease";
import { setCollabOfferForTest } from "src/sections/common/Collab/collabOffer";

const h = vi.hoisted(() => ({
  get: vi.fn(() => new Promise(() => {})),
  put: vi.fn(),
  organize: vi.fn(),
  onClose: vi.fn(),
  Null: () => null,
}));
vi.mock("src/utils/axios", async (original) => ({
  ...(await original()),
  default: { get: h.get, put: h.put },
}));
vi.mock("src/components/iconify", () => ({ default: h.Null }));

import { RowMenuList } from "src/sections/develop/DatasetListCells";

const ROW = { id: "gs", name: "Golden", my_perms: {} };

const mountMenu = ({ canEdit = true } = {}) =>
  render(
    <QueryClientProvider client={new QueryClient()}>
      <RowMenuList
        open
        anchorReference="anchorPosition"
        anchorPosition={{ top: 20, left: 20 }}
        row={ROW}
        actions={{ open: vi.fn(), edit: vi.fn(), organize: h.organize }}
        canEdit={canEdit}
        canDelete
        onClose={h.onClose}
      />
    </QueryClientProvider>,
  );

beforeEach(() => {
  setCollabOfferForTest({ all: false, madeReal: [] });
});
afterEach(() => {
  cleanup();
  setCollabOfferForTest(null);
  vi.clearAllMocks();
});

describe("row menu where metadata and agents are not offered", () => {
  it("keeps Metadata… and Agents…, each disabled and described by the shared line", () => {
    mountMenu();
    for (const label of ["Metadata…", "Agents…"]) {
      const item = screen.getByRole("menuitem", { name: label });
      expect(item).toHaveAttribute("aria-disabled", "true");
      expect(item).toHaveAccessibleDescription(NOT_IN_RELEASE);
      expect(within(item).getByText(NOT_IN_RELEASE)).toBeInTheDocument();
    }
  });

  it("a press on either reaches nothing", () => {
    mountMenu();
    fireEvent.click(screen.getByRole("menuitem", { name: "Metadata…" }));
    fireEvent.click(screen.getByRole("menuitem", { name: "Agents…" }));
    expect(h.organize).not.toHaveBeenCalled();
    expect(h.put).not.toHaveBeenCalled();
  });

  it("a viewer reads the same shared line", () => {
    mountMenu({ canEdit: false });
    expect(
      screen.getByRole("menuitem", { name: "Metadata…" }),
    ).toHaveAccessibleDescription(NOT_IN_RELEASE);
    expect(
      screen.getByRole("menuitem", { name: "Agents…" }),
    ).toHaveAccessibleDescription(NOT_IN_RELEASE);
  });

  it("Move to folder… is offered and asks the list to open its dialog", () => {
    mountMenu();
    const folder = screen.getByRole("menuitem", { name: "Move to folder…" });
    expect(folder).not.toHaveAttribute("aria-disabled");
    fireEvent.click(folder);
    expect(h.organize).toHaveBeenCalledWith("folder", ROW);
  });
});

describe("row menu where the registry makes metadata and agents real", () => {
  it("Metadata… is enabled, with no sentence, and asks the list to open its dialog", () => {
    setCollabOfferForTest({
      all: false,
      madeReal: ["list.organize.metadata", "list.organize.agents"],
    });
    mountMenu();
    const item = screen.getByRole("menuitem", { name: "Metadata…" });
    expect(item).not.toHaveAttribute("aria-disabled");
    expect(within(item).queryByText(NOT_IN_RELEASE)).toBeNull();
    fireEvent.click(item);
    expect(h.organize).toHaveBeenCalledWith("metadata", ROW);
  });
});
