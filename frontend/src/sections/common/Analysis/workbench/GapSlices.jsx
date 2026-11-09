import React, { useState } from "react";
import PropTypes from "prop-types";
import {
  Box,
  Button,
  IconButton,
  ListItemIcon,
  ListItemText,
  Menu,
  MenuItem,
  Stack,
  Typography,
} from "@mui/material";
import Iconify from "src/components/iconify";
import CustomTooltip from "src/components/tooltip";

const KEY_LABEL = {
  category: "Category",
  difficulty: "Difficulty",
  split: "Split",
  source: "Source",
};
export const sliceKeyLabel = (key) =>
  KEY_LABEL[key] ??
  `${key.charAt(0).toUpperCase()}${key.slice(1).replace(/_/g, " ")}`;
const MINUS = "−";

/** `Category refund · +55.6 pts · 6 rows (7/18 → 17/18)`. */
export function sliceLine(s) {
  const d = s.delta_pts;
  const sign = d > 0 ? "+" : d < 0 ? MINUS : "";
  return `${sliceKeyLabel(s.key)} ${s.value} · ${sign}${Math.abs(d).toFixed(1)} pts · ${s.rows} rows (${s.from.pass}/${s.from.n} → ${s.to.pass}/${s.to.n})`;
}

/** `Where #2D beats #2B most` / `Where #4A loses to #3A most`. */
export function gapTitle(slices, candLabel, baseLabel) {
  const loses = (slices?.[0]?.delta_pts ?? 0) < 0;
  return `Where ${candLabel} ${loses ? "loses to" : "beats"} ${baseLabel} most`;
}

function LineMenu({ actions }) {
  const [anchor, setAnchor] = useState(null);
  return (
    <>
      <IconButton
        size="small"
        aria-label="Slice actions"
        onClick={(e) => setAnchor(e.currentTarget)}
        sx={{ p: 0.25 }}
      >
        <Iconify icon="mdi:dots-horizontal" width={16} />
      </IconButton>
      <Menu
        anchorEl={anchor}
        open={Boolean(anchor)}
        onClose={() => setAnchor(null)}
      >
        {actions.map((a) => (
          <CustomTooltip
            key={a.label}
            show={Boolean(a.disabled)}
            title={a.disabled ?? ""}
            size="small"
            placement="left"
            arrow
          >
            <span>
              <MenuItem
                disabled={Boolean(a.disabled)}
                onClick={() => {
                  setAnchor(null);
                  a.onClick();
                }}
              >
                <ListItemIcon>
                  <Iconify icon={a.icon} width={18} />
                </ListItemIcon>
                <ListItemText primaryTypographyProps={{ typography: "s1" }}>
                  {a.label}
                </ListItemText>
              </MenuItem>
            </span>
          </CustomTooltip>
        ))}
      </Menu>
    </>
  );
}

LineMenu.propTypes = { actions: PropTypes.array.isRequired };

/**
 * `Where the gap is` (R2-SPEC §6.3.6): the three slices where the focused
 * candidate differs most from the baseline (runStats.gapSlices: ≥ 3 rows,
 * ≤ half the rows, ranked by |pass@1 gap|). A line sets `rows=`; `⋯` shows
 * the rows or iterates on the slice. Deterministic, no LLM.
 */
const GapSlices = ({
  slices,
  candLabel,
  baseLabel,
  candidates,
  onCand,
  onPick,
  onShowRows,
  onIterate,
  iterateDisabled,
}) => {
  const [anchor, setAnchor] = useState(null);
  return (
    <Box
      sx={{
        border: 1,
        borderColor: "divider",
        borderRadius: 1,
        bgcolor: "background.paper",
        px: 1.5,
        py: 1,
      }}
    >
      <Stack direction="row" alignItems="center" gap={0.5} sx={{ mb: 0.5 }}>
        <Iconify icon="mdi:target" width={18} sx={{ color: "primary.main" }} />
        <Typography typography="s1" fontWeight="fontWeightSemiBold">
          {gapTitle(slices, candLabel, baseLabel)}
        </Typography>
        {candidates.length > 1 && (
          <>
            <Button
              size="small"
              endIcon={<Iconify icon="mdi:chevron-down" width={16} />}
              onClick={(e) => setAnchor(e.currentTarget)}
              sx={{ ml: 0.5, typography: "s2", minWidth: 0 }}
            >
              Change run
            </Button>
            <Menu
              anchorEl={anchor}
              open={Boolean(anchor)}
              onClose={() => setAnchor(null)}
            >
              {candidates.map((c) => (
                <MenuItem
                  key={c.run_id}
                  selected={c.label === candLabel}
                  onClick={() => {
                    setAnchor(null);
                    onCand(c.run_id);
                  }}
                  sx={{ typography: "s1" }}
                >
                  {c.label} {c.name}
                </MenuItem>
              ))}
            </Menu>
          </>
        )}
      </Stack>
      {slices.length === 0 ? (
        <Typography typography="s2" color="text.secondary">
          No slice of 3 or more rows separates {candLabel} from {baseLabel}.
        </Typography>
      ) : (
        slices.map((s) => (
          <Stack
            key={`${s.key}:${s.value}`}
            direction="row"
            alignItems="center"
            gap={0.5}
          >
            <Box
              component="button"
              type="button"
              onClick={() => onPick(s)}
              sx={{
                all: "unset",
                cursor: "pointer",
                py: 0.25,
                typography: "s1",
                color: "text.primary",
                fontVariantNumeric: "tabular-nums",
                "&:hover": {
                  color: "primary.main",
                  textDecoration: "underline",
                },
                "&:focus-visible": {
                  outline: (t) => `2px solid ${t.palette.primary.main}`,
                },
              }}
            >
              {sliceLine(s)}
            </Box>
            <LineMenu
              actions={[
                {
                  label: "Show rows",
                  icon: "mdi:table-eye",
                  onClick: () => onShowRows(s),
                },
                // dataset integration FE-0: absent when Test a fix does not apply.
                ...(onIterate
                  ? [
                      {
                        label: "Test a fix on this slice…",
                        icon: "mdi:sync",
                        onClick: () => onIterate(s),
                        disabled: iterateDisabled,
                      },
                    ]
                  : []),
              ]}
            />
          </Stack>
        ))
      )}
    </Box>
  );
};

GapSlices.propTypes = {
  slices: PropTypes.array.isRequired,
  candLabel: PropTypes.string,
  baseLabel: PropTypes.string,
  candidates: PropTypes.array,
  onCand: PropTypes.func,
  onPick: PropTypes.func.isRequired,
  onShowRows: PropTypes.func.isRequired,
  onIterate: PropTypes.func,
  iterateDisabled: PropTypes.string,
};

export default GapSlices;
