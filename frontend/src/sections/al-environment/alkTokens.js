/**
 * The harness page is monospaced throughout its chrome. That terminal character is the
 * identity of the tool, and our sans would flatten it — so we keep their stack, scoped
 * to this section rather than applied globally.
 */
export const ALK_MONO = `ui-monospace, SFMono-Regular, "SF Mono", Menlo, Consolas, monospace`;

/**
 * Their --paper/--card/--ink/--muted/--hair map onto our theme one-for-one, so components
 * use `background.default`, `background.paper`, `text.primary`, `text.secondary` and
 * `divider` directly through `sx` — no indirection needed, and the page follows our
 * light/dark switcher for free. Their semantic --world/--refuse/--fail are likewise just
 * our `success`/`warning`/`error`.
 */
