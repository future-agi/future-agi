import PropTypes from "prop-types";
import { alpha } from "@mui/material/styles";
import { useState } from "react";
import {
  Box,
  Stack,
  Typography,
  Button,
  IconButton,
  Tooltip,
  Menu,
  MenuItem,
} from "@mui/material";
import Iconify from "src/components/iconify";
import TrialsPicker from "./TrialsPicker";

/**
 * Bulk-action bar for the scenario table.
 *
 * Takes the toolbar's place in the SAME row when one or more rows are
 * checked — same height, same edges (no extra inset), same 30px buttons — so
 * the table never jumps when selection toggles; only the contents swap.
 *
 * Hierarchy (left → right):
 *   ✓  N scenarios selected ×  ....  [Must-pass] [edit] [delete] | Repeats [Run (N)]
 *   context                          marks on the set     edit · delete    the run
 *
 * Run + Repeats stay anchored to the workspace header (top-right); its Run
 * button reads "Run selected" while rows are checked (the bar already says how many).
 */
const ICON_BTN_SX = { minWidth: 0, width: 30, px: 0 };
const BTN_SX = {
  flexShrink: 0,
  whiteSpace: "nowrap",
  color: "text.primary",
  borderColor: "divider",
};

export default function SelectionBar({
  count,
  onEdit,
  onDelete,
  onClear,
  onMustPass,
  allMustPass = false,
  run = null,
  /* Tag, add to group, duplicate, validate, archive, promote, export — each
     confirmed with its exact count (onBulk(kind)). */
  onBulk = null,
  /* Select all matching is its own action, never a silent widening. */
  matching = null,
}) {
  const [moreAnchor, setMoreAnchor] = useState(null);
  return (
    <Stack
      direction="row"
      alignItems="center"
      spacing={1}
      sx={{ minHeight: 30 }}
    >
      <Stack
        direction="row"
        alignItems="center"
        spacing={1}
        sx={{ flexShrink: 0 }}
      >
        <Box
          sx={{
            width: 18,
            height: 18,
            borderRadius: "4px",
            display: "flex",
            alignItems: "center",
            justifyContent: "center",
            bgcolor: "primary.main",
            color: (t) =>
              t.palette.mode === "dark"
                ? "background.default"
                : "background.paper",
          }}
        >
          <Iconify icon="solar:check-linear" width={14} />
        </Box>
        <Typography
          sx={{
            typography: "s2",
            fontWeight: 600,
            color: "text.primary",
            whiteSpace: "nowrap",
            fontVariantNumeric: "tabular-nums",
          }}
        >
          {`${count} scenario${count === 1 ? "" : "s"} selected`}
        </Typography>
        <Tooltip arrow title="Clear selection">
          <IconButton
            size="small"
            aria-label="Clear selection"
            onClick={onClear}
          >
            <Iconify
              icon="solar:close-linear"
              width={16}
              sx={{ color: "text.tertiary" }}
            />
          </IconButton>
        </Tooltip>
        {matching && matching.count > count && (
          <Button
            size="small"
            onClick={matching.onSelectAll}
            sx={{ color: "text.secondary", whiteSpace: "nowrap" }}
          >
            {`Select all ${matching.count} matching`}
          </Button>
        )}
      </Stack>

      <Box sx={{ flex: 1 }} />

      {onBulk && (
        <>
          <Button
            size="small"
            variant="outlined"
            onClick={(e) => setMoreAnchor(e.currentTarget)}
            endIcon={<Iconify icon="solar:alt-arrow-down-linear" width={14} />}
            sx={BTN_SX}
          >
            Organise
          </Button>
          <Menu
            anchorEl={moreAnchor}
            open={!!moreAnchor}
            onClose={() => setMoreAnchor(null)}
          >
            {[
              ["tag", "Add tag…", "solar:tag-linear"],
              ["group", "Add to group…", "solar:folder-2-linear"],
              ["duplicate", "Duplicate to edit", "solar:copy-linear"],
              ["validate", "Validate", "solar:shield-check-linear"],
              [
                "promote",
                "Promote to regression",
                "solar:lock-keyhole-minimalistic-linear",
              ],
              ["export", "Export", "solar:export-linear"],
              ["archive", "Archive", "solar:archive-linear"],
            ].map(([k, label, icon]) => (
              <MenuItem
                key={k}
                onClick={() => {
                  setMoreAnchor(null);
                  onBulk(k);
                }}
                sx={{ typography: "s2", gap: 1 }}
              >
                <Iconify
                  icon={icon}
                  width={16}
                  sx={{ color: "text.secondary" }}
                />
                {label}
              </MenuItem>
            ))}
          </Menu>
        </>
      )}

      {/* Must-pass: every selected scenario becomes a release blocker —
          or, when they all already are, stops being one. */}
      {onMustPass && (
        <Button
          size="small"
          variant="outlined"
          onClick={onMustPass}
          startIcon={
            <Iconify
              icon={
                allMustPass
                  ? "solar:shield-check-bold"
                  : "solar:shield-check-linear"
              }
              width={14}
            />
          }
          sx={BTN_SX}
        >
          {allMustPass ? "Remove must-pass" : "Must-pass"}
        </Button>
      )}
      {/* Edit and Delete as icons — the bar keeps room for the run. */}
      {onEdit && (
        <Tooltip
          arrow
          title={`Edit ${count === 1 ? "it" : `these ${count}`} in chat`}
        >
          <Button
            size="small"
            variant="outlined"
            onClick={onEdit}
            aria-label="Edit in chat"
            sx={{ ...BTN_SX, ...ICON_BTN_SX }}
          >
            <Iconify icon="solar:pen-new-square-linear" width={16} />
          </Button>
        </Tooltip>
      )}
      <Tooltip
        arrow
        title={`Delete ${count === 1 ? "this scenario" : `${count} scenarios`}`}
      >
        <Button
          size="small"
          variant="outlined"
          onClick={onDelete}
          aria-label="Delete selected scenarios"
          sx={{
            ...BTN_SX,
            ...ICON_BTN_SX,
            color: "error.main",
            borderColor: (t) => alpha(t.palette.error.main, 0.4),
            "&:hover": {
              borderColor: "error.main",
              bgcolor: (t) =>
                alpha(
                  t.palette.error.main,
                  t.palette.mode === "dark" ? 0.1 : 0.05,
                ),
            },
          }}
        >
          <Iconify icon="solar:trash-bin-trash-linear" width={16} />
        </Button>
      </Tooltip>

      {/* Running the selection lives here while rows are checked — the
          header's own run controls step aside, so there's one Run. */}
      {run && (
        <>
          <Box
            sx={{
              width: "1px",
              alignSelf: "stretch",
              my: 0.5,
              mx: 0.5,
              bgcolor: "divider",
              flexShrink: 0,
            }}
          />
          <TrialsPicker
            trials={run.repeats}
            onChange={run.onRepeats}
            scenarioCount={count}
            unit={run.unit || "conversation"}
          />
          <Tooltip
            arrow
            title={run.disabledReason || ""}
            disableHoverListener={!run.disabledReason}
          >
            <span>
              <Button
                size="small"
                variant="contained"
                color="primary"
                disabled={!!run.disabledReason}
                onClick={run.onRun}
                startIcon={<Iconify icon="solar:play-bold" width={14} />}
                sx={{ whiteSpace: "nowrap" }}
              >
                Run selected
              </Button>
            </span>
          </Tooltip>
        </>
      )}
    </Stack>
  );
}
SelectionBar.propTypes = {
  count: PropTypes.number.isRequired,
  onEdit: PropTypes.func,
  onDelete: PropTypes.func.isRequired,
  onClear: PropTypes.func.isRequired,
  onMustPass: PropTypes.func,
  allMustPass: PropTypes.bool,
  onBulk: PropTypes.func,
  matching: PropTypes.object,
  run: PropTypes.shape({
    repeats: PropTypes.oneOfType([PropTypes.number, PropTypes.string]),
    onRepeats: PropTypes.func,
    onRun: PropTypes.func,
    disabledReason: PropTypes.node,
    unit: PropTypes.string,
  }),
};
