import React, { useState } from "react";
import PropTypes from "prop-types";
import {
  Box,
  Button,
  Checkbox,
  Collapse,
  FormControlLabel,
  IconButton,
  MenuItem,
  Popover,
  Select,
  Stack,
  Switch,
  Typography,
} from "@mui/material";
import Iconify from "src/components/iconify";
import CustomTooltip from "src/components/tooltip";
import { fmtCost, fmtMs } from "src/sections/common/format/fmt";
import { RailDot } from "./TaskRail";
import { MAX_LANES, dotOf } from "./workspaceModel";

export const LANE_SORTS = [
  { value: "matrix", label: "Matrix order" },
  { value: "score", label: "Score" },
  { value: "cost", label: "Cost" },
  { value: "time", label: "Time" },
];

const mean = (xs) => {
  const v = (xs ?? []).filter(Number.isFinite);
  return v.length ? v.reduce((a, b) => a + b, 0) / v.length : null;
};
const passOf = (cell) => (cell?.trials ?? []).filter((t) => t === 1).length;

/** Lane order for a sort (baseline stays first). */
export function sortLaneIds(ids, sort, cells) {
  const [base, ...rest] = ids;
  const key = {
    score: (id) => -passOf(cells?.[id]),
    cost: (id) => mean(cells?.[id]?.cost_usd) ?? Infinity,
    time: (id) => mean(cells?.[id]?.wall_ms) ?? Infinity,
  }[sort];
  if (!key) return ids;
  return [base, ...[...rest].sort((a, b) => key(a) - key(b))];
}

const TRIAL_WORD = {
  1: "passed",
  0: "failed",
  error: "errored · counted as a fail",
  infra: "didn't run",
};

/**
 * `Edit lanes ▾`: one row per run in the selection (colour, label + name,
 * its result on this task, expand to its trials), `Sort lanes`, the 4-lane
 * cap with its reason, and `Production · chat-1052` when the task came from
 * production. Changes apply at once (URL `runs`).
 */
export default function LanePicker({
  runs,
  laneIds,
  cells,
  onChange,
  sort,
  onSort,
  sourceRef,
  productionId,
  onCards,
  blind,
  blindOn,
  onBlind,
}) {
  const [anchor, setAnchor] = useState(null);
  const [open, setOpen] = useState({});
  // IT-R2 G4 P9: any 2–4 runs; the baseline is not pinned (unpicking it
  // makes the next lane the baseline). With 4 shown, picking another takes
  // the last lane's place (the tooltip says which).
  const toggle = (id) => {
    if (laneIds.includes(id)) {
      if (laneIds.length <= 1) return;
      onChange(laneIds.filter((x) => x !== id));
    } else if (laneIds.length < MAX_LANES) onChange([...laneIds, id]);
    else onChange([...laneIds.slice(0, -1), id]);
  };
  const full = laneIds.length >= MAX_LANES;
  const lastLabel = runs.find(
    (r) => r.run_id === laneIds[laneIds.length - 1],
  )?.label;
  return (
    <>
      <CustomTooltip
        describeChild
        show
        size="small"
        title="Choose which runs to compare (up to 4 lanes)"
        disableFocusListener
      >
        <Button
          size="small"
          color="inherit"
          onClick={(e) => setAnchor(e.currentTarget)}
          endIcon={<Iconify icon="mdi:chevron-down" width={16} />}
          sx={{ flexShrink: 0, color: "text.secondary", whiteSpace: "nowrap" }}
          aria-haspopup="dialog"
          data-lane-picker-btn
        >
          Lanes
        </Button>
      </CustomTooltip>
      <Popover
        open={Boolean(anchor)}
        anchorEl={anchor}
        onClose={() => setAnchor(null)}
        anchorOrigin={{ vertical: "bottom", horizontal: "left" }}
      >
        <Box sx={{ width: 380, p: 1.25 }} data-lane-picker>
          <Stack
            direction="row"
            alignItems="center"
            spacing={1}
            sx={{ mb: 0.75 }}
          >
            <Typography typography="s1" fontWeight={600}>
              Lanes
            </Typography>
            <Typography typography="s3" color="text.secondary">
              {laneIds.length} shown · up to {MAX_LANES}
            </Typography>
            <Box sx={{ flex: 1 }} />
            <Select
              size="small"
              value={sort}
              onChange={(e) => onSort(e.target.value)}
              sx={{ typography: "s2", "& .MuiSelect-select": { py: 0.25 } }}
              aria-label="Sort lanes"
            >
              {LANE_SORTS.map((s) => (
                <MenuItem
                  key={s.value}
                  value={s.value}
                  sx={{ typography: "s2" }}
                >
                  Sort lanes: {s.label}
                </MenuItem>
              ))}
            </Select>
          </Stack>
          <Stack sx={{ maxHeight: 360, overflowY: "auto" }}>
            {runs.map((r) => {
              const on = laneIds.includes(r.run_id);
              const cell = cells?.[r.run_id];
              const d = dotOf(cell, r.label);
              const disabled = on && laneIds.length <= 1;
              const replaces = !on && full;
              return (
                <Box key={r.run_id}>
                  <CustomTooltip
                    describeChild
                    show
                    size="small"
                    placement="left"
                    title={
                      disabled
                        ? "Keep at least one lane"
                        : replaces
                          ? `Up to ${MAX_LANES} side by side: shows it in place of ${lastLabel ?? "the last lane"}`
                          : ""
                    }
                  >
                    <Stack
                      direction="row"
                      alignItems="center"
                      spacing={0.75}
                      sx={{
                        py: 0.25,
                        px: 0.5,
                        borderRadius: 1,
                        "&:hover": { bgcolor: "action.hover" },
                      }}
                    >
                      <Checkbox
                        size="small"
                        checked={on}
                        disabled={disabled}
                        onChange={() => toggle(r.run_id)}
                        inputProps={{ "aria-label": `Lane ${r.label}` }}
                        sx={{ p: 0.5 }}
                      />
                      <Box
                        sx={{
                          width: 10,
                          height: 10,
                          borderRadius: "50%",
                          bgcolor: blind ? "text.disabled" : r.color?.solid,
                          flexShrink: 0,
                        }}
                      />
                      <Typography
                        typography="s2"
                        fontWeight={700}
                        sx={{ flexShrink: 0 }}
                      >
                        {r.label}
                      </Typography>
                      <Typography
                        typography="s3"
                        color="text.secondary"
                        noWrap
                        sx={{ minWidth: 0, flex: 1 }}
                      >
                        {r.name}
                      </Typography>
                      <RailDot cell={cell} run={r} />
                      <Typography
                        typography="s3"
                        color="text.secondary"
                        sx={{
                          flexShrink: 0,
                          fontVariantNumeric: "tabular-nums",
                        }}
                      >
                        {d.label.split(" · ")[1] ?? "didn't run"}
                      </Typography>
                      <IconButton
                        size="small"
                        onClick={() =>
                          setOpen((o) => ({ ...o, [r.run_id]: !o[r.run_id] }))
                        }
                        aria-label={`Trials of ${r.label}`}
                        sx={{ p: 0.25 }}
                        disabled={!cell}
                      >
                        <Iconify
                          icon={
                            open[r.run_id]
                              ? "mdi:chevron-up"
                              : "mdi:chevron-down"
                          }
                          width={16}
                        />
                      </IconButton>
                    </Stack>
                  </CustomTooltip>
                  <Collapse in={Boolean(open[r.run_id])} unmountOnExit>
                    <Stack sx={{ pl: 5, pb: 0.5 }}>
                      {(cell?.trials ?? []).map((t, i) => (
                        <Typography
                          key={i}
                          typography="s3"
                          color="text.secondary"
                          sx={{ fontVariantNumeric: "tabular-nums" }}
                        >
                          Trial {i + 1} · {TRIAL_WORD[t] ?? "no result"}
                          {Number.isFinite(cell?.cost_usd?.[i]) && t !== "infra"
                            ? ` · ${fmtCost(cell.cost_usd[i])}`
                            : ""}
                          {Number.isFinite(cell?.wall_ms?.[i]) && t !== "infra"
                            ? ` · ${fmtMs(cell.wall_ms[i])}`
                            : ""}
                        </Typography>
                      ))}
                    </Stack>
                  </Collapse>
                </Box>
              );
            })}
            {sourceRef ? (
              <CustomTooltip
                describeChild
                show
                size="small"
                placement="left"
                title={`The real conversation this task came from (${sourceRef}). Trace only: no checks.`}
              >
                <Stack
                  direction="row"
                  alignItems="center"
                  spacing={0.75}
                  sx={{
                    py: 0.25,
                    px: 0.5,
                    mt: 0.5,
                    borderTop: "1px dashed",
                    borderColor: "divider",
                  }}
                >
                  <Checkbox
                    size="small"
                    checked={laneIds.includes(productionId)}
                    disabled={!laneIds.includes(productionId) && full}
                    onChange={() => toggle(productionId)}
                    inputProps={{ "aria-label": `Production · ${sourceRef}` }}
                    sx={{ p: 0.5 }}
                  />
                  <Iconify
                    icon="mdi:access-point"
                    width={14}
                    sx={{ color: "text.secondary" }}
                  />
                  <Typography typography="s2" fontWeight={600}>
                    Production · {sourceRef}
                  </Typography>
                  <Typography typography="s3" color="text.secondary">
                    trace only
                  </Typography>
                </Stack>
              </CustomTooltip>
            ) : null}
          </Stack>
          {runs.length > MAX_LANES ? (
            <Button
              size="small"
              sx={{ mt: 1, px: 0.5 }}
              onClick={() => (setAnchor(null), onCards())}
              startIcon={<Iconify icon="mdi:view-grid-outline" width={16} />}
            >
              See all {runs.length} as cards
            </Button>
          ) : null}
          {onBlind ? (
            // IT-R1 G4: Blind review lives with the lanes (and View ▾ / ⌘K).
            <FormControlLabel
              sx={{
                m: 0,
                mt: 1,
                pt: 0.75,
                width: "100%",
                borderTop: "1px solid",
                borderColor: "divider",
              }}
              control={
                <Switch
                  size="small"
                  checked={Boolean(blindOn)}
                  onChange={() => onBlind()}
                />
              }
              label={
                <Typography typography="s2">
                  Blind review · hide run names while you rate
                </Typography>
              }
            />
          ) : null}
        </Box>
      </Popover>
    </>
  );
}

LanePicker.propTypes = {
  runs: PropTypes.array.isRequired,
  laneIds: PropTypes.array.isRequired,
  cells: PropTypes.object,
  onChange: PropTypes.func.isRequired,
  sort: PropTypes.string,
  onSort: PropTypes.func,
  sourceRef: PropTypes.string,
  productionId: PropTypes.string,
  onCards: PropTypes.func,
  blind: PropTypes.bool,
  blindOn: PropTypes.bool,
  onBlind: PropTypes.func,
};
