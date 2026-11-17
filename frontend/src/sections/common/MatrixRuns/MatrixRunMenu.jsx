// W6 C4 (W6-10): the Matrix's run-again actions (matrixRunActionsModel).
// `useMatrixRunActions` gives the Matrix three things through its existing
// menus: column items (`Run #2C again · creates #2C·2 on 30 rows`), cell
// menu items (`Run this cell again`, `Run row 33 on every run`) and the
// header line of a later run against the run it started from (`vs #2C: 3
// better · 1 worse`, through the Matrix read of both runs). Every action
// goes through rerun-cells, asks first where a confirm host is mounted, and
// toasts what the mock answered.
import React, { useCallback, useMemo } from "react";
import { ListItemIcon, ListItemText, MenuItem } from "@mui/material";
import { useQueries, useQuery, useQueryClient } from "@tanstack/react-query";
import Iconify from "src/components/iconify";
import axios, { endpoints } from "src/utils/axios";
import { MATRIX_PAGE_SIZE, matrixKey } from "src/api/develop/r2";
import { runResultsKey } from "src/api/develop/run-results";
import { rerunCells } from "src/api/develop/experiment-run-actions";
import { confirmRunAction } from "src/sections/develop-detail/Experiment/runActionConfirm";
import notify from "src/sections/common/ux/toast";
import { getRequestErrorMessage } from "src/utils/errorUtils";
import {
  cellRunItem,
  columnRunItem,
  parentOf,
  rowRunItem,
  runToast,
  vsParentLine,
} from "./matrixRunActionsModel";

const matrixParams = (runId, parentId) => ({
  runs: [runId, parentId].join(","),
  shape: "matrix",
  page: 0,
  page_size: MATRIX_PAGE_SIZE,
  baseline: parentId,
  measure: "pass_at_1",
});

/**
 * @param {{datasetId: string, runIds: string[], runsById: object,
 *   rows: number, noun?: string, readOnly?: boolean}} args
 */
export default function useMatrixRunActions({
  datasetId,
  runIds = [],
  runsById: runsByIdIn = {},
  rows = 0,
  noun = "row",
  readOnly = false,
}) {
  const queryClient = useQueryClient();
  // Each run's output column on its experiment (rerun-cells `cells`): the
  // R3 read's `summary.runs[].output_column_id` (the Matrix shape carries
  // cells only).
  const colsQ = useQuery({
    queryKey: [...runResultsKey(datasetId), "w6-output-columns", runIds],
    queryFn: () =>
      axios.get(endpoints.develop.datasetRuns.results(datasetId), {
        params: { runs: runIds.join(","), page: 0, page_size: 1 },
      }),
    select: (res) =>
      Object.fromEntries(
        (res.data?.result?.summary?.runs ?? res.data?.result?.runs ?? [])
          .filter((r) => r?.run_id && r.output_column_id)
          .map((r) => [r.run_id, r.output_column_id]),
      ),
    enabled: Boolean(datasetId) && runIds.length > 0,
    staleTime: 30_000,
  });
  const runsById = useMemo(() => {
    const cols = colsQ.data ?? {};
    const out = { ...runsByIdIn };
    Object.entries(cols).forEach(([id, col]) => {
      if (out[id]) out[id] = { ...out[id], output_column_id: col };
    });
    return out;
  }, [runsByIdIn, colsQ.data]);
  const refresh = useCallback(() => {
    queryClient.invalidateQueries({ queryKey: ["develop", "dataset-runs"] });
    queryClient.invalidateQueries({ queryKey: ["develop", "run-results"] });
    queryClient.invalidateQueries({ queryKey: ["experiment"] });
    queryClient.invalidateQueries({ queryKey: ["experiment-list"] });
  }, [queryClient]);

  const send = useCallback(
    async (requests, { confirm, fallback }) => {
      if (confirm) {
        const go = await confirmRunAction(confirm);
        if (!go) return null;
      }
      try {
        const res = await Promise.all(
          requests.map((r) => rerunCells(r.experimentId, r.body)),
        );
        notify(
          res.length === 1
            ? runToast(res[0], fallback)
            : `${
                res
                  .map((x) => runToast(x, ""))
                  .filter(Boolean)
                  .join(" · ") || fallback
              }`,
        );
        refresh();
        return res;
      } catch (e) {
        notify(getRequestErrorMessage(e, fallback), { variant: "error" });
        return null;
      }
    },
    [refresh],
  );

  const columnItems = useCallback(
    (runId) => {
      const run = runsById[runId];
      // The new run runs every row of its parent (rerun-cells source_ids).
      const n = run?.progress?.total ?? run?.examples ?? rows;
      const item = columnRunItem(run, { rows: n, noun });
      if (!item) return [];
      return [
        {
          label: readOnly
            ? `${item.label} (Editors can start runs)`
            : `${item.label} · creates ${item.next} on ${n} ${n === 1 ? noun : `${noun}s`}`,
          icon: "mdi:play-circle-outline",
          disabled: readOnly,
          onClick: () =>
            send([item], {
              confirm: {
                title: `${item.label}?`,
                content: `${item.secondary}.`,
                confirmLabel: "Run again",
              },
              fallback: `Started ${item.next}.`,
            }),
        },
      ];
    },
    [runsById, rows, noun, readOnly, send],
  );

  const cellItems = useCallback(
    (cellMenu, close) => {
      if (!cellMenu || readOnly) return [];
      const run = runsById[cellMenu.runId];
      const cell = cellRunItem(run, cellMenu.row);
      const row = rowRunItem(
        runIds.map((id) => runsById[id]).filter(Boolean),
        cellMenu.row,
      );
      const entry = (key, it, icon, onClick) => (
        <MenuItem
          key={key}
          data-testid={`matrix-run-${key}`}
          onClick={() => {
            close?.();
            onClick();
          }}
        >
          <ListItemIcon>
            <Iconify icon={icon} width={18} />
          </ListItemIcon>
          <ListItemText
            primary={it.label}
            secondary={it.secondary}
            primaryTypographyProps={{ typography: "s1" }}
            secondaryTypographyProps={{ typography: "s3" }}
          />
        </MenuItem>
      );
      return [
        cell
          ? entry("cell", cell, "mdi:play-outline", () =>
              send([cell], { fallback: "1 cell re-run" }),
            )
          : null,
        row
          ? entry("row", row, "mdi:table-row", () =>
              send(row.requests, { fallback: row.label }),
            )
          : null,
      ].filter(Boolean);
    },
    [runsById, runIds, readOnly, send],
  );

  // Later runs in view (#2C·2) and the runs they started from.
  const pairs = useMemo(
    () =>
      runIds
        .map((id) => ({
          run: runsById[id],
          parent: parentOf(runsById[id], runsById),
        }))
        .filter((p) => p.run && p.parent),
    [runIds, runsById],
  );
  const reads = useQueries({
    queries: pairs.map(({ run, parent }) => {
      const params = matrixParams(
        run.run_id ?? run.id,
        parent.run_id ?? parent.id,
      );
      return {
        queryKey: matrixKey(datasetId, params),
        queryFn: () =>
          axios.get(endpoints.develop.datasetRuns.results(datasetId), {
            params,
          }),
        select: (res) => res.data?.result,
        enabled: Boolean(datasetId),
        staleTime: 5_000,
      };
    }),
  });
  const lines = useMemo(() => {
    const out = {};
    pairs.forEach(({ run, parent }, i) => {
      const line = vsParentLine(reads[i]?.data, run, parent);
      if (line) out[run.run_id ?? run.id] = line;
    });
    return out;
  }, [pairs, reads]);

  /** The Matrix's header roles with the `vs #2C` line where it applies. */
  const withLines = useCallback(
    (roles = {}) => {
      const out = { ...roles };
      Object.entries(lines).forEach(([id, line]) => {
        out[id] = out[id] ? `${out[id]} · ${line}` : line;
      });
      return out;
    },
    [lines],
  );

  return { columnItems, cellItems, withLines, lines };
}
