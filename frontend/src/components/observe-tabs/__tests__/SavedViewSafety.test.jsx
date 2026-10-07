import React from "react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { render, screen, fireEvent, waitFor } from "src/utils/test-utils";
import userEvent from "@testing-library/user-event";
import TabContextMenu from "../TabContextMenu";
import CustomViewTab from "../CustomViewTab";
import DeleteViewDialog from "../DeleteViewDialog";
import ConflictDialog from "../ConflictDialog";

vi.mock("notistack", () => ({ enqueueSnackbar: vi.fn() }));

const update = vi.fn();
const remove = vi.fn();
const duplicate = vi.fn();
vi.mock("src/api/project/saved-views", async (original) => ({
  ...(await original()),
  useUpdateSavedView: () => ({ mutate: update }),
  useDeleteSavedView: () => ({ mutate: remove }),
  useDuplicateSavedView: () => ({ mutate: duplicate }),
}));
vi.mock("src/sections/project/context/ObserveHeaderContext", () => ({
  useObserveHeader: () => ({ getViewConfig: () => ({ columns: [] }), getViewRevision: () => 3 }),
}));

const own = { id: "view-1", name: "Errors", revision: 3, visibility: "personal", is_owner: true, can_edit: true, can_delete: true };
const menu = (view = own, extra = {}) => render(<TabContextMenu view={view} projectId="p" projectName="Checkout Agent" activeTab="view-view-1" anchorPosition={{ x: 20, y: 20 }} onClose={vi.fn()} onRename={vi.fn()} onTabChange={vi.fn()} {...extra} />);
const tab = (view = own, extra = {}) => render(<CustomViewTab view={view} projectName="Checkout Agent" isActive onClick={vi.fn()} onClose={vi.fn()} onContextMenu={vi.fn()} onRenameSubmit={vi.fn()} onRenameCancel={vi.fn()} {...extra} />);

beforeEach(() => vi.clearAllMocks());

describe("saved view ownership and consent", () => {
  it("offers the complete owner menu", () => {
    menu();
    expect(screen.getAllByRole("menuitem").map((node) => node.textContent)).toEqual(["Rename", "Duplicate", "Share with project", "Copy link", "Delete"]);
  });
  it.each([false, true])("non-owner menu, admin cleanup = %s", (admin) => {
    menu({ ...own, visibility: "project", is_owner: false, can_edit: false, can_delete: admin });
    expect(screen.getAllByRole("menuitem").map((node) => node.textContent)).toEqual(admin ? ["Save a copy", "Copy link", "DeleteAdmin"] : ["Save a copy", "Copy link"]);
  });
  it("requires explicit project consent and cancel sends no mutation", async () => {
    menu();
    fireEvent.click(screen.getByRole("menuitem", { name: "Share with project" }));
    expect(await screen.findByRole("dialog", { name: "Share “Errors” with Checkout Agent?" })).toBeInTheDocument();
    expect(screen.getByText(/Sharing does not change what data they can access/)).toBeInTheDocument();
    expect(update).not.toHaveBeenCalled();
    fireEvent.click(screen.getByRole("button", { name: "Cancel" }));
    expect(update).not.toHaveBeenCalled();
  });
  it("saves dirty changes before sharing and carries the new revision", async () => {
    update
      .mockImplementationOnce((_payload, callbacks) => callbacks.onSuccess({ data: { result: { revision: 4 } } }))
      .mockImplementationOnce((_payload, callbacks) => callbacks.onSuccess());
    menu(own, { isDirty: true });
    fireEvent.click(screen.getByRole("menuitem", { name: "Share with project" }));
    fireEvent.click(await screen.findByRole("button", { name: "Save changes first" }));
    expect(update).toHaveBeenNthCalledWith(1, { id: "view-1", expected_revision: 3, config: { columns: [] } }, expect.any(Object));
    expect(update).toHaveBeenNthCalledWith(2, { id: "view-1", expected_revision: 4, visibility: "project" }, expect.any(Object));
  });

  it("shares only the saved version with a revision when dirty", async () => {
    menu(own, { isDirty: true });
    fireEvent.click(screen.getByRole("menuitem", { name: "Share with project" }));
    expect(await screen.findByRole("button", { name: "Save changes first" })).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "Share saved version" }));
    expect(update).toHaveBeenCalledWith({ id: "view-1", expected_revision: 3, visibility: "project" }, expect.any(Object));
    expect(update.mock.calls[0][0]).not.toHaveProperty("config");
  });
  it("warns that making personal removes teammates' access", async () => {
    menu({ ...own, visibility: "project" });
    fireEvent.click(screen.getByRole("menuitem", { name: "Make personal" }));
    expect(await screen.findByText(/will lose access to this view the next time they refresh/)).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "Make personal" }));
    expect(update).toHaveBeenCalledWith({ id: "view-1", expected_revision: 3, visibility: "personal" }, expect.any(Object));
  });
  it("copies a canonical identity link with no draft filter params", async () => {
    const writeText = vi.fn().mockResolvedValue();
    Object.defineProperty(navigator, "clipboard", { configurable: true, value: { writeText } });
    menu();
    fireEvent.click(screen.getByRole("menuitem", { name: "Copy link" }));
    await waitFor(() => expect(writeText).toHaveBeenCalled());
    const link = new URL(writeText.mock.calls[0][0]);
    expect([...link.searchParams]).toEqual([["tab", "view-view-1"]]);
  });
  it("hides destructive and rename controls for a non-owner", () => {
    tab({ ...own, can_edit: false, can_delete: false, is_owner: false, visibility: "project" }, { isRenaming: true });
    expect(screen.queryByRole("button", { name: "Delete Errors" })).not.toBeInTheDocument();
    expect(screen.queryByRole("textbox")).not.toBeInTheDocument();
    expect(screen.getByRole("tab")).toHaveAccessibleName(/Shared · owned by a teammate/);
  });
  it("the close icon requests confirmation without issuing a delete", () => {
    const close = vi.fn();
    tab(own, { onClose: close });
    fireEvent.click(screen.getByRole("button", { name: "Delete Errors" }));
    expect(close).toHaveBeenCalledWith("view-1");
    expect(remove).not.toHaveBeenCalled();
  });
  it("delete confirmation keeps the view until acknowledgement", () => {
    const done = vi.fn(); const close = vi.fn();
    render(<DeleteViewDialog view={{ ...own, visibility: "project" }} projectId="p" projectName="Checkout Agent" onClose={close} onDeleted={done} />);
    expect(screen.getByText(/teammates will lose this view/)).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "Delete" }));
    expect(remove).toHaveBeenCalledWith({ id: "view-1", expected_revision: 3 }, expect.any(Object));
    expect(done).not.toHaveBeenCalled();
    remove.mock.calls[0][1].onSuccess();
    expect(done).toHaveBeenCalledWith("view-1");
    expect(close).toHaveBeenCalled();
  });
  it("delete transport failure keeps confirmation open and offers Retry", async () => {
    remove.mockImplementation((_variables, callbacks) => callbacks.onError(new Error("Offline")));
    const done = vi.fn();
    render(<DeleteViewDialog view={own} projectId="p" onClose={vi.fn()} onDeleted={done} />);
    fireEvent.click(screen.getByRole("button", { name: "Delete" }));
    expect(await screen.findByRole("button", { name: "Retry" })).toBeInTheDocument();
    expect(done).not.toHaveBeenCalled();
  });
  it("context menu is keyboard reachable from a tab", async () => {
    const open = vi.fn();
    tab(own, { onContextMenu: open });
    screen.getByRole("tab").focus();
    await userEvent.keyboard("{Shift>}{F10}{/Shift}");
    expect(open).toHaveBeenCalledWith(expect.any(Number), expect.any(Number), "view-1");
  });
  it("Escape returns focus to the originating tab", async () => {
    const close = vi.fn();
    render(<><button data-view-id="view-1">Source tab</button><TabContextMenu view={own} projectId="p" anchorPosition={{ x: 20, y: 20 }} onClose={close} onRename={vi.fn()} onTabChange={vi.fn()} /></>);
    await userEvent.keyboard("{Escape}");
    await waitFor(() => expect(close).toHaveBeenCalled());
  });
});

describe("conflict recovery", () => {
  it("requires a second explicit discard confirmation", () => {
    const reload = vi.fn();
    render(<ConflictDialog open viewName="Errors" onClose={vi.fn()} onReload={reload} onSaveCopy={vi.fn()} />);
    fireEvent.click(screen.getByRole("button", { name: "Reload latest (discards your changes)" }));
    expect(reload).not.toHaveBeenCalled();
    fireEvent.click(screen.getByRole("button", { name: "Discard and reload" }));
    expect(reload).toHaveBeenCalledOnce();
  });
  it.each(["Cancel", "Save as copy"])("%s does not discard the draft", (choice) => {
    const reload = vi.fn(); const close = vi.fn(); const copy = vi.fn();
    render(<ConflictDialog open viewName="Errors" onClose={close} onReload={reload} onSaveCopy={copy} />);
    fireEvent.click(screen.getByRole("button", { name: choice }));
    expect(reload).not.toHaveBeenCalled();
    expect(choice === "Cancel" ? close : copy).toHaveBeenCalledOnce();
  });
});
