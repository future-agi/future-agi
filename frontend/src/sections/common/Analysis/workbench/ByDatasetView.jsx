import React, { useMemo, useState } from "react";
import PropTypes from "prop-types";
import { useQueries } from "@tanstack/react-query";
import { useLocation, useNavigate } from "react-router-dom";
import {
  Alert,
  Button,
  Checkbox,
  ListItemText,
  Menu,
  MenuItem,
  Skeleton,
  Stack,
  Typography,
} from "@mui/material";
import Iconify from "src/components/iconify";
import axios, { endpoints } from "src/utils/axios";
import { datasetRunsParams } from "src/api/develop/runs";
import {
  R1_CONFIG_INCLUDE,
  configKeyOf,
  runConfigsKey,
  useByDataset,
} from "src/api/develop/r2";
import { compareSummaryHref } from "src/sections/common/Analysis/analysisLinks";
import { kindLabel, runKindOf } from "src/sections/common/Runs/runKind";
import ByDatasetTable from "./ByDatasetTable";
import GroupBarsChart from "./charts/GroupBarsChart";
import {
  buildByDataset,
  byDatasetPivot,
  configName,
  leaderboardDraftOf,
} from "./byDatasetModel";
// HO R8 (W5 B-H7): Save as leaderboard… (W5 C2's editor and page).
import LeaderboardEditor from "src/sections/common/Leaderboards/LeaderboardEditor";
import { leaderboardHref } from "src/sections/common/Leaderboards/leaderboardModel";
import { runAxisValues } from "./workbenchPivot";

// Static gate (HO S2): prototype-only doors fold away in other builds.
const PROTOTYPE_BUILD =
  import.meta.env.MODE === "test" ||
  (import.meta.env.VITE_ENVIRONMENT === "local" &&
    import.meta.env.VITE_PROTOTYPE_MODE === "true");

const humanize = (key) => {
  const s = String(key ?? "")
    .replace(/^[a-z]+:/, "")
    .replace(/_/g, " ");
  return s ? `${s.charAt(0).toUpperCase()}${s.slice(1)}` : "Headline measure";
};

/**
 * Workbench `across=datasets` (By dataset, R2-SPEC §3.3.7): R1 on every
 * dataset joined by config key; the table + `Bars by group`.
 */
const ByDatasetView = ({
  focusRun,
  configKey,
  axis,
  extraKeys,
  onExtraKeys,
  colColors,
  onMatchCell,
}) => {
  const navigate = useNavigate();
  const location = useLocation();
  const here = `${location.pathname}${location.search}`;
  const [chosen, setChosen] = useState({});
  const [anchor, setAnchor] = useState(null);
  const [boardDraft, setBoardDraft] = useState(null);
  const by = useByDataset({});
  const entries = useMemo(() => by.data ?? [], [by.data]);
  const params = datasetRunsParams({ include: R1_CONFIG_INCLUDE });
  const defs = useQueries({
    queries: entries.map((e) => ({
      queryKey: runConfigsKey(e.dataset.datasetId, params),
      queryFn: () =>
        axios.get(endpoints.develop.datasetRuns.list(e.dataset.datasetId), {
          params,
        }),
      select: (res) => res.data?.result?.metric_defs ?? [],
    })),
  });
  const model = useMemo(
    () =>
      buildByDataset(entries, {
        configKey,
        axis,
        extraKeys,
        chosen,
        focusValue: focusRun ? runAxisValues(focusRun)[axis] : undefined,
      }),
    [entries, configKey, axis, extraKeys, chosen, focusRun],
  );
  const measureNames = {};
  entries.forEach((e, i) => {
    const key = model.rows.find(
      (r) => r.dataset.datasetId === e.dataset.datasetId,
    )?.measureKey;
    if (!key) return;
    measureNames[e.dataset.datasetId] =
      e.dataset.kind === "task_suite"
        ? "pass@1"
        : defs[i]?.data?.find((d) => d.key === key)?.name ?? humanize(key);
  });
  const others = useMemo(() => {
    const seen = new Map();
    entries.forEach((e) =>
      e.runs.forEach((r) => {
        const k = configKeyOf(r);
        if (k && k !== configKey && r.listed !== false && !seen.has(k))
          seen.set(k, r);
      }),
    );
    return [...seen.entries()].map(([k, r]) => ({
      key: k,
      name: configName(r),
      kind: kindLabel(runKindOf(r)),
    }));
  }, [entries, configKey]);

  if (by.isLoading)
    return (
      <Stack gap={1.5}>
        <Skeleton variant="rounded" height={300} />
        <Skeleton variant="rounded" height={220} />
      </Stack>
    );
  if (by.isError)
    return (
      <Alert severity="error">
        Runs on other datasets could not be loaded.
      </Alert>
    );
  if (!model.rows.length)
    return (
      <Alert severity="info">
        {focusRun ? `${focusRun.label} ` : ""}has no runs with the same setup on
        other datasets.
      </Alert>
    );

  const open = (row) => {
    const runIds = model.columns
      .map((c) => row.cells[c.key]?.run?.run_id)
      .filter(Boolean);
    navigate(
      compareSummaryHref({
        datasetId: row.dataset.datasetId,
        runIds,
        baselineId: runIds[0],
        from: here,
      }),
    );
  };
  // L7c g6 (bydataset:view): the sentence counts the focused config's
  // datasets, so it names that config (not the column's axis value, which
  // other configs share).
  const focusName = configName(focusRun) || model.focusCol?.label;
  const rankable = new Set(
    defs
      .flatMap((d) => d.data ?? [])
      .filter((d) => d.kind === "eval" || d.kind === "system")
      .map((d) => d.key),
  );
  const draft = leaderboardDraftOf(model, {
    configKey,
    name: configName(focusRun ?? model.focusCol?.run),
    columnKeys: rankable,
  });
  const sentence = `${focusName} ran on ${model.ranOn} ${model.ranOn === 1 ? "dataset" : "datasets"}. Each row uses that dataset's own measure, so compare across a row, not down a column.`;
  const bars = byDatasetPivot(model);
  const barColors = Object.fromEntries(
    model.columns.map((c, i) => [c.key, colColors(i)]),
  );

  return (
    <Stack gap={1.5}>
      <GroupBarsChart
        pivot={bars.pivot}
        groups={bars.groups}
        measure={{ kind: "rate", higherIsBetter: true, short: "headline" }}
        colColors={barColors}
        title="Headline measure by dataset"
        axisTitle="Headline measure (%)"
        onOpenCell={(g) => {
          const row = model.rows.find((r) => r.dataset.name === g);
          if (row) open(row);
        }}
      />
      <ByDatasetTable
        model={model}
        measureNames={measureNames}
        sentence={sentence}
        onOpenRow={open}
        focusRunId={focusRun?.run_id}
        onMatchCell={
          onMatchCell
            ? (row, col, cell) => onMatchCell(cell.run.run_id)
            : undefined
        }
        onPickRun={(row, col, runId) =>
          setChosen((c) => ({
            ...c,
            [`${row.dataset.datasetId}|${col.key}`]: runId,
          }))
        }
        compareWith={
          <>
            {/* proto-off (L7c g1): boards read prototype-only paths, so other
                builds have no Save as leaderboard door. */}
            {PROTOTYPE_BUILD && draft && (
              <Button
                size="small"
                startIcon={<Iconify icon="mdi:trophy-outline" width={16} />}
                onClick={() => setBoardDraft(draft)}
                data-testid="by-dataset-save-leaderboard"
                sx={{ flexShrink: 0 }}
              >
                Save as leaderboard…
              </Button>
            )}
            {others.length > 0 && (
              <>
                <Button
                  size="small"
                  startIcon={<Iconify icon="mdi:plus" width={16} />}
                  endIcon={<Iconify icon="mdi:chevron-down" width={16} />}
                  onClick={(e) => setAnchor(e.currentTarget)}
                  sx={{ flexShrink: 0 }}
                >
                  Compare with
                </Button>
                <Menu
                  anchorEl={anchor}
                  open={Boolean(anchor)}
                  onClose={() => setAnchor(null)}
                  slotProps={{ paper: { sx: { maxHeight: 360, width: 340 } } }}
                >
                  <Typography
                    typography="s3"
                    color="text.secondary"
                    sx={{ px: 2, py: 0.5 }}
                  >
                    Add another setup as a column
                  </Typography>
                  {others.map((o) => {
                    const on = extraKeys.includes(o.key);
                    return (
                      <MenuItem
                        key={o.key}
                        onClick={() =>
                          onExtraKeys(
                            on
                              ? extraKeys.filter((k) => k !== o.key)
                              : [...extraKeys, o.key],
                          )
                        }
                        dense
                      >
                        <Checkbox
                          size="small"
                          checked={on}
                          sx={{ p: 0.25, mr: 1 }}
                        />
                        <ListItemText
                          primary={o.name}
                          secondary={o.kind}
                          primaryTypographyProps={{
                            typography: "s1",
                            noWrap: true,
                          }}
                          secondaryTypographyProps={{ typography: "s3" }}
                        />
                      </MenuItem>
                    );
                  })}
                </Menu>
              </>
            )}
          </>
        }
      />
      {PROTOTYPE_BUILD ? (
        <LeaderboardEditor
          open={Boolean(boardDraft)}
          initial={boardDraft}
          onClose={() => setBoardDraft(null)}
          onSaved={(read) => {
            setBoardDraft(null);
            const id = read?.definition?.id;
            if (id) navigate(leaderboardHref(id));
          }}
        />
      ) : null}
    </Stack>
  );
};

ByDatasetView.propTypes = {
  focusRun: PropTypes.object,
  configKey: PropTypes.string,
  axis: PropTypes.string.isRequired,
  extraKeys: PropTypes.array.isRequired,
  onExtraKeys: PropTypes.func.isRequired,
  colColors: PropTypes.func.isRequired,
  onMatchCell: PropTypes.func,
};

export default ByDatasetView;
