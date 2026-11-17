import React from "react";
import PropTypes from "prop-types";
import { Box, Button, Divider, Paper, Typography } from "@mui/material";
import Iconify from "src/components/iconify";
import CustomTooltip from "src/components/tooltip";
import { KeyCap } from "src/sections/common/ux/KeyCap";

/**
 * Floating selection bar of the dataset list (DL#16, DL#17): bottom-centre
 * over the list, never replacing the toolbar (no layout shift). Esc clears.
 * `Compare datasets` shows only when the host passes `onCompare` (prototype
 * builds do not: the compare flow is not mocked, so it is absent there).
 */
export default function ListSelectionBar({
  count,
  onDuplicate,
  onCompare,
  onDelete,
  onClear,
  canCreate,
  canDelete,
  organize = null,
}) {
  if (!count) return null;
  const btn = { typography: "s2", textTransform: "none", whiteSpace: "nowrap" };
  return (
    <Paper
      elevation={8}
      role="region"
      aria-label="Selection actions"
      sx={{
        position: "absolute",
        left: "50%",
        transform: "translateX(-50%)",
        bottom: 56,
        zIndex: 5,
        display: "flex",
        alignItems: "center",
        gap: 1,
        px: 1.5,
        py: 0.75,
        borderRadius: 1.5,
        border: "1px solid",
        borderColor: "divider",
        maxWidth: "calc(100% - 32px)",
      }}
    >
      <Typography
        typography="s2"
        fontWeight={600}
        color="primary.main"
        aria-live="polite"
        sx={{ whiteSpace: "nowrap", fontVariantNumeric: "tabular-nums" }}
      >
        {count} selected
      </Typography>
      <Divider orientation="vertical" flexItem sx={{ mx: 0.5 }} />
      {count === 1 ? (
        <CustomTooltip
          show={!canCreate}
          title="Viewers can't duplicate datasets"
          size="small"
        >
          <span>
            <Button
              size="small"
              color="inherit"
              startIcon={<Iconify icon="gg:duplicate" width={16} />}
              onClick={onDuplicate}
              disabled={!canCreate}
              sx={btn}
            >
              Duplicate
            </Button>
          </span>
        </CustomTooltip>
      ) : onCompare ? (
        <CustomTooltip
          show
          title={
            count > 3
              ? `Compare up to 3 datasets · you selected ${count}`
              : "Compare these datasets column by column"
          }
          size="small"
        >
          <span>
            <Button
              size="small"
              color="inherit"
              startIcon={<Iconify icon="mdi:compare-horizontal" width={16} />}
              onClick={onCompare}
              disabled={count > 3}
              sx={btn}
            >
              Compare datasets
            </Button>
          </span>
        </CustomTooltip>
      ) : null}
      {/* W9 A1, A6, A7: `Organize ▾` when the host passes it. */}
      {organize}
      <CustomTooltip
        show={!canDelete}
        title="Only owners and admins can delete datasets"
        size="small"
      >
        <span>
          <Button
            size="small"
            color="error"
            startIcon={
              <Iconify icon="solar:trash-bin-trash-linear" width={16} />
            }
            onClick={onDelete}
            disabled={!canDelete}
            sx={btn}
          >
            Delete {count}
          </Button>
        </span>
      </CustomTooltip>
      <Divider orientation="vertical" flexItem sx={{ mx: 0.5 }} />
      <Button
        size="small"
        color="inherit"
        onClick={onClear}
        sx={{ ...btn, color: "text.secondary", gap: 0.75 }}
        aria-label="Clear selection"
      >
        <Box component="span">Clear</Box>
        <KeyCap>Esc</KeyCap>
      </Button>
    </Paper>
  );
}

ListSelectionBar.propTypes = {
  count: PropTypes.number,
  onDuplicate: PropTypes.func,
  onCompare: PropTypes.func,
  onDelete: PropTypes.func,
  onClear: PropTypes.func,
  canCreate: PropTypes.bool,
  canDelete: PropTypes.bool,
  organize: PropTypes.node,
};
