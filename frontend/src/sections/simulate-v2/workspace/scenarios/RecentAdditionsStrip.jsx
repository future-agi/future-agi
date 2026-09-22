import PropTypes from "prop-types";
import { useState } from "react";
import { alpha } from "@mui/material/styles";
import { Box, Stack, Typography, Button, Tooltip, Collapse } from "@mui/material";
import Iconify from "src/components/iconify";
import { groupByBatch, relativeTime, sourceOf } from "../../_mock/scenarioProvenance";

/**
 * Recent additions strip — the "batch" surface without making Batch the
 * default grouping.
 *
 * PRD §3023 Q27: "how to differentiate and reuse batches (50 today +
 * 50 tomorrow) rather than append to one list … design a batch/set
 * identity so batches are reusable and comparable."
 *
 * This strip surfaces the last three batches inline above the table
 * regardless of the current group-by. Each row shows: source · when ·
 * added-by · count · [Show these] filter action. Collapses to a
 * one-line summary so it never dominates the page.
 */
export default function RecentAdditionsStrip({ scenarios = [], onFilterToBatch }) {
  const [open, setOpen] = useState(true);
  const batches = groupByBatch(scenarios).slice(0, 3);
  if (batches.length <= 1) return null;

  const totalNew = batches.reduce((a, b) => a + b.scenarios.length, 0);
  const newestAgo = relativeTime(batches[0].addedAt);

  return (
    <Box sx={{
      border: "1px solid", borderColor: "divider", borderRadius: 1.5,
      bgcolor: "background.paper",
      mb: 1.5,
      overflow: "hidden",
    }}>
      {/* header — always visible, click toggles */}
      <Stack
        direction="row" alignItems="center" spacing={1.5}
        onClick={() => setOpen((v) => !v)}
        sx={{
          px: 2, py: 1.25, cursor: "pointer",
          "&:hover": { bgcolor: "action.hover" },
        }}
      >
        <Iconify
          icon={open ? "solar:alt-arrow-down-linear" : "solar:alt-arrow-right-linear"}
          width={14} sx={{ color: "text.subtitle" }}
        />
        <Typography sx={{ typography: "s2", fontWeight: 700, fontSize: 13 }}>
          Recent additions
        </Typography>
        <Typography sx={{ typography: "s3", color: "text.subtitle", fontSize: 12 }}>
          {batches.length} batches · {totalNew} scenarios · newest {newestAgo}
        </Typography>
      </Stack>

      <Collapse in={open} timeout="auto" unmountOnExit>
        <Stack
          divider={<Box sx={{ height: "1px", bgcolor: "divider" }} />}
          sx={{ borderTop: "1px solid", borderColor: "divider" }}
        >
          {batches.map((b) => <BatchRow key={b.batchId} batch={b} onFilter={onFilterToBatch} />)}
        </Stack>
      </Collapse>
    </Box>
  );
}
RecentAdditionsStrip.propTypes = { scenarios: PropTypes.array, onFilterToBatch: PropTypes.func };

function BatchRow({ batch, onFilter }) {
  const src = sourceOf(batch.source);
  const who = batch.addedBy?.name || "System";
  const when = relativeTime(batch.addedAt);
  return (
    <Stack direction="row" alignItems="center" spacing={2} sx={{ px: 2, py: 1.25 }}>
      <Tooltip title={src.label} arrow>
        <Box sx={{
          width: 30, height: 30, borderRadius: 0.75,
          display: "flex", alignItems: "center", justifyContent: "center",
          bgcolor: (t) => alpha(t.palette.text.primary, t.palette.mode === "dark" ? 0.06 : 0.04),
          color: "text.primary", flexShrink: 0,
        }}>
          <Iconify icon={src.icon} width={14} />
        </Box>
      </Tooltip>
      <Box sx={{ flex: 1, minWidth: 0 }}>
        <Typography sx={{ typography: "s2", fontWeight: 600, fontSize: 13 }}>
          {batch.scenarios.length} scenarios · {src.short}
        </Typography>
        <Typography sx={{ typography: "s3", color: "text.subtitle", fontSize: 11.5, mt: 0.125 }}>
          {who} · {when}
        </Typography>
      </Box>
      {onFilter && (
        <Button
          size="small"
          onClick={() => onFilter(batch)}
          endIcon={<Iconify icon="solar:alt-arrow-right-linear" width={12} />}
          sx={{
            typography: "s2", fontWeight: 600, fontSize: 12,
            color: "text.primary", px: 1,
            "&:hover": { bgcolor: "action.hover" },
          }}
        >
          Show these
        </Button>
      )}
    </Stack>
  );
}
BatchRow.propTypes = { batch: PropTypes.object, onFilter: PropTypes.func };
