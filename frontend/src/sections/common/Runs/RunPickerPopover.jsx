import React, { useEffect, useMemo, useRef, useState } from "react";
import PropTypes from "prop-types";
import { Link as RouterLink } from "react-router-dom";
import {
  Box,
  Button,
  Chip,
  CircularProgress,
  Divider,
  Popover,
  Stack,
  Typography,
} from "@mui/material";
import Iconify from "src/components/iconify";
import CustomTooltip from "src/components/tooltip";
import FormSearchField from "src/components/FormSearchField/FormSearchField";
import { useDatasetRuns } from "src/api/develop/runs";
import { runColorAt } from "./runColors";
import { kindLabel, runKindOf } from "./runKind";
import { provenanceOf } from "./provenanceModel";
import SourceLogo from "src/sections/common/RunSources/SourceLogo";
import {
  versionPair,
  versionTags,
} from "src/sections/experiment-detail/results/pageCandidate";
import { runMatches, sameTarget } from "./runMatch";
import { notRunHere } from "./notRunHere";
import { useRunSourceAvailability } from "src/sections/common/RunSources/useRunSourceAvailability";
import { PickerGroupTitle, RunPickerRow } from "./RunPickerList";
import { pickerPresets } from "./pickerPresets";

// IT-R5 G6 P4: what a preset needs two of.
const PRESET_NOUN = {
  "harness-vs-code": "harness or coded-agent runs",
  frameworks: "frameworks",
  approaches: "approaches",
};

/**
 * `Correctness 82.1%` for a picker row: the run's first eval, from R1
 * `metrics.aggregates` (`include=metrics`); null without a value.
 */
export function keyMetricText(run, metricDefs) {
  const key = run?.metric_keys?.[0];
  const agg = key && run?.metrics?.aggregates?.[key];
  if (!agg || agg.n === 0) return null;
  const def = (metricDefs ?? []).find((d) => d.key === key);
  const name =
    def?.name ??
    key
      .replace(/^[a-z]+:/, "")
      .replace(/_/g, " ")
      .replace(/^./, (c) => c.toUpperCase());
  const value = agg.pass_rate ?? agg.avg;
  if (!Number.isFinite(value)) return null;
  const text =
    agg.pass_rate != null
      ? `${Number((value * 100).toFixed(1))}%`
      : value.toFixed(2);
  return `${name} ${text}`;
}

// IT-R2 G2 P1 (rule 14): the add action for a search that names a source.
// A source the server's report does not let this workspace run (or that it
// has not answered for yet) keeps the line and the action, disabled, with
// the reason under it.
function NotRunHereAction({ search, datasetId, datasetName, fallback }) {
  const availability = useRunSourceAvailability();
  const miss = notRunHere(search, { datasetId, datasetName, availability });
  if (!miss) return fallback;
  return (
    <Stack
      gap={0.75}
      alignItems="flex-start"
      sx={{ px: 1, py: 1 }}
      data-testid="picker-not-run-here"
    >
      <Typography typography="s2" color="text.secondary">
        {miss.text}
      </Typography>
      {miss.unavailableReason ? (
        <>
          <Button
            size="small"
            variant="outlined"
            disabled
            startIcon={<Iconify icon="mdi:plus" width={16} />}
          >
            {miss.action}
          </Button>
          <Typography typography="s3" color="text.secondary">
            {miss.unavailableReason}
          </Typography>
        </>
      ) : (
        <Button
          size="small"
          variant="outlined"
          component={RouterLink}
          to={miss.href}
          startIcon={<Iconify icon="mdi:plus" width={16} />}
        >
          {miss.action}
        </Button>
      )}
    </Stack>
  );
}

NotRunHereAction.propTypes = {
  search: PropTypes.string.isRequired,
  datasetId: PropTypes.string,
  datasetName: PropTypes.string,
  fallback: PropTypes.node,
};

/**
 * IT-R2 G2 P1 (rule 14): an empty search that names a framework or harness
 * says it has not run here and offers to add it.
 */
function EmptyRuns({ search, datasetId, datasetName, fallback }) {
  const empty = (
    <Typography typography="s2" color="text.disabled" sx={{ px: 1, py: 0.75 }}>
      {fallback}
    </Typography>
  );
  if (!search) return empty;
  return (
    <NotRunHereAction
      search={search}
      datasetId={datasetId}
      datasetName={datasetName}
      fallback={empty}
    />
  );
}

EmptyRuns.propTypes = {
  search: PropTypes.string,
  datasetId: PropTypes.string,
  datasetName: PropTypes.string,
  fallback: PropTypes.string,
};

// The Kind filter's fixed chips (R2 §3.3.1 Run picker); other kinds present
// on the dataset (Traces, HTTP, Upload) are added after them.
const PICKER_KINDS = ["prompt", "agent", "code", "harness"];

/**
 * RunPickerPopover — pick runs to add to a comparison (PRODUCT-SPEC §4,
 * §3.10.2–3). A trigger button opens a searchable, multi-select list:
 * `Same target` first (when `groupBySameTarget`), then `In this dataset`.
 * Each row: RunChip with status, version (amber when it differs from the
 * runs in view) and age. At most `max` runs in the comparison, counting
 * `excludeIds`.
 *
 * Props (frozen):
 * @param {string} props.datasetId
 * @param {string[]} [props.excludeIds]  runs already in view (not offered);
 *   the first is the reference for `Same target` and the version check
 * @param {number} [props.max=8]  comparison size cap
 * @param {boolean} [props.groupBySameTarget]
 * @param {(runIds: string[]) => void} props.onPick
 * @param {string} [props.label="Add runs"]  trigger text
 * @param {string} [props.confirmLabel]  footer button, default "Add n runs"
 * @param {string[]} [props.includeIds]  IT-R2 G2 P1 (additive): the runs of
 *   the page's experiment, listed first under `Runs in this experiment`
 *   (ticked when 4 or fewer, untick to make room) instead of being hidden;
 *   the rest are grouped by experiment under `Other experiments on <name>`.
 *   `onPick` then receives every ticked run, this experiment's included.
 * @param {string} [props.datasetName]  for `Other experiments on …`
 */
const PRETICK_MAX = 4;

const RunPickerPopover = ({
  datasetId,
  excludeIds = [],
  max = 8,
  groupBySameTarget = false,
  onPick,
  label = "Add runs",
  confirmLabel,
  includeIds,
  datasetName,
  openSignal,
  anchorEl,
}) => {
  const anchorRef = useRef(null);
  const [open, setOpen] = useState(false);
  const [search, setSearch] = useState("");
  const [picked, setPicked] = useState([]);
  const byExperiment = Array.isArray(includeIds);
  const [kind, setKind] = useState("all");
  const [framework, setFramework] = useState("all");
  // IT-R5 G6 P4: presets, kind and framework fold behind one `Filters (N)`
  // row, so the run list comes first.
  const [filtersOpen, setFiltersOpen] = useState(false);
  const reference = excludeIds[0];

  // `config` tells the run kinds apart (R1 include=config, additive).
  const { data, isLoading, isError, refetch } = useDatasetRuns(datasetId, {
    enabled: open,
    include: "metrics,config",
    sameTargetAs: groupBySameTarget ? reference : undefined,
  });

  const runs = useMemo(() => data?.results ?? [], [data]);
  const refRun = runs.find((r) => r.run_id === reference);
  const notInView = runs.filter(
    (r) => !excludeIds.includes(r.run_id) && r.listed !== false,
  );
  const kindCounts = useMemo(() => {
    const counts = {};
    notInView.forEach((r) => {
      const k = runKindOf(r);
      counts[k] = (counts[k] ?? 0) + 1;
    });
    return counts;
  }, [notInView]);
  const kindChips = [
    "all",
    ...PICKER_KINDS,
    ...Object.keys(kindCounts).filter((k) => !PICKER_KINDS.includes(k)),
  ];
  // MUST 7: framework facet (with logos) and version tags.
  const sourceOf = (r) => provenanceOf(r).source;
  const frameworks = useMemo(() => {
    const m = new Map();
    notInView
      .filter((r) => kind === "all" || runKindOf(r) === kind)
      .forEach((r) => {
        const src = sourceOf(r);
        if (!src?.name) return;
        const cur = m.get(src.name) ?? {
          name: src.name,
          entryId: src.entryId,
          n: 0,
        };
        cur.n += 1;
        m.set(src.name, cur);
      });
    return [...m.values()];
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [notInView, kind]);
  const tags = useMemo(() => versionTags(runs), [runs]);
  const pair = useMemo(() => versionPair(runs), [runs]);
  // IT-R4 G6 P2: one click adds a whole comparison.
  const presets = useMemo(
    () => pickerPresets(runs, excludeIds),
    // eslint-disable-next-line react-hooks/exhaustive-deps
    [runs, excludeIds.join(",")],
  );
  const offered = notInView.filter(
    (r) =>
      runMatches(r, search) &&
      (kind === "all" || runKindOf(r) === kind) &&
      (framework === "all" || sourceOf(r)?.name === framework),
  );
  const same =
    groupBySameTarget && refRun
      ? offered.filter((r) => sameTarget(r, refRun))
      : [];
  const rest = offered.filter((r) => !same.includes(r));
  const room = Math.max(0, max - excludeIds.length);
  const full = picked.length >= room;
  // Experiment mode: this experiment first, then one group per experiment.
  const mine = byExperiment
    ? offered.filter((r) => includeIds.includes(r.run_id))
    : [];
  const otherGroups = useMemo(() => {
    if (!byExperiment) return [];
    const m = new Map();
    offered
      .filter((r) => !includeIds.includes(r.run_id))
      .forEach((r) => {
        const k = r.experiment_id ?? "other";
        if (!m.has(k))
          m.set(k, {
            key: k,
            ordinal: r.experiment_ordinal ?? null,
            name: r.experiment_name ?? null,
            runs: [],
          });
        m.get(k).runs.push(r);
      });
    return [...m.values()].sort((a, b) => (b.ordinal ?? 0) - (a.ordinal ?? 0));
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [byExperiment, offered.map((r) => r.run_id).join(","), includeIds]);

  // IT-R5 G6 P1 (additive): a parent menu (`Compare ▾` → `with…`) opens
  // the picker by bumping `openSignal`; with `anchorEl` the trigger button
  // is not rendered and the popover anchors there.
  const begin = () => {
    if (byExperiment)
      setPicked(
        includeIds.length <= PRETICK_MAX ? includeIds.slice(0, room) : [],
      );
    setOpen(true);
  };
  useEffect(() => {
    if (openSignal) begin();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [openSignal]);

  const close = () => {
    setOpen(false);
    setSearch("");
    setPicked([]);
    setKind("all");
    setFramework("all");
    setFiltersOpen(false);
  };

  const toggle = (run) =>
    setPicked((p) =>
      p.includes(run.run_id)
        ? p.filter((id) => id !== run.run_id)
        : p.length < room
          ? [...p, run.run_id]
          : p,
    );

  const colorIndex = (id) =>
    excludeIds.length + Math.max(0, picked.indexOf(id));

  const renderRows = (list) =>
    list.map((r) => {
      const selected = picked.includes(r.run_id);
      return (
        <RunPickerRow
          key={r.run_id}
          run={r}
          multi
          color={selected ? runColorAt(colorIndex(r.run_id)) : undefined}
          selected={selected}
          disabled={!selected && full}
          disabledReason={`Compare up to ${max} runs`}
          isPinned={!!r.is_dataset_baseline}
          referenceVersionNumber={refRun?.dataset_version?.number}
          onToggle={toggle}
          versionTag={tags[r.run_id]}
        />
      );
    });

  return (
    <>
      {anchorEl === undefined && (
        <CustomTooltip
          show
          size="small"
          placement="bottom"
          title={
            room === 0
              ? `Compare takes up to ${max} runs. Remove one to add another.`
              : "Add runs from this experiment or any other experiment on this dataset (for example another experiment's frameworks or harnesses)"
          }
        >
          <span
            style={{ display: "inline-flex", flexShrink: 0 }}
            ref={anchorRef}
          >
            <Button
              variant="outlined"
              size="small"
              aria-disabled={room === 0 || undefined}
              onClick={begin}
              disabled={room === 0}
              startIcon={<Iconify icon="mdi:plus" width={16} />}
              sx={{ height: 32, px: 1.25, flexShrink: 0, whiteSpace: "nowrap" }}
            >
              <Typography typography="s1" fontWeight="fontWeightMedium">
                {label}
              </Typography>
            </Button>
          </span>
        </CustomTooltip>
      )}
      <Popover
        open={
          open && Boolean(anchorEl === undefined ? anchorRef.current : anchorEl)
        }
        anchorEl={anchorEl === undefined ? anchorRef.current : anchorEl}
        onClose={close}
        anchorOrigin={{ vertical: "bottom", horizontal: "left" }}
        transformOrigin={{ vertical: "top", horizontal: "left" }}
        slotProps={{
          paper: {
            sx: {
              mt: 0.5,
              p: 1,
              borderRadius: 1,
              width: 460,
              maxHeight: 520,
              display: "flex",
              flexDirection: "column",
            },
          },
        }}
      >
        <FormSearchField
          searchQuery={search}
          onChange={(e) => setSearch(e.target.value)}
          placeholder="Search #4, name, model"
          autoFocus
          fullWidth
          sx={{ mb: 0.5 }}
        />
        {/* IT-R5 G6 P4: one row; the presets and facets open under it. */}
        {(() => {
          const active =
            (kind !== "all" ? 1 : 0) + (framework !== "all" ? 1 : 0);
          return (
            <Stack direction="row" alignItems="center" gap={1} sx={{ mb: 0.5 }}>
              <Button
                size="small"
                variant="outlined"
                color={active ? "primary" : "inherit"}
                onClick={() => setFiltersOpen((o) => !o)}
                aria-expanded={filtersOpen}
                data-testid="picker-filters"
                startIcon={<Iconify icon="mdi:filter-variant" width={16} />}
                endIcon={
                  <Iconify
                    icon={filtersOpen ? "mdi:chevron-up" : "mdi:chevron-down"}
                    width={16}
                  />
                }
                sx={{
                  height: 26,
                  typography: "s2",
                  borderColor: "divider",
                  boxShadow: "none",
                }}
              >
                {active ? `Filters (${active})` : "Filters"}
              </Button>
              <Typography typography="s3" color="text.secondary" noWrap>
                {[
                  presets.length || pair
                    ? `${presets.length + (pair ? 1 : 0)} presets`
                    : null,
                  kind !== "all" ? kindLabel(kind) : null,
                  framework !== "all" ? framework : null,
                ]
                  .filter(Boolean)
                  .join(" · ")}
              </Typography>
              {active ? (
                <Button
                  size="small"
                  variant="text"
                  onClick={() => {
                    setKind("all");
                    setFramework("all");
                  }}
                  sx={{ height: 26, typography: "s2", ml: "auto" }}
                >
                  Clear
                </Button>
              ) : null}
            </Stack>
          );
        })()}
        {filtersOpen ? (
          <Box data-testid="picker-filters-panel">
            {pair || presets.length ? (
              <Stack
                direction="row"
                alignItems="center"
                gap={0.75}
                flexWrap="wrap"
                sx={{ mb: 0.5 }}
                data-testid="picker-presets"
              >
                <Typography typography="s3" color="text.secondary">
                  Presets
                </Typography>
                {presets.map((preset) => {
                  const want = preset.ids;
                  const on =
                    want.length > 0 && want.every((id) => picked.includes(id));
                  const tooBig = want.length > room;
                  // IT-R5 G6 P4: a new comparison needs 2 runs; a preset that
                  // holds 1 says so and where more come from.
                  const tooFew = byExperiment && want.length < 2;
                  return (
                    <CustomTooltip
                      key={preset.id}
                      show
                      size="small"
                      placement="top"
                      title={
                        tooFew
                          ? `Needs 2 ${PRESET_NOUN[preset.id] ?? "runs"}; this dataset has ${want.length}. Add runs from another experiment, or run one more.`
                          : tooBig
                            ? `${preset.tooltip} (${want.length}). Compare takes up to ${max} runs: remove ${want.length - room} first.`
                            : `${preset.tooltip} (${want.length})`
                      }
                    >
                      <span>
                        <Chip
                          size="small"
                          label={`${preset.label} · ${want.length}`}
                          variant={on ? "filled" : "outlined"}
                          color={on ? "primary" : "default"}
                          disabled={tooBig || tooFew}
                          onClick={() =>
                            setPicked((p) =>
                              on
                                ? p.filter((id) => !want.includes(id))
                                : [...new Set([...p, ...want])].slice(0, room),
                            )
                          }
                          data-testid={`picker-preset-${preset.id}`}
                          sx={{ height: 24, typography: "s2" }}
                        />
                      </span>
                    </CustomTooltip>
                  );
                })}
                {pair &&
                  (() => {
                    const want = [pair.previous, pair.latest].filter(
                      (id) => !excludeIds.includes(id),
                    );
                    const on =
                      want.length > 0 &&
                      want.every((id) => picked.includes(id));
                    const label = `Latest vs previous version · ${tags[pair.latest]?.text ?? ""} vs ${tags[pair.previous]?.text ?? ""}`;
                    return (
                      <CustomTooltip
                        show
                        size="small"
                        placement="top"
                        title={
                          want.length
                            ? `Adds ${want.length === 2 ? "both runs" : "the other run"}: the newest version of the agent with the most runs and the version before it`
                            : "Both are already in view"
                        }
                      >
                        <span>
                          <Chip
                            size="small"
                            icon={
                              <Iconify icon="mdi:source-branch" width={14} />
                            }
                            label={label}
                            variant={on ? "filled" : "outlined"}
                            color={on ? "primary" : "default"}
                            disabled={!want.length || want.length > room}
                            onClick={() =>
                              setPicked((p) =>
                                on
                                  ? p.filter((id) => !want.includes(id))
                                  : [...new Set([...p, ...want])].slice(
                                      0,
                                      room,
                                    ),
                              )
                            }
                            data-testid="picker-preset-latest"
                            sx={{ height: 24, typography: "s2" }}
                          />
                        </span>
                      </CustomTooltip>
                    );
                  })()}
              </Stack>
            ) : null}
            <Stack
              direction="row"
              gap={0.5}
              flexWrap="wrap"
              role="group"
              aria-label="Kind"
              sx={{ mb: 0.5 }}
            >
              {kindChips.map((k) => {
                const n = k === "all" ? notInView.length : kindCounts[k] ?? 0;
                const selected = kind === k;
                return (
                  <Chip
                    key={k}
                    size="small"
                    label={`${k === "all" ? "All" : kindLabel(k)} ${n}`}
                    variant={selected ? "filled" : "outlined"}
                    color={selected ? "primary" : "default"}
                    disabled={k !== "all" && n === 0}
                    onClick={() => setKind(k)}
                    aria-pressed={selected}
                    sx={{
                      height: 24,
                      typography: "s2",
                      fontVariantNumeric: "tabular-nums",
                    }}
                  />
                );
              })}
            </Stack>
            {frameworks.length > 1 ? (
              <Stack
                direction="row"
                gap={0.5}
                flexWrap="wrap"
                role="group"
                aria-label="Framework"
                sx={{ mb: 0.5 }}
              >
                {[{ name: "all", n: null }, ...frameworks].map((f) => {
                  const selected = framework === f.name;
                  return (
                    <Chip
                      key={f.name}
                      size="small"
                      icon={
                        f.entryId ? (
                          <Box
                            component="span"
                            sx={{ display: "inline-flex", ml: 0.5 }}
                          >
                            <SourceLogo entryId={f.entryId} size={16} />
                          </Box>
                        ) : undefined
                      }
                      label={
                        f.name === "all" ? "Any framework" : `${f.name} ${f.n}`
                      }
                      variant={selected ? "filled" : "outlined"}
                      color={selected ? "primary" : "default"}
                      onClick={() => setFramework(f.name)}
                      aria-pressed={selected}
                      sx={{
                        height: 24,
                        typography: "s2",
                        fontVariantNumeric: "tabular-nums",
                      }}
                    />
                  );
                })}
              </Stack>
            ) : null}
          </Box>
        ) : null}
        <Box sx={{ overflowY: "auto", flex: 1, minHeight: 120 }}>
          {isLoading && (
            <Stack alignItems="center" sx={{ py: 3 }}>
              <CircularProgress size={18} />
            </Stack>
          )}
          {isError && (
            <Stack alignItems="center" gap={1} sx={{ py: 3 }}>
              <Typography typography="s2" color="text.secondary">
                Couldn’t load runs.
              </Typography>
              <Button size="small" variant="outlined" onClick={() => refetch()}>
                Retry
              </Button>
            </Stack>
          )}
          {!isLoading && !isError && byExperiment ? (
            <>
              {mine.length > 0 && (
                <>
                  <PickerGroupTitle>Runs in this experiment</PickerGroupTitle>
                  {renderRows(mine)}
                </>
              )}
              {otherGroups.length > 0 && (
                <PickerGroupTitle>
                  {`Other experiments on ${datasetName ?? "this dataset"}`}
                </PickerGroupTitle>
              )}
              {otherGroups.map((g) => (
                <Box key={g.key} data-testid="picker-experiment-group">
                  <Typography
                    typography="s3"
                    color="text.secondary"
                    noWrap
                    sx={{ px: 1, pt: 0.5, fontWeight: 600 }}
                  >
                    {[g.ordinal != null ? `#${g.ordinal}` : null, g.name]
                      .filter(Boolean)
                      .join(" ")}
                  </Typography>
                  {renderRows(g.runs)}
                </Box>
              ))}
              {!mine.length && !otherGroups.length && (
                <EmptyRuns
                  search={search}
                  datasetId={datasetId}
                  datasetName={datasetName}
                  fallback={
                    search ? `No runs match “${search}”` : "No other runs"
                  }
                />
              )}
            </>
          ) : null}
          {!byExperiment && !isLoading && !isError && same.length > 0 && (
            <>
              <PickerGroupTitle>Same target</PickerGroupTitle>
              {renderRows(same)}
            </>
          )}
          {!byExperiment && !isLoading && !isError && (
            <>
              <PickerGroupTitle>
                {same.length ? "In this dataset" : "Runs on this dataset"}
              </PickerGroupTitle>
              {rest.length ? (
                renderRows(rest)
              ) : (
                <EmptyRuns
                  search={same.length ? "" : search}
                  datasetId={datasetId}
                  datasetName={datasetName}
                  fallback={
                    search
                      ? `No runs match “${search}”`
                      : kind !== "all"
                        ? `Nothing to add under ${kindLabel(kind)}`
                        : same.length
                          ? "No other runs"
                          : "No other runs on this dataset"
                  }
                />
              )}
            </>
          )}
        </Box>
        <Divider sx={{ my: 0.75 }} />
        <Stack
          direction="row"
          alignItems="center"
          justifyContent="space-between"
          sx={{ px: 0.5 }}
        >
          <Typography typography="s2" color="text.secondary">
            {picked.length
              ? `${picked.length} selected · up to ${room}`
              : `Select up to ${room} ${room === 1 ? "run" : "runs"}`}
          </Typography>
          <Stack direction="row" gap={1}>
            <Button size="small" variant="outlined" onClick={close}>
              Cancel
            </Button>
            <CustomTooltip
              show={byExperiment ? picked.length < 2 : !picked.length}
              size="small"
              placement="top"
              title={byExperiment ? "Tick 2 or more runs" : "Tick a run to add"}
            >
              <span style={{ display: "inline-flex" }}>
                <Button
                  size="small"
                  variant="contained"
                  color="primary"
                  disabled={byExperiment ? picked.length < 2 : !picked.length}
                  onClick={() => {
                    const ids = picked;
                    close();
                    onPick?.(ids);
                  }}
                >
                  {byExperiment
                    ? picked.length < 2
                      ? "Pick 2 or more runs"
                      : `Compare ${picked.length} runs`
                    : confirmLabel ??
                      (picked.length > 1
                        ? `Add ${picked.length} runs`
                        : "Add run")}
                </Button>
              </span>
            </CustomTooltip>
          </Stack>
        </Stack>
      </Popover>
    </>
  );
};

RunPickerPopover.propTypes = {
  datasetId: PropTypes.string.isRequired,
  excludeIds: PropTypes.arrayOf(PropTypes.string),
  max: PropTypes.number,
  groupBySameTarget: PropTypes.bool,
  onPick: PropTypes.func.isRequired,
  label: PropTypes.string,
  confirmLabel: PropTypes.string,
  includeIds: PropTypes.arrayOf(PropTypes.string),
  datasetName: PropTypes.string,
  // IT-R5 G6 P1 (additive): open from a parent menu; anchor without a trigger.
  openSignal: PropTypes.number,
  anchorEl: PropTypes.any,
};

export default RunPickerPopover;
