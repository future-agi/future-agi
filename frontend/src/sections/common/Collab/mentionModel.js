// W4-1 (Lane B): the @ picker, pure. The member list is the one org users
// endpoint; who can view the dataset comes from the access read.
import { mentionToken } from "./commentModel";

// The stored token (commentModel's format): `@[Meera Iyer](user:<id>)`.
const TOKEN_RE = /@\[([^\]\n]{1,120})\]\(user:([^)\s]{1,80})\)/g;
const escapeRe = (s) => s.replace(/[.*+?^${}()|[\]\\]/g, "\\$&");

/**
 * The mention being typed at the caret: `{query, start}` when the text
 * before the caret ends in `@` + letters (no space yet), else `null`.
 * `start` is the index of the `@`.
 */
export function activeMention(text, caret) {
  const s = String(text ?? "");
  const end = Number.isFinite(caret) ? caret : s.length;
  const before = s.slice(0, end);
  const m = /(^|[\s(])@([\p{L}\p{N}._-]{0,40})$/u.exec(before);
  if (!m) return null;
  return { query: m[2], start: end - m[2].length - 1 };
}

/**
 * Members matching a query (name or email, case-insensitive), people who
 * can view first, each `{...member, noAccess}`. `canView(id)` → bool.
 */
export function mentionCandidates(members, query, canView = () => true) {
  const q = String(query ?? "")
    .trim()
    .toLowerCase();
  return (members ?? [])
    .filter((m) => m && m.id && m.name)
    .filter(
      (m) =>
        !q ||
        m.name.toLowerCase().includes(q) ||
        String(m.email ?? "")
          .toLowerCase()
          .includes(q),
    )
    .map((m) => ({ ...m, noAccess: !canView(m.id) }))
    .sort((a, b) => Number(a.noAccess) - Number(b.noAccess));
}

/**
 * Put a member's token in place of the `@query` being typed. Returns
 * `{text, caret}` (the caret after the token and one space).
 */
export function insertMention(text, mention, member) {
  const s = String(text ?? "");
  const token = `${mentionToken(member)} `;
  const start = mention?.start ?? s.length;
  const end = start + 1 + (mention?.query?.length ?? 0);
  return {
    text: s.slice(0, start) + token + s.slice(end),
    caret: start + token.length,
  };
}

/**
 * The composer shows people by name: a stored body becomes `{text,
 * mentions}` with each token written `@Meera Iyer` and `mentions` =
 * `[{id, name}]` (who those names are).
 */
export function displayOfBody(stored) {
  const mentions = [];
  const text = String(stored ?? "").replace(TOKEN_RE, (_, name, id) => {
    if (!mentions.some((m) => m.id === id)) mentions.push({ id, name });
    return `@${name}`;
  });
  return { text, mentions };
}

/**
 * The body the server stores: every `@Name` of a picked person still in
 * the text (at a word start, not followed by more of a name) becomes their
 * token. Longer names win (`@Meera Iyer` before `@Meera`).
 */
export function storedOfDisplay(text, mentions) {
  const s = String(text ?? "");
  const list = (mentions ?? [])
    .filter((m) => m?.id && m?.name)
    .sort((a, b) => b.name.length - a.name.length);
  if (!list.length) return s;
  const re = new RegExp(
    `(^|[\\s(])@(${list.map((m) => escapeRe(m.name)).join("|")})(?![\\p{L}\\p{N}_])`,
    "gu",
  );
  return s.replace(re, (_, pre, name) => {
    const m = list.find((x) => x.name === name);
    return `${pre}${mentionToken(m)}`;
  });
}

/**
 * Put `@Name ` in place of the `@query` being typed (what the composer
 * shows; `storedOfDisplay` writes the token). Returns `{text, caret}`.
 */
export function insertMentionName(text, mention, member) {
  const s = String(text ?? "");
  const shown = `@${member.name} `;
  const start = mention?.start ?? s.length;
  const end = start + 1 + (mention?.query?.length ?? 0);
  return {
    text: s.slice(0, start) + shown + s.slice(end),
    caret: start + shown.length,
  };
}

/** The note shown when picking someone who can't view the dataset. */
export const noAccessNote = (name) =>
  `${name} can't view this dataset. An admin can give access in Dataset settings › Access.`;
