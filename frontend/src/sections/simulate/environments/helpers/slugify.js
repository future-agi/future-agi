// Turn a template name into a shell-safe slug for the CLI scaffold commands.
export const slugify = (name) =>
  String(name || "environment")
    .toLowerCase()
    .replace(/[^a-z0-9]+/g, "-")
    .replace(/^-+|-+$/g, "") || "environment";
