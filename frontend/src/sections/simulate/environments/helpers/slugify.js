// Turn a name into a shell-safe slug for the CLI scaffold commands, or with `_` into an actor id.
export const slugify = (
  name,
  { separator = "-", fallback = "environment" } = {},
) =>
  String(name || fallback)
    .toLowerCase()
    .normalize("NFKD")
    .replace(/[̀-ͯ]/g, "")
    .replace(/[^a-z0-9]+/g, separator)
    .replace(new RegExp(`^${separator}+|${separator}+$`, "g"), "") || fallback;
