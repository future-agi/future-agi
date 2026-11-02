---
name: building-ui
description: Use whenever you create or change frontend UI in this repo — pages, tables, dialogs, drawers, forms, filters, status labels, charts, or empty, loading and error states. Builds it with the Future AGI Design System so every screen looks the same whoever wrote it.
---

# Building UI with the Future AGI Design System

The design system is the standard for every screen. Docs, with every component shown in light and dark, a Part · Spec table and Do / Don't: https://cuddly-disco-y9y51ke.pages.github.io/design-system/

## Before writing code

1. Find the component you need in the docs (table, dialog, drawer, tabs, filter, badge, empty state, page header, metric strip…) and build to its spec.
2. Use the shared component if one exists. Never rebuild one on a screen, and never copy an older screen's styling — older screens may predate the system.

## Use these, don't hand-roll

| Need | Use |
|---|---|
| Page title, one line of description, actions | `src/components/page-header` |
| Section heading on a page | `src/components/section` |
| A page's few headline numbers | `src/components/metric-strip` |
| A list of records | MUI `Table` or `src/components/data-table`; AG Grid takes `src/theme/ag-theme.js`. Row and header heights come from `ROW_HEIGHT` and `TABLE_HEADER_HEIGHT` in `src/theme/sizes.js` |
| Search, filters and Columns above a table | `src/components/list-toolbar` wrapping `FormSearchField` (`src/components/FormSearchField/FormSearchField`) for search, and a MUI `TextField select size="small"` for a plain status filter ("All statuses"). Use `src/components/filter` (`FilterButton`) only for multi-property filtering |
| Rows selected | `src/components/selection-bar` |
| Any status (run, call, check…) | `src/components/status-badge`, mapped through `src/utils/runStatus` — one status map for the whole product |
| Empty, loading, error | `src/components/empty-state`, `src/components/content-skeleton`, `src/components/error-state` |
| A row's ⋯ menu | `IconButton` with `solar:menu-dots-bold` opening a MUI `Menu` / `MenuItem` (16px `solar:*-linear` icon in a `ListItemIcon`). It holds every row action |
| A toast | `enqueueSnackbar` from `notistack` |
| A popup | `ClosableDialog` or `ConfirmDialog` from `src/components/custom-dialog` — every popup has a close ×. `ConfirmDialog` takes `title` and `content` as plain strings and your confirm button as `action`; for a delete or revoke use `variant="contained" color="error"` with the verb |

## Where a new page goes

- Pages are lazy-imported and routed in `src/routes/sections/dashboard.jsx`. Settings pages live in `settingsRoute`, whose paths are relative to `/dashboard/settings` (write `path: "api-keys"`, not `"settings/api-keys"`).
- One primary button per view: in a first-run empty state the empty block's button is the primary, so don't repeat it in the page header.
- Show each state by URL param (`?state=loading|error|empty`) so reviewers can reach it.

## Rules

- **Colour:** theme roles only — `text.primary / secondary / tertiary`, `divider`, `background.paper`, `action.hover / selected`, `status.*`, and `palette.data[]` for chart series. No hex or rgba values, no `isDark ? … : …`. When dark mode matters, read `theme.palette.mode`.
- **One accent:** purple only for the main action, focus and AI. Status colours only ever mean status. Put colour on a change (up or down), not on the number itself.
- **Type:** theme variants only — `m1` titles (15/600), `m3` section headings (13/500), `s1` body and table values (12), `s3` secondary text (11). Weights 400, 500, 600; never 700. No hard-coded `fontSize`.
- **Spacing and shape:** theme spacing (`p: 1.5`, `gap: 1`) and theme radii (4 / 6 / 8). No `calc(100vh - N)` heights.
- **Surfaces:** flat with a 1px `divider` border. Shadows only on menus, dialogs, drawers and toasts. Tables sit on the page with hairline rows — never inside a card.
- **Icons:** Iconify Solar set (`solar:*-linear`, `solar:*-bold`). No mdi, mingcute or eva icons.
- **Behaviour:** one primary button per view; one side drawer open at a time; every screen designs its empty, loading and error states; works in light and dark.
- **Words:** plain sentences a user would say; name things the way the rest of the product names them.

## Before you finish

1. `cd frontend && npx eslint -c .eslintrc.ds.cjs <changed files>` — fix every `[DS]` message.
2. `cd frontend && npx eslint <changed files>` (not `yarn lint`, which lints all of `src`).
3. Open the screen in light and in dark and compare each component you used with its docs page.
