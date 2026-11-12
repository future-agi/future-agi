/* eslint-disable react/prop-types */
import React from "react";
import { Box, Typography } from "@mui/material";
import { fmtCount, NUM_SX } from "src/sections/common/format/fmt";
import {
  BaselineCell,
  BestRunCell,
  ExperimentsCell,
  LastExperimentCell,
  NameCell,
  OwnerCell,
  RowMenu,
  SourceCell,
  TimeCell,
  VersionCell,
} from "./DatasetListCells";
import { BASELINE_HELP } from "./datasetList";
import DatasetStarToggle from "./organize/DatasetStarToggle";
import { DatasetDragHandle } from "./organize/FolderDnd";
import { realListColumnIds, datasetCaps } from "./realBackendCaps";

// Columns of the dataset list (PRODUCT-SPEC §3.1, SVP-4.1, DL#9–12). Every
// column is defined here; which show, in what order and frozen is the
// user's layout (`DATASET_LIST_COLUMNS` + useColumnLayout, A1-01/02).

const NumCell = ({ value }) => (
  <Typography typography="s2" sx={{ ...NUM_SX, width: "100%" }}>
    {fmtCount(value ?? 0)}
  </Typography>
);

// Shown on row hover or keyboard focus (always, for a starred row's star).
const onRowHover = (shown) => ({
  opacity: shown ? 1 : 0,
  transition: "opacity 120ms",
  ".MuiDataGrid-row:hover &, &:focus-within": { opacity: 1 },
});
// The grid focuses (and re-renders) the cell on mousedown, which would
// swallow the click (as RowMenu).
const stopMouseDown = (e) => e.stopPropagation();

// The drag handle's room at the start of the Name cell, in theme spacing
// units: its button (a 16 px icon with 2 px padding), pulled into the cell
// padding, then the gap before the name. The Name header starts after the
// same room, so the header and the names share one left edge while the
// handle is hidden (it shows on row hover or focus).
const HANDLE = 2.5;
const HANDLE_PULL = 0.75;
const NAME_GAP = 0.25;
const HANDLE_ROOM = HANDLE - HANDLE_PULL + NAME_GAP;

/**
 * W9 A4, A6 (M4 mount): the Name cell with a drag handle (move to a folder;
 * editors only) and the per-user star (anyone), each in its own slot so
 * neither covers the name or its tags.
 */
const NameWithOrg = ({ row, query, draggable, star = true }) => (
  <Box
    sx={{
      display: "flex",
      alignItems: "flex-start",
      gap: NAME_GAP,
      width: "100%",
      minWidth: 0,
    }}
  >
    {draggable && (
      <Box
        onMouseDown={stopMouseDown}
        sx={{
          mt: 0.5,
          ml: -HANDLE_PULL,
          width: (t) => t.spacing(HANDLE),
          display: "flex",
          justifyContent: "center",
          flexShrink: 0,
          ...onRowHover(false),
        }}
      >
        <DatasetDragHandle row={row} />
      </Box>
    )}
    <Box sx={{ flex: 1, minWidth: 0 }}>
      <NameCell row={row} query={query} />
    </Box>
    {star && (
      <Box
        onMouseDown={stopMouseDown}
        sx={{ mt: 0.5, flexShrink: 0, ...onRowHover(Boolean(row.starred)) }}
      >
        <DatasetStarToggle dataset={row} />
      </Box>
    )}
  </Box>
);

/**
 * Every column def; the layout picks, orders and freezes them. `extra`
 * (the organisation columns) go before the ⋯ column, which stays last;
 * `draggable` (an editor, with the folder rail shown) adds the drag handle.
 * L1: on the real backend (`real`) the Name cell has no star (no star API
 * on dev), Rows counts the dataset's rows (no versions) and Experiments is
 * a plain count (the Experiments tab is not part of the Datasets launch).
 */
export function datasetListColumns({
  query,
  rowActions,
  canEdit,
  canDelete,
  draggable = false,
  extra = [],
  real = datasetCaps().real,
}) {
  const handle = draggable && datasetCaps(real).listFolders;
  const all = [
    {
      id: "name",
      accessorKey: "name",
      header: "Name",
      // With the drag handle, the label starts after the handle's room.
      ...(handle && {
        renderHeader: () => (
          <Box component="span" sx={{ pl: HANDLE_ROOM }}>
            Name
          </Box>
        ),
      }),
      meta: { flex: 2 },
      // Leaves room for a vertical scrollbar at 1280 (no horizontal scroll).
      minSize: 248,
      cell: ({ row }) => (
        <NameWithOrg
          row={row.original}
          query={query}
          draggable={handle}
          star={datasetCaps(real).listStars}
        />
      ),
    },
    {
      id: "numberOfDatapoints",
      accessorKey: "numberOfDatapoints",
      header: "Rows",
      description: real ? "Rows in this dataset" : "Rows in the latest version",
      // Reserve the label, sort/menu icons and table padding in Firefox too.
      // Keep the existing semantic table typography and right alignment.
      size: 112,
      headerAlign: "right",
      align: "right",
      cell: ({ getValue }) => <NumCell value={getValue()} />,
    },
    {
      id: "version",
      header: "Version",
      size: 124,
      enableSorting: false,
      cell: ({ row }) => <VersionCell row={row.original} />,
    },
    {
      id: "lastExperiment",
      header: "Last experiment",
      size: 168,
      cell: ({ row }) => <LastExperimentCell row={row.original} />,
    },
    {
      id: "baseline",
      header: "Baseline",
      // IT-R4 G2 P9: the help is the header's tooltip (like Best), not a
      // second control in the header row (screen 01 budget).
      description: BASELINE_HELP,
      size: 120,
      cell: ({ row }) => <BaselineCell row={row.original} />,
    },
    {
      id: "bestRun",
      header: "Best",
      description:
        "Best run and its change vs the baseline (grey = within noise)",
      // IT-R4 finalize: room for `#1C · 91.7%  +25.0 pts n=24` (the score
      // was cut to `9…` at 172).
      size: 196,
      cell: ({ row }) => <BestRunCell row={row.original} />,
    },
    {
      id: "numberOfExperiments",
      accessorKey: "numberOfExperiments",
      header: "Experiments",
      size: 108,
      headerAlign: "right",
      align: "right",
      cell: ({ row, getValue }) =>
        real ? (
          <NumCell value={getValue()} />
        ) : (
          <Box sx={{ width: "100%", ...NUM_SX }}>
            <ExperimentsCell row={row.original} />
          </Box>
        ),
    },
    {
      id: "updatedAt",
      accessorKey: "updated_at",
      header: "Updated",
      size: 124,
      cell: ({ row, getValue }) => (
        <TimeCell
          value={getValue()}
          by={row.original.latest_version?.created_by_name}
        />
      ),
    },
    {
      id: "createdAt",
      accessorKey: "createdAt",
      header: "Created",
      // L1: room for `Created` and its sort arrow (the default sort there).
      size: real ? 128 : 104,
      cell: ({ row, getValue }) => (
        <TimeCell value={getValue()} by={row.original.created_by_name} />
      ),
    },
    {
      id: "owner",
      header: "Owner",
      size: 130,
      enableSorting: false,
      cell: ({ row }) => <OwnerCell row={row.original} />,
    },
    {
      id: "numberOfOptimisations",
      accessorKey: "numberOfOptimisations",
      header: "Optimizations",
      size: 112,
      enableSorting: false,
      headerAlign: "right",
      align: "right",
      cell: ({ getValue }) => <NumCell value={getValue()} />,
    },
    {
      id: "source",
      accessorKey: "source",
      header: "Source",
      size: 104,
      enableSorting: false,
      cell: ({ getValue }) => <SourceCell value={getValue()} />,
    },
    ...extra,
    {
      id: "actions",
      header: "",
      size: 48,
      enableSorting: false,
      cell: ({ row }) => (
        <RowMenu
          row={row.original}
          actions={rowActions}
          canEdit={canEdit}
          canDelete={canDelete}
        />
      ),
    },
  ];
  // L1: the table shows every def it is given, whatever the layout's
  // catalog holds, so the real backend gets only the columns get-datasets
  // answers (and the ⋯ column).
  // shared-edit(F44): expose only the independently qualified metadata columns.
  const allowed = realListColumnIds();
  return real
    ? all.filter((c) => c.id === "actions" || allowed.includes(c.id))
    : all;
}
