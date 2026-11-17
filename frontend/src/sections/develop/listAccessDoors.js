// W4 §9 (M6, the dataset list integration): the list row's doors follow the
// caller's access to that dataset (`my_perms` on each get-datasets row).
// Pure. The role decides first; then the row's access; without `my_perms`
// (a build without it) the role alone decides, as before.
import { MANAGE_WHO } from "src/sections/common/Access/accessModel";

export const LIST_DENIED = Object.freeze({
  run: "You need run access to this dataset",
  edit: "You need edit access to this dataset",
  download: "You need download access to this dataset",
  manage: `${MANAGE_WHO} can delete it`,
});

/** {ok, reason} for a list door. */
export function listDoor(row, perm, roleOk = true, roleReason) {
  if (!roleOk) return { ok: false, reason: roleReason };
  if (row?.my_perms?.[perm] === false)
    return { ok: false, reason: LIST_DENIED[perm] };
  return { ok: true, reason: undefined };
}
