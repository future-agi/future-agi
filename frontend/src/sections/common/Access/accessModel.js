// Words for per-dataset access (W4-5). Pure: every sentence the Access
// section, the denial note and the integration gates show comes from here, from
// the server's access view (GET /model-hub/datasets/{id}/access/) or a 403
// denial body. Level keys, labels and permissions come from the server.

export const PERM_ORDER = ["view", "download", "edit", "run"];

/** "Asha Rao", "Asha Rao or Ravi Menon", "Asha Rao, Ravi Menon or Meera Iyer". */
export function namesOr(people = []) {
  const names = people.map((p) => p?.name).filter(Boolean);
  if (names.length <= 1) return names[0] ?? "";
  return `${names.slice(0, -1).join(", ")} or ${names[names.length - 1]}`;
}

/** "view", "view and download", "view, download and edit" … */
export function permPhrase(perms = {}) {
  const have = PERM_ORDER.filter((p) => perms[p]);
  if (have.length <= 1) return have[0] ?? "";
  return `${have.slice(0, -1).join(", ")} and ${have[have.length - 1]}`;
}

/** The first permission the caller lacks (what "Request … access" asks for). */
export const nextMissingPerm = (perms = {}) =>
  PERM_ORDER.find((p) => !perms[p]) ?? null;

/**
 * What "Request … access" asks for: the first permission the server says the
 * caller could still be given (`me.requestable`), else the first one missing.
 */
export const requestPerm = (me) =>
  Array.isArray(me?.requestable)
    ? me.requestable[0] ?? null
    : nextMissingPerm(me?.perms);

const joinOr = (list) =>
  list.length <= 1
    ? list[0] ?? ""
    : `${list.slice(0, -1).join(", ")} or ${list[list.length - 1]}`;

/** The lowest server level that includes `perm`. */
export function levelFor(perm, levels = []) {
  const withPerm = levels.filter((l) => l.perms.includes(perm));
  return withPerm.sort((a, b) => a.perms.length - b.perms.length)[0] ?? null;
}

/** A level that keeps what someone has and adds `perm`. */
export function levelGranting(perm, currentKey, levels = []) {
  const current = levels.find((l) => l.key === currentKey);
  if (current?.perms.includes(perm)) return current;
  const wanted = levelFor(perm, levels);
  if (!current || !wanted) return wanted;
  return current.perms.length > wanted.perms.length ? current : wanted;
}

export const levelLabel = (key, levels = []) =>
  levels.find((l) => l.key === key)?.label ?? key;

/** The caller's own line in the Access section. */
export function meLine(view) {
  const me = view?.me;
  if (!me) return "";
  const ask = namesOr(view.ask);
  const askPart = ask ? ` Ask ${ask} for more.` : "";
  if (!me.perms?.view)
    return `You don't have access to this dataset.${askPart}`;
  if (!nextMissingPerm(me.perms) && me.perms?.manage && me.via !== "admin")
    return "You can view, download, edit and run this dataset, change who has access and delete it.";
  if (!nextMissingPerm(me.perms))
    return "You can view, download, edit and run this dataset.";
  if (Array.isArray(me.requestable) && me.requestable.length === 0) {
    const cannot = PERM_ORDER.filter((p) => !me.perms?.[p]);
    return `You can ${permPhrase(me.perms)} this dataset. Workspace viewers can't ${joinOr(cannot)} datasets.`;
  }
  return `You can ${permPhrase(me.perms)} this dataset.${askPart}`;
}

export const requestLabel = (perm) => `Request ${perm} access`;
export const REQUESTED_LINE = "Requested · an admin sees it under Access";

/**
 * Why the caller can't do `perm` (null when they can). Used as the tooltip
 * of a disabled door and under a refused action.
 */
export function reasonFor(perm, view) {
  const me = view?.me;
  if (!me || me.perms?.[perm]) return null;
  const ask = namesOr(view.ask);
  const askPart = ask ? ` Ask ${ask}.` : "";
  const workspace = view.workspace?.name;
  if (perm === "manage") return `${MANAGE_WHO} can change access.${askPart}`;
  if (me.via === "ceiling" && perm === "download")
    return `Viewers can download only when an admin grants it.${askPart}`;
  if (me.via === "ceiling")
    return `Workspace viewers can't ${perm} datasets.${askPart}`;
  if (me.via === "workspace" && workspace) {
    const level = levelLabel(view.workspace_level, view.levels);
    return `Everyone in ${workspace} has ${level} access to this dataset. You need ${perm} access.${askPart}`;
  }
  if (me.via === "grant") {
    const level = levelLabel(me.level, view.levels);
    return `You have ${level} access to this dataset. You need ${perm} access.${askPart}`;
  }
  return `You need ${perm} access to this dataset.${askPart}`;
}

/** Who holds Manage on a dataset (owner decision 2026-10-23). */
export const MANAGE_WHO =
  "Only the dataset's owner, workspace admins and people with Manage access";

/**
 * Why the caller can't delete the dataset (null when they can, or before the
 * server's access view is known: the server's guard still refuses then).
 * Deleting needs Manage, not Edit: it is irreversible for everyone.
 */
export function deleteReason(view) {
  const me = view?.me;
  if (!me || me.perms?.manage) return null;
  const ask = namesOr(view.ask);
  return `${MANAGE_WHO} can delete it.${ask ? ` Ask ${ask}.` : ""}`;
}

/** The sentence for a server denial body (403). */
export function denialLine(body) {
  if (!body?.missing_permission) return null;
  const ask = namesOr(body.ask);
  const base =
    body.error ?? `You need ${body.missing_permission} access to this dataset.`;
  return ask && body.missing_permission !== "score"
    ? `${base} Ask ${ask}.`
    : base;
}

/** Toasts after a change. */
export function grantToast(name, levelKey, levels) {
  if (!levelKey) return `${name} no longer has their own access`;
  if (levelKey === "none") return `${name} can't open this dataset now`;
  return `${name} can now ${levelLabel(levelKey, levels).toLowerCase()}`;
}
export function workspaceToast(workspace, levelKey, levels) {
  if (levelKey === "none")
    return `Only the people listed and workspace admins can open this dataset now`;
  return `Everyone in ${workspace} can now ${levelLabel(levelKey, levels).toLowerCase()}`;
}
export const requestSentence = (req) =>
  `${req.user?.name ?? "Someone"} asked for ${req.perm} access`;

export const ACCESS_COPY = Object.freeze({
  title: "Access",
  adminsNote:
    "Workspace admins always have full access. Viewers get download only when you grant it.",
  restricted:
    "Restricted · only the people below and workspace admins can open this dataset.",
  addPeople: "Add people",
});

/**
 * The denial body when a read of the dataset itself was refused (403 for
 * view, or a key that can't read datasets), else null. `error` is what
 * utils/axios.js rejects with: the body plus `statusCode`.
 */
export function viewDenialOf(error) {
  if (Number(error?.statusCode) !== 403) return null;
  return ["view", "read_datasets"].includes(error?.missing_permission)
    ? error
    : null;
}

/** The heading of the page a refused reader sees instead of the dataset. */
export const deniedTitle = (denial) =>
  denial?.dataset_name
    ? `You don't have access to “${denial.dataset_name}”`
    : "You don't have access to this dataset";

/**
 * v2.1 g8 (access:grant): who "+ Add people" offers — workspace members
 * without their own grant, never the caller, and never a workspace Owner or
 * Admin (admins always have full access; a grant would show a level that
 * never applies). Pure.
 */
export const ALWAYS_FULL_ROLES = Object.freeze(["Owner", "Admin"]);
export function addablePeople(
  members = [],
  { granted = new Set(), meId } = {},
) {
  return (members ?? []).filter(
    (m) =>
      m?.id &&
      !granted.has(m.id) &&
      m.id !== meId &&
      !ALWAYS_FULL_ROLES.includes(m.organization_role),
  );
}
