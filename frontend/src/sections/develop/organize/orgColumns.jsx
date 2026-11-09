/* eslint-disable react/prop-types */
// Column defs of the organisation columns (W9-SCOPE §5 A1, A6, A7), in the
// shape datasetListColumns() returns; the page appends them. Sorting stays
// off: the list query sorts only by get-datasets' own fields.
import React from "react";
import { AgentsCell, FolderCell, MetadataCell, MetaKeyCell } from "./OrgCells";
import { META_COLUMN_PREFIX } from "../datasetList";

export function datasetOrgColumns({ metadataKeys = [] } = {}) {
  return [
    {
      id: "folder",
      header: "Folder",
      size: 140,
      enableSorting: false,
      cell: ({ row }) => <FolderCell row={row.original} />,
    },
    {
      id: "agents",
      header: "Agents",
      description: "Each attached agent's latest finished run on this dataset",
      size: 220,
      enableSorting: false,
      cell: ({ row }) => <AgentsCell row={row.original} />,
    },
    {
      id: "metadata",
      header: "Metadata",
      size: 200,
      enableSorting: false,
      cell: ({ row }) => <MetadataCell row={row.original} />,
    },
    ...[...new Set(metadataKeys)].sort().map((key) => ({
      id: `${META_COLUMN_PREFIX}${key}`,
      header: key,
      size: 120,
      enableSorting: false,
      cell: ({ row }) => <MetaKeyCell row={row.original} metaKey={key} />,
    })),
  ];
}
