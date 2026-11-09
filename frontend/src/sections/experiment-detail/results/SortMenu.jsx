import React, { useRef, useState } from "react";
import PropTypes from "prop-types";
import {
  Box,
  Button,
  Divider,
  MenuItem,
  MenuList,
  Popover,
  Select,
  Stack,
  Typography,
} from "@mui/material";
import Iconify from "src/components/iconify";
import CustomTooltip from "src/components/tooltip";
import { SORT_ENTRIES, parseSort, sortLabel, sortString } from "./sortModel";

const selectSx = { typography: "s1", "& .MuiSelect-select": { py: 0.75 } };

/** `Sort ▾` (PRODUCT-SPEC §3.10.6): pick an order, then its metric and run. */
const SortMenu = ({
  sort,
  onChange,
  metrics,
  runs,
  baselineId,
  hasTrials,
  compact = false,
  shortLabel = false,
  defaultSort,
}) => {
  const anchorRef = useRef(null);
  const [open, setOpen] = useState(false);
  const current = parseSort(sort);
  const [entryId, setEntryId] = useState(null);
  const candidates = runs.filter((r) => r.run_id !== baselineId);
  const [metric, setMetric] = useState(current.metric ?? metrics[0]?.key ?? "");
  const [run, setRun] = useState(
    current.run ?? candidates[0]?.run_id ?? runs[0]?.run_id ?? "",
  );
  const entry = SORT_ENTRIES.find((e) => e.id === entryId) ?? null;
  const runChoices = entry?.compare ? candidates : runs;
  const metricsByKey = Object.fromEntries(metrics.map((m) => [m.key, m]));
  const runsById = Object.fromEntries(runs.map((r) => [r.run_id, r]));

  const close = () => {
    setOpen(false);
    setEntryId(null);
  };
  const choose = (e) => {
    if (!e.fields.length) {
      onChange(sortString(e.id));
      close();
      return;
    }
    if (!runChoicesFor(e).some((r) => r.run_id === run))
      setRun((e.compare ? candidates : runs)[0]?.run_id ?? "");
    if (!metrics.some((m) => m.key === metric))
      setMetric(metrics[0]?.key ?? "");
    setEntryId(e.id);
  };
  const runChoicesFor = (e) => (e.compare ? candidates : runs);
  const disabledReason = (e) =>
    e.compare && candidates.length === 0
      ? "Add a second run to compare against the baseline"
      : e.trials && !hasTrials
        ? "No run in view has trials"
        : null;

  const fullLabel = sortLabel(sort, { metricsByKey, runsById });
  const shownLabel = shortLabel
    ? sortLabel(sort, { metricsByKey, runsById, short: true })
    : fullLabel;
  // Compact: the sort icon already says "sort"; the full label is the hover.
  const label = compact ? shownLabel.replace(/^Sort: /, "") : shownLabel;
  // Highlighted only when it differs from the page's default sort (one
  // view: `Regressed first` with a baseline is the default, not a filter).
  const active = defaultSort
    ? sort !== defaultSort
    : current.entryId !== "order";

  return (
    <>
      <Button
        ref={anchorRef}
        variant="outlined"
        size="small"
        onClick={() => setOpen(true)}
        startIcon={<Iconify icon="material-symbols:sort-rounded" width={18} />}
        endIcon={<Iconify icon="mdi:chevron-down" width={16} />}
        sx={{
          height: 32,
          px: 1.25,
          typography: "s2",
          fontWeight: 500,
          maxWidth: 300,
          color: "text.primary",
          borderColor: "divider",
          ...(active && {
            borderColor: "primary.main",
            color: "primary.main",
          }),
          "& .MuiButton-endIcon": { ml: 0.5 },
        }}
      >
        <Box
          component="span"
          title={active || shownLabel !== fullLabel ? fullLabel : undefined}
          sx={{
            overflow: "hidden",
            textOverflow: "ellipsis",
            whiteSpace: "nowrap",
          }}
        >
          {label}
        </Box>
      </Button>
      <Popover
        open={open}
        anchorEl={anchorRef.current}
        onClose={close}
        anchorOrigin={{ vertical: "bottom", horizontal: "left" }}
        transformOrigin={{ vertical: "top", horizontal: "left" }}
        slotProps={{ paper: { sx: { mt: 0.5, borderRadius: 1 } } }}
      >
        <Stack direction="row">
          <MenuList dense sx={{ width: 260, py: 0.5 }}>
            {SORT_ENTRIES.map((e) => {
              const reason = disabledReason(e);
              const selected = entry
                ? e.id === entryId
                : e.id === current.entryId;
              const item = (
                <MenuItem
                  key={e.id}
                  selected={selected}
                  disabled={!!reason}
                  onClick={() => choose(e)}
                  sx={{ typography: "s1", py: 0.75 }}
                >
                  {e.label}
                  {e.fields.length > 0 && (
                    <Iconify
                      icon="mdi:chevron-right"
                      width={16}
                      sx={{ ml: "auto", color: "text.disabled" }}
                    />
                  )}
                </MenuItem>
              );
              return reason ? (
                <CustomTooltip
                  key={e.id}
                  show
                  size="small"
                  placement="right"
                  arrow
                  title={reason}
                >
                  <span>{item}</span>
                </CustomTooltip>
              ) : (
                item
              );
            })}
          </MenuList>
          {entry && (
            <>
              <Divider orientation="vertical" flexItem />
              <Stack spacing={1.5} sx={{ p: 2, width: 260 }}>
                <Typography typography="s1" fontWeight="fontWeightSemiBold">
                  {entry.label}
                </Typography>
                {entry.fields.includes("metric") && (
                  <Stack spacing={0.5}>
                    <Typography typography="s2" color="text.secondary">
                      Metric
                    </Typography>
                    <Select
                      size="small"
                      value={metric}
                      onChange={(e) => setMetric(e.target.value)}
                      sx={selectSx}
                    >
                      {metrics.map((m) => (
                        <MenuItem key={m.key} value={m.key}>
                          {m.name}
                        </MenuItem>
                      ))}
                    </Select>
                  </Stack>
                )}
                <Stack spacing={0.5}>
                  <Typography typography="s2" color="text.secondary">
                    Run
                  </Typography>
                  <Select
                    size="small"
                    value={run}
                    onChange={(e) => setRun(e.target.value)}
                    sx={selectSx}
                  >
                    {runChoices.map((r) => (
                      <MenuItem key={r.run_id} value={r.run_id}>
                        {`${r.label} ${r.name ?? ""}`.trim()}
                      </MenuItem>
                    ))}
                  </Select>
                </Stack>
                <Stack direction="row" justifyContent="flex-end" gap={1}>
                  <Button size="small" onClick={close}>
                    Cancel
                  </Button>
                  <Button
                    size="small"
                    variant="contained"
                    color="primary"
                    disabled={
                      !run || (entry.fields.includes("metric") && !metric)
                    }
                    onClick={() => {
                      onChange(sortString(entry.id, { metric, run }));
                      close();
                    }}
                  >
                    Sort
                  </Button>
                </Stack>
              </Stack>
            </>
          )}
        </Stack>
      </Popover>
    </>
  );
};

SortMenu.propTypes = {
  compact: PropTypes.bool,
  shortLabel: PropTypes.bool,
  defaultSort: PropTypes.string,
  sort: PropTypes.string,
  onChange: PropTypes.func.isRequired,
  metrics: PropTypes.array.isRequired,
  runs: PropTypes.array.isRequired,
  baselineId: PropTypes.string,
  hasTrials: PropTypes.bool,
};

export default SortMenu;
