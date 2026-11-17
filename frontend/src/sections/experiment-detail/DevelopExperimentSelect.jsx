import {
  Box,
  ButtonBase,
  InputAdornment,
  MenuItem,
  Popover,
  Stack,
  TextField,
  Typography,
} from "@mui/material";
import PropTypes from "prop-types";
import React, { useMemo, useRef, useState } from "react";
import { useLocation, useNavigate } from "react-router";
import Iconify from "src/components/iconify";
import { useDatasetRuns } from "src/api/develop/runs";
import { getRunStatus } from "src/sections/common/Runs/runStatus";

/** The most active status across an experiment's runs (running first). */
export function experimentStatusOf(runs) {
  const infos = (runs ?? []).map((r) => getRunStatus(r));
  return (
    infos.find((i) => i.active) ??
    infos.find((i) => i.waiting) ??
    infos.find((i) => i.key === "Failed") ??
    infos.find((i) => i.key === "Cancelled") ??
    // the capability contract: a run with errored rows never hides behind a green one.
    infos.find((i) => i.key === "PartialCompleted") ??
    infos[0] ??
    null
  );
}

/** Experiments of a dataset from its runs, newest first: `#n name ●status`. */
export function experimentsFromRuns(runs) {
  const byId = new Map();
  (runs ?? []).forEach((r) => {
    if (!byId.has(r.experiment_id))
      byId.set(r.experiment_id, {
        id: r.experiment_id,
        ordinal: r.experiment_ordinal,
        name: r.experiment_name,
        runs: [],
      });
    byId.get(r.experiment_id).runs.push(r);
  });
  return [...byId.values()].sort((a, b) => b.ordinal - a.ordinal);
}

/** Search matches `#4`, `4` or a word of the name. */
export function matchesExperiment(exp, query) {
  const q = (query ?? "").trim().toLowerCase();
  if (!q) return true;
  const num = q.replace(/^#/, "");
  if (/^\d+$/.test(num)) return String(exp.ordinal).startsWith(num);
  return (exp.name ?? "").toLowerCase().includes(q);
}

// `#4`, or "" when the experiment has no number (the experiment read does not
// carry one): never `#undefined`; the experiment's name then names it.
const numberText = (ordinal) =>
  ordinal == null || ordinal === "" ? "" : `#${ordinal}`;

const StatusDot = ({ info }) =>
  info ? (
    <Stack direction="row" alignItems="center" gap={0.5} sx={{ flexShrink: 0 }}>
      <Box
        sx={{ width: 6, height: 6, borderRadius: "50%", bgcolor: info.color }}
      />
      <Typography typography="s3" color="text.secondary" noWrap>
        {info.label}
      </Typography>
    </Stack>
  ) : null;
StatusDot.propTypes = { info: PropTypes.object };

/**
 * Experiment switcher in the experiment page header (PRODUCT-SPEC §3.10.2):
 * `#4 Support agent v3 · 3 trials`, a list of the dataset's experiments with
 * their status; search matches `#4`. Keeps the current tab.
 */
const DevelopExperimentSelect = ({ experiment, maxWidth = 340 }) => {
  const [open, setOpen] = useState(false);
  const [search, setSearch] = useState("");
  const anchorRef = useRef(null);
  const navigate = useNavigate();
  const { pathname } = useLocation();
  const tab = pathname.endsWith("/summary")
    ? "summary"
    : pathname.endsWith("/matrix")
      ? "matrix"
      : "data";
  const datasetId = experiment?.dataset_id;
  const { data, isLoading } = useDatasetRuns(datasetId, { enabled: open });
  const experiments = useMemo(
    () =>
      experimentsFromRuns(data?.results).filter((e) =>
        matchesExperiment(e, search),
      ),
    [data, search],
  );

  const close = () => {
    setOpen(false);
    setSearch("");
  };

  return (
    <>
      <ButtonBase
        ref={anchorRef}
        onClick={() => setOpen(true)}
        aria-label="Switch experiment"
        sx={{
          height: 38,
          px: 1.5,
          gap: 1,
          border: "1px solid",
          borderColor: "divider",
          borderRadius: 0.75,
          maxWidth,
          "&:hover": { borderColor: "text.primary" },
        }}
      >
        {experiment ? (
          <>
            {numberText(experiment.ordinal) && (
              <Typography
                typography="s1"
                fontWeight="fontWeightSemiBold"
                sx={{ flexShrink: 0 }}
              >
                {numberText(experiment.ordinal)}
              </Typography>
            )}
            <Typography
              typography="s1"
              fontWeight={500}
              noWrap
              sx={{ minWidth: 0 }}
            >
              {experiment.name}
            </Typography>
          </>
        ) : (
          <Typography typography="s1" color="text.secondary">
            Loading experiment…
          </Typography>
        )}
        <Iconify
          icon="mdi:chevron-down"
          width={18}
          sx={{ color: "text.secondary", flexShrink: 0 }}
        />
      </ButtonBase>
      <Popover
        open={open}
        anchorEl={anchorRef.current}
        onClose={close}
        anchorOrigin={{ vertical: "bottom", horizontal: "left" }}
        transformOrigin={{ vertical: "top", horizontal: "left" }}
        slotProps={{ paper: { sx: { width: 420, mt: 0.5, borderRadius: 1 } } }}
      >
        <Box sx={{ p: 1 }}>
          <TextField
            autoFocus
            placeholder="Search by #number or name"
            size="small"
            value={search}
            onChange={(e) => setSearch(e.target.value)}
            InputProps={{
              startAdornment: (
                <InputAdornment position="start">
                  <Iconify
                    icon="solar:magnifier-linear"
                    width={16}
                    sx={{ color: "text.disabled" }}
                  />
                </InputAdornment>
              ),
            }}
            fullWidth
          />
          <Typography
            typography="s3"
            color="text.disabled"
            sx={{
              px: 1,
              pt: 1,
              pb: 0.5,
              display: "block",
              textTransform: "uppercase",
              letterSpacing: "0.5px",
            }}
          >
            Experiments on this dataset
          </Typography>
          <Box sx={{ maxHeight: 300, overflow: "auto" }}>
            {isLoading && (
              <Typography
                typography="s2"
                color="text.secondary"
                sx={{ px: 1, py: 1 }}
              >
                Loading…
              </Typography>
            )}
            {!isLoading && experiments.length === 0 && (
              <Typography
                typography="s2"
                color="text.secondary"
                sx={{ px: 1, py: 1 }}
              >
                {search
                  ? `No experiment matches “${search}”`
                  : "No experiments yet"}
              </Typography>
            )}
            {experiments.map((exp) => (
              <MenuItem
                key={exp.id}
                selected={exp.id === experiment?.id}
                onClick={() => {
                  close();
                  navigate(`/dashboard/develop/experiment/${exp.id}/${tab}`);
                }}
                sx={{ gap: 1, borderRadius: 0.5, py: 0.75 }}
              >
                {numberText(exp.ordinal) && (
                  <Typography
                    typography="s1"
                    fontWeight="fontWeightSemiBold"
                    sx={{ width: 32, flexShrink: 0 }}
                  >
                    {numberText(exp.ordinal)}
                  </Typography>
                )}
                <Typography
                  typography="s1"
                  noWrap
                  sx={{ flex: 1, minWidth: 0 }}
                >
                  {exp.name}
                </Typography>
                <Typography
                  typography="s3"
                  color="text.disabled"
                  sx={{ flexShrink: 0 }}
                >
                  {`${exp.runs.length} ${exp.runs.length === 1 ? "run" : "runs"}`}
                </Typography>
                <StatusDot info={experimentStatusOf(exp.runs)} />
              </MenuItem>
            ))}
          </Box>
        </Box>
      </Popover>
    </>
  );
};

DevelopExperimentSelect.propTypes = {
  experiment: PropTypes.object,
  maxWidth: PropTypes.number,
};

export default DevelopExperimentSelect;
