/* eslint-disable react/prop-types */
// Cells of the organisation columns (W9-SCOPE §5 A1, A6, A7): Metadata
// (`domain: billing` +n), one column per metadata key, Folder, Agents
// (`Support Agent · #4B 86.6%` with the latest run in the tooltip and a link
// to the agent's builder). Each shows only what the row carries.
import React from "react";
import { Box, Chip, Link, Stack, Typography } from "@mui/material";
import CustomTooltip from "src/components/tooltip";
import { metadataChips, rowsOf } from "./metadataModel";
import { agentBuilderHref, agentChipText, agentTooltip } from "./agentsModel";

const Empty = () => (
  <Typography typography="s2" color="text.disabled">
    —
  </Typography>
);

const chipSx = { typography: "s3", height: 20, maxWidth: 180 };

/** `domain: billing` and `+2`; every pair in the tooltip. */
export function MetadataCell({ row }) {
  const { chips, more } = metadataChips(row?.metadata, 1);
  if (!chips.length) return <Empty />;
  const all = rowsOf(row.metadata)
    .map((r) => `${r.key}: ${String(r.value)}`)
    .join("\n");
  return (
    <CustomTooltip
      show
      size="small"
      title={<Box sx={{ whiteSpace: "pre-line" }}>{all}</Box>}
    >
      <Stack direction="row" gap={0.5} alignItems="center" sx={{ minWidth: 0 }}>
        {chips.map((c) => (
          <Chip key={c} size="small" variant="outlined" label={c} sx={chipSx} />
        ))}
        {more > 0 && (
          <Typography typography="s3" color="text.secondary">
            {`+${more}`}
          </Typography>
        )}
      </Stack>
    </CustomTooltip>
  );
}

/** One metadata key's value. */
export function MetaKeyCell({ row, metaKey }) {
  const v = row?.metadata?.[metaKey];
  if (v === undefined || v === null) return <Empty />;
  return (
    <Typography typography="s2" noWrap>
      {String(v)}
    </Typography>
  );
}

/** The folder path, muted. */
export function FolderCell({ row }) {
  if (!row?.folder) return <Empty />;
  return (
    <Typography
      typography="s2"
      color="text.secondary"
      noWrap
      title={row.folder}
    >
      {row.folder}
    </Typography>
  );
}

/** Each attached agent with its latest run on the dataset. */
export function AgentsCell({ row }) {
  const agents = row?.agents ?? [];
  if (!agents.length) return <Empty />;
  return (
    <Stack direction="row" gap={0.5} sx={{ minWidth: 0, overflow: "hidden" }}>
      {agents.map((a) => (
        <CustomTooltip
          key={a.id}
          show
          size="small"
          title={
            <Box>
              <Box>{agentTooltip(a, row)}</Box>
              <Link
                href={agentBuilderHref(a.id)}
                onClick={(e) => e.stopPropagation()}
                sx={{ typography: "s3" }}
              >
                Open in Agents ↗
              </Link>
            </Box>
          }
        >
          <Chip
            size="small"
            variant="outlined"
            label={agentChipText(a)}
            sx={chipSx}
            data-testid="agent-chip"
          />
        </CustomTooltip>
      ))}
    </Stack>
  );
}
