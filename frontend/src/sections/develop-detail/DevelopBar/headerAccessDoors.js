// W4 §9 (M6, the DatasetHeaderBar integration): the header's access doors.
// Pure. Delete needs Manage (owner decision 2026-10-23: owner, admin or a
// Manage grant), New experiment needs run. Before the access view is known
// (or without one) the role decides, and the server's guard still refuses.
import { deleteReason } from "src/sections/common/Access/accessModel";

export const VIEWER_DELETE = "Viewers can't delete datasets";

/**
 * {canDelete, deleteReason, canRun, requestDownload} for the dataset header.
 * `requestDownload` (W4 A should-fix 2): a person without download who may
 * ask for it gets "Request download access" (`{requested}` once asked).
 */
export function headerAccessDoors({ roleDelete, access }) {
  const view = access?.view ?? null;
  const manageWhy = deleteReason(view);
  const roleOk = Boolean(roleDelete);
  const me = view?.me;
  const askable =
    Boolean(me) &&
    !me.perms?.download &&
    (me.requestable ?? []).includes("download");
  const requested = (view?.requests ?? []).some(
    (r) => r.perm === "download" && r.user?.id === me?.user_id,
  );
  return {
    canDelete: roleOk && !manageWhy,
    deleteReason: !roleOk ? VIEWER_DELETE : manageWhy,
    canRun: access?.can?.run !== false,
    requestDownload: askable ? { requested } : null,
  };
}
