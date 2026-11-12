// Command providers for the ⌘K palette (R3 G0 §B6): global actions, Go to,
// datasets (the existing list hook), runs of the dataset in the route, rows
// of that dataset (`T26`, `Row 12`, or free text through the existing table
// query). Hooks; each returns Command[] (commandRegistry.js).
import { useMemo } from "react";
import { useQuery } from "@tanstack/react-query";
import axios, { endpoints } from "src/utils/axios";
import { useDevelopDatasetList } from "src/api/develop/develop-detail";
import { useDatasetRuns } from "src/api/develop/runs";
import { paths } from "src/routes/paths";
import { taskCompareHref } from "src/sections/common/Analysis/analysisLinks";
import { prototypeCommands, prototypeState } from "./simulateCommands";
import { shareLinkOf } from "src/sections/experiment-detail/results/shareLink";
import { launchCommandShown } from "./commandRegistry";
import { isSurfaceShown } from "src/launch/launchSwitch";

// Static gate (HO S2): prototype-only Go to targets fold away elsewhere.
const PROTOTYPE_BUILD =
  import.meta.env.VITE_ENVIRONMENT === "local" &&
  import.meta.env.VITE_PROTOTYPE_MODE === "true";

const UUID = "[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}";
const DATASET_RE = new RegExp(`^/dashboard/develop/(${UUID})(?:/|$)`, "i");

/** The dataset id in a pathname (`/dashboard/develop/<id>/…`), or null. */
export const datasetIdOfPath = (pathname) =>
  DATASET_RE.exec(pathname ?? "")?.[1] ?? null;

/** `T26` / `t 26` / `Row 12` / `#12` → 12; else null. */
export function rowNumberOf(query) {
  const m = /^\s*(?:t|row|#)\s*(\d{1,6})\s*$/i.exec(query ?? "");
  return m ? Number(m[1]) : null;
}

/**
 * Copy link to this view: the live URL. The legacy Simple/Advanced `ui`
 * param is never copied (one view, G-04). L7c g1 (cmd:view.copyLink): in a
 * prototype build the link keeps this tab's `proto_stage` (shareLinkOf), so
 * it opens on the stage where this view's data exists.
 */
export function viewLink() {
  const url = new URL(window.location.href);
  url.searchParams.delete("ui");
  return shareLinkOf(url.toString());
}

export { PROTO_ROLE_KEY, protoRole } from "./simulateCommands";

/**
 * Global actions + Go to. `ctx`: {openSheet, copy, toggleDark, isDark,
 * askFalcon, hasTips, showTips}. Page verbs (`View options…`, `Reset view`,
 * `Columns…`, `Show <section>`) are registered by the one-view primitives
 * while a page shows them.
 */
export function useGlobalCommands(ctx) {
  const { isDark, hasTips } = ctx;
  return useMemo(() => {
    const cmds = [
      ...(hasTips
        ? [
            {
              id: "help.tips",
              group: "Settings",
              label: "Show tips on this page",
              keywords: ["tip", "hint", "help", "learn", "guide"],
              icon: "mdi:lightbulb-on-outline",
              hint: "Brings back the tips you hid here",
              run: () => ctx.showTips(),
            },
          ]
        : []),
      {
        id: "help.shortcuts",
        group: "Settings",
        label: "Keyboard shortcuts",
        keywords: ["keys", "hotkeys", "help", "?"],
        icon: "mdi:keyboard-outline",
        shortcut: "help",
        run: () => ctx.openSheet({ tab: "shortcuts" }),
      },
      {
        id: "help.glossary",
        group: "Settings",
        label: "Glossary: what the words mean",
        keywords: ["terms", "jargon", "define", "meaning", "what does"],
        icon: "mdi:book-open-variant",
        run: () => ctx.openSheet({ tab: "glossary" }),
      },
      {
        id: "view.copyLink",
        group: "Actions",
        label: "Copy link to this view",
        keywords: ["share", "url", "permalink"],
        icon: "mdi:link-variant",
        run: () => ctx.copy(viewLink(), "Link copied"),
      },
      {
        id: "theme.toggle",
        group: "Settings",
        label: isDark ? "Switch to light mode" : "Switch to dark mode",
        keywords: ["dark", "light", "theme", "toggle dark mode"],
        icon: isDark ? "mdi:white-balance-sunny" : "mdi:weather-night",
        run: () => ctx.toggleDark(),
      },
      {
        id: "falcon.ask",
        group: "Actions",
        label: "Ask Falcon…",
        keywords: ["ai", "assistant", "chat", "help"],
        icon: "mdi:creation-outline",
        run: () => ctx.askFalcon(""),
      },
      ...[
        ["Datasets", paths.dashboard.develop, "mdi:database-outline"],
        ["Prompts", paths.dashboard.workbench, "mdi:message-text-outline"],
        ["Evals", paths.dashboard.evals, "mdi:check-decagram-outline"],
        ["Tracing", paths.dashboard.observe, "mdi:chart-timeline-variant"],
        [
          "Annotation queues",
          paths.dashboard.annotations.queues,
          "mdi:account-check-outline",
        ],
        // L7c g1 (nav:leaderboards): the nav's Leaderboards (prototype only).
        ...(PROTOTYPE_BUILD
          ? [
              [
                "Leaderboards",
                `${paths.dashboard.develop}/leaderboards`,
                "mdi:podium",
              ],
            ]
          : []),
        ["Settings", "/dashboard/settings/profile-settings", "mdi:cog-outline"],
      ]
        .filter(([, href]) => Boolean(href))
        .map(([label, href, icon]) => ({
          id: `go.${label.toLowerCase().replace(/\s+/g, "-")}`,
          group: "Go to",
          label: `Go to ${label}`,
          keywords: [label.toLowerCase(), "open", "navigate"],
          icon,
          href,
        })),
    ];
    // Prototype only: View as …, Simulate: …, Demo stage (simulateCommands.js).
    // The env check is static, so other builds drop these items and their
    // proto_* strings (HO S2 leak scan).
    if (
      import.meta.env.VITE_ENVIRONMENT === "local" &&
      import.meta.env.VITE_PROTOTYPE_MODE === "true"
    )
      cmds.push(...prototypeCommands(prototypeState()));
    // L6 launch switch: no global verb opens a hidden surface (Glossary).
    return cmds.filter(launchCommandShown);
    // ctx functions are stable per render of the palette
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [isDark, hasTips]);
}

/** Datasets from the existing list hook (searched server-side). */
export function useDatasetCommands(query, { enabled = true } = {}) {
  const q = (query ?? "").trim();
  const { data } = useDevelopDatasetList(q.length >= 2 ? q : undefined, [], {
    enabled,
  });
  return useMemo(
    () =>
      (data ?? []).slice(0, 8).map((d) => ({
        id: `dataset.${d.datasetId ?? d.id}`,
        group: "Datasets",
        label: d.name,
        keywords: ["dataset", d.kind === "task_suite" ? "task suite" : ""],
        icon: "mdi:database-outline",
        href: `${paths.dashboard.develop}/${d.datasetId ?? d.id}`,
      })),
    [data],
  );
}

/** The kind (`rows` / `task_suite` / `conversations`) of a dataset. */
export function useDatasetKind(datasetId, { enabled = true } = {}) {
  const { data } = useDevelopDatasetList(undefined, [], {
    enabled: Boolean(datasetId) && enabled,
  });
  return (
    (data ?? []).find((d) => (d.datasetId ?? d.id) === datasetId)?.kind ?? null
  );
}

const runCaption = (r) =>
  [r.label, r.name].filter(Boolean).join(" ") || r.run_id;

/** Runs of the dataset in the route (`2D` finds `#2D`). */
export function useRunCommands(datasetId, { enabled = true } = {}) {
  // L6 launch switch: the runs list (R1) is absent on dev; no Runs group,
  // no request, until a lane ships it.
  const runsShown = isSurfaceShown("palette.runs");
  const { data } = useDatasetRuns(datasetId, {
    enabled: Boolean(datasetId) && enabled && runsShown,
  });
  return useMemo(
    () =>
      (runsShown ? data?.results ?? [] : []).map((r) => ({
        id: `run.${r.run_id}`,
        group: "Runs",
        label: runCaption(r),
        keywords: [
          String(r.label ?? "").replace("#", ""),
          r.experiment_name,
          "run",
        ].filter(Boolean),
        hint: r.experiment_name ?? undefined,
        icon: "mdi:play-circle-outline",
        href: r.experiment_id
          ? `${paths.dashboard.develop}/experiment/${r.experiment_id}/data`
          : undefined,
      })),
    [data, runsShown],
  );
}

const previewOf = (row, columns) => {
  for (const c of columns ?? []) {
    const id = c.id ?? c.column_id;
    const v = row?.[id]?.cell_value ?? row?.[id];
    if (typeof v === "string" && v.trim()) return v.trim();
  }
  return "";
};

/**
 * Rows of the dataset in the route: `T26` / `Row 12` jump by position;
 * other text searches cells through the existing table query.
 */
/**
 * The get-dataset-table params for a palette row query. `search` is a JSON
 * object like the grid's (dev's DatasetTableQuerySerializer refuses plain
 * text with 400 "Value must be valid JSON."; the prototype mock reads both).
 */
export function rowSearchParams(q) {
  const n = rowNumberOf(q);
  return n
    ? { page_size: 1, current_page_index: n - 1 }
    : {
        page_size: 5,
        current_page_index: 0,
        search: JSON.stringify({ key: q, type: ["text"] }),
      };
}

export function useRowCommands(
  datasetId,
  query,
  { enabled = true, kind } = {},
) {
  const q = (query ?? "").trim();
  const n = rowNumberOf(q);
  const params = rowSearchParams(q);
  const on = Boolean(datasetId) && enabled && (n != null || q.length >= 2);
  const { data } = useQuery({
    queryKey: ["palette-rows", datasetId, params],
    queryFn: () =>
      axios.get(endpoints.develop.getDatasetDetail(datasetId), { params }),
    select: (res) => res.data?.result,
    enabled: on,
    staleTime: 30_000,
  });
  return useMemo(
    () =>
      on && data
        ? rowCommandsOf({
            rows: data.table ?? [],
            cols: data.column_config ?? data.columnConfig ?? [],
            n,
            q,
            kind,
            datasetId,
          })
        : [],
    [on, data, n, q, kind, datasetId],
  );
}

/**
 * Palette commands for table rows. A task jumped to by number (`T26`) on a
 * task suite also gets `Compare runs side by side`, listed first: the
 * workspace fills its lanes from the pinned baseline's experiment.
 * L7c g1 (palette:search-results): `Row 200` jumps to the 200th row of the
 * view (like the grid's Go to row), so it is labelled `#200`, the Row view's
 * own name for a position, not `Row 200` (a row's permanent number).
 */
export function rowCommandsOf({ rows, cols, n, q, kind, datasetId }) {
  const head = n == null ? null : kind === "task_suite" ? `T${n}` : `#${n}`;
  return rows.slice(0, 5).flatMap((row) => {
    const id = row.row_id ?? row.id;
    const text = previewOf(row, cols);
    const open = {
      id: `row.${id}`,
      group: "Rows",
      label: head ? `${head}${text ? ` · ${text}` : ""}` : text || id,
      keywords: [q, head ?? "", "row", "task"].filter(Boolean),
      icon: "mdi:table-row",
      href: `${paths.dashboard.develop}/${datasetId}?tab=data&row=${id}`,
    };
    // L6: side by side is a hidden page in a launch build.
    if (kind !== "task_suite" || !head || !isSurfaceShown("exp.compare"))
      return [open];
    return [
      {
        id: `row.${id}.side-by-side`,
        group: "Rows",
        label: `${head} · Compare runs side by side`,
        keywords: [q, head, "side by side", "compare", "task"],
        icon: "mdi:compare-horizontal",
        href: taskCompareHref({ datasetId, rowId: id }),
      },
      open,
    ];
  });
}
