// @vitest-environment jsdom
// W9 A0 review fix: deleting a saved view shows `View "<name>" deleted`
// with Undo; Undo restores that same record by id (useSavedViews.restore),
// never re-creates it as the person who clicked; a refused restore says
// the server's sentence in an error toast.
import { describe, expect, it, vi } from "vitest";
import { deleteViewWithUndo } from "../SavedViewsMenu";

const VIEW = {
  id: "rv-asha-shared",
  name: "Asha · refunds triage",
  state: { q: "refund" },
  shared: true,
  mine: false,
  created_by: "Asha Rao",
  created_at: "2026-10-21T10:00:00.000Z",
};

function deps({ restoreWith } = {}) {
  const calls = [];
  const remove = vi.fn().mockResolvedValue({ deleted: true });
  const restore = vi.fn(
    restoreWith ??
      (() => Promise.resolve({ ...VIEW, can_delete: true, shared: true })),
  );
  const toast = vi.fn((message, opts) => calls.push({ message, opts }));
  return { remove, restore, toast, calls };
}

describe("deleteViewWithUndo", () => {
  it("deletes, then Undo restores the same id (no create)", async () => {
    const d = deps();
    await deleteViewWithUndo(VIEW, d);
    expect(d.remove).toHaveBeenCalledWith("rv-asha-shared");
    expect(d.calls[0].message).toBe('View "Asha · refunds triage" deleted');
    expect(d.calls[0].opts.key).toBe("view-deleted");
    expect(d.restore).not.toHaveBeenCalled();
    await d.calls[0].opts.undo.onUndo();
    expect(d.restore).toHaveBeenCalledTimes(1);
    expect(d.restore).toHaveBeenCalledWith("rv-asha-shared");
    expect(d.calls).toHaveLength(1); // no error toast
  });

  it("no toast when the delete is refused", async () => {
    const d = deps();
    d.remove.mockRejectedValueOnce({ result: "Only Asha Rao or an admin…" });
    await expect(deleteViewWithUndo(VIEW, d)).rejects.toBeTruthy();
    expect(d.toast).not.toHaveBeenCalled();
  });

  it("a refused restore shows the server's sentence", async () => {
    const d = deps({
      restoreWith: () =>
        Promise.reject({
          result: 'A view named "Asha · refunds triage" already exists.',
          statusCode: 400,
        }),
    });
    await deleteViewWithUndo(VIEW, d);
    await d.calls[0].opts.undo.onUndo();
    expect(d.calls[1]).toEqual({
      message: 'A view named "Asha · refunds triage" already exists.',
      opts: { variant: "error", key: "view-restore" },
    });
  });

  it("a restore that fails without a sentence says so plainly", async () => {
    const d = deps({
      restoreWith: () => Promise.reject(new Error("network")),
    });
    await deleteViewWithUndo(VIEW, d);
    await d.calls[0].opts.undo.onUndo();
    expect(d.calls[1].message).toBe(
      'Couldn\'t bring back "Asha · refunds triage". Try again.',
    );
  });
});
