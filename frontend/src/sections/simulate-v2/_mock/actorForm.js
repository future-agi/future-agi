/*
  Actor form rules (Linear TH-8927, platform PR #3698).

  Every actor follows one id rule — 1 to 64 lowercase letters, digits or
  underscores — and needs a name (at most 80 characters); its role is at most
  160. The form checks as you type, so Save stays off until there is a name and
  an id that is valid and free.
*/

export const ID_MAX = 64;
export const NAME_MAX = 80;
export const ROLE_MAX = 160;
export const ID_RE = /^[a-z0-9_]{1,64}$/;

/**
 * An id from a name: accents folded, lowercase, anything else an underscore.
 * "José Núñez" → "jose_nunez". Empty when the name has nothing to build one from.
 */
export const idFrom = (name) =>
  String(name || "")
    .normalize("NFD")
    .replace(/[̀-ͯ]/g, "")
    .toLowerCase()
    .replace(/[^a-z0-9]+/g, "_")
    .replace(/^_+|_+$/g, "")
    .slice(0, ID_MAX)
    .replace(/_+$/g, "");

/** The first of `base`, `base_2`, `base_3`… that is not taken. */
export const freeIdFrom = (name, taken = []) => {
  const base = idFrom(name);
  if (!base) return "";
  const used = new Set(taken);
  if (!used.has(base)) return base;
  for (let n = 2; n < 1000; n += 1) {
    const suffix = `_${n}`;
    const id = `${base.slice(0, ID_MAX - suffix.length)}${suffix}`;
    if (!used.has(id)) return id;
  }
  return base;
};

/** What is wrong with an id, in a few words — null when it is fine. */
export const idProblemOf = (id, taken = []) => {
  if (!id) return "Give it an id";
  if (id.length > ID_MAX) return `At most ${ID_MAX} characters`;
  if (!ID_RE.test(id)) return "Lowercase letters, digits or underscores only";
  if (taken.includes(id)) return "That id is already used by another actor";
  return null;
};

/**
 * Every problem in the form, by field: { name?, id?, role? }. `takenIds` is
 * every other actor's id; `checkId` is off for an existing actor, whose id is
 * fixed (a stored one may predate the rule).
 */
export const problemsOf = (form, { takenIds = [], checkId = true } = {}) => {
  const out = {};
  const name = String(form?.name || "").trim();
  if (!name) out.name = "Give it a name";
  else if (name.length > NAME_MAX) out.name = `At most ${NAME_MAX} characters`;
  if (checkId) {
    const id = idProblemOf(form?.id, takenIds);
    if (id) out.id = id;
  }
  if (String(form?.role || "").trim().length > ROLE_MAX)
    out.role = `At most ${ROLE_MAX} characters`;
  return out;
};

export const isValidForm = (form, opts) =>
  Object.keys(problemsOf(form, opts)).length === 0;
