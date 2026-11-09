import React, { useEffect, useMemo, useRef, useState } from "react";
import PropTypes from "prop-types";
import {
  Box,
  Button,
  Chip,
  CircularProgress,
  Link,
  Stack,
  TextField,
  Typography,
} from "@mui/material";
import { useQuery } from "@tanstack/react-query";
import { useWatch } from "react-hook-form";
import axios, { endpoints } from "src/utils/axios";
import Iconify from "src/components/iconify";
import CustomTooltip from "src/components/tooltip";
import { FormSearchSelectFieldState } from "src/components/FromSearchSelectField";
import { useImportPreview } from "src/api/develop/r2";
import { useGetDatasetColumns } from "src/api/develop/develop-detail";
import PathInput from "src/sections/common/Values/PathInput";
import { datasetGridHref } from "src/sections/common/Analysis/analysisLinks";
import RunCardShell from "./RunCardShell";
import { useExperimentForm } from "./ExperimentFormContext";
import { DEFAULT_TRACES } from "./experimentFormValues";
import { InfoTip } from "./RunFromCodeParts";
import {
  DEFAULT_WINDOW,
  FILTER_OPS,
  MATCH_BY_OPTIONS,
  ROOT_OUTPUT_PATH,
  WINDOW_OPTIONS,
  filterLabel,
  matchLine,
  matchRequest,
  tracesIssues,
} from "./linkedTracesModel";

const FIELD_W = 220;
const Label = ({ children, tip }) => (
  <Stack
    direction="row"
    alignItems="center"
    gap={0.5}
    sx={{ width: 176, flexShrink: 0 }}
  >
    <Typography typography="s2" color="text.secondary">
      {children}
    </Typography>
    {tip ? <InfoTip title={tip} /> : null}
  </Stack>
);
Label.propTypes = { children: PropTypes.node, tip: PropTypes.string };

const Row = ({ label, tip, children }) => (
  <Stack direction="row" alignItems="center" gap={1} sx={{ minWidth: 0 }}>
    <Label tip={tip}>{label}</Label>
    <Stack
      direction="row"
      alignItems="center"
      gap={1}
      flexWrap="wrap"
      sx={{ minWidth: 0, flex: 1 }}
    >
      {children}
    </Stack>
  </Stack>
);
Row.propTypes = {
  label: PropTypes.string,
  tip: PropTypes.string,
  children: PropTypes.node,
};

const Pick = ({ value, options, onChange, label, width = FIELD_W }) => (
  <Box sx={{ width, flexShrink: 0 }}>
    <FormSearchSelectFieldState
      size="small"
      fullWidth
      label={label}
      value={value ?? ""}
      options={options}
      showClear={false}
      onChange={(e) => onChange(e?.target?.value ?? e)}
      sx={{ bgcolor: "background.paper" }}
    />
  </Box>
);
Pick.propTypes = {
  value: PropTypes.any,
  options: PropTypes.array.isRequired,
  onChange: PropTypes.func.isRequired,
  label: PropTypes.string,
  width: PropTypes.number,
};

/**
 * Traces I already have (R2-SPEC §5.3.9): turn traces a script already sent
 * with traceAI into a run, without running anything again. Writes
 * `${fieldPrefix}.traces` (r2Shapes, manifest row 42c) and
 * `${fieldPrefix}._matchedRows` (the live match count; not sent).
 *
 * Props (frozen, R2 manifest row 20; same as ExternalRunCard).
 */
const LinkedTracesCard = ({
  control,
  setValue,
  fieldPrefix,
  letters = [],
  onRemove,
  showErrors = false,
  error,
  trials = 1,
}) => {
  const { datasetId } = useExperimentForm();
  const traces =
    useWatch({ control, name: `${fieldPrefix}.traces` }) ?? DEFAULT_TRACES;
  const set = (key, value) =>
    setValue?.(`${fieldPrefix}.traces.${key}`, value, { shouldDirty: true });
  const [pathMode, setPathMode] = useState(
    traces.output_path && traces.output_path !== ROOT_OUTPUT_PATH
      ? "path"
      : "root",
  );

  // Projects you can read: Observe projects + experiment-type projects.
  const { data: observe = [] } = useQuery({
    queryKey: ["tracer", "project-ids"],
    queryFn: () => axios.get(endpoints.project.listProjects()),
    select: (res) => res.data?.result?.projects ?? [],
    staleTime: 60 * 1000,
  });
  const { data: linked } = useQuery({
    queryKey: ["tracer", "project-version-runs", "experiment"],
    queryFn: () => axios.get(endpoints.project.projectExperimentRun()),
    select: (res) => res.data?.result,
    staleTime: 60 * 1000,
  });
  const projects = useMemo(
    () => [
      ...(linked?.project
        ? [{ ...linked.project, type: "experiment", versions: linked.runs }]
        : []),
      ...observe.map((p) => ({ ...p, type: "observe" })),
    ],
    [observe, linked],
  );
  const project = projects.find((p) => p.id === traces.project_id) ?? null;
  const isExperiment = project?.type === "experiment";
  const versions = project?.versions ?? [];

  // An experiment project starts on its newest version with traces.
  useEffect(() => {
    if (!isExperiment || traces.project_version_id) return;
    const v =
      [...versions].reverse().find((x) => x.traces > 0) ?? versions.at(-1);
    if (v) set("project_version_id", v.name);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [isExperiment, versions.length, traces.project_version_id]);
  const seededTrials = useRef(false);
  useEffect(() => {
    if (seededTrials.current) return;
    seededTrials.current = true;
    // Up to 3 matching traces per row (newest first) unless the experiment
    // runs more trials.
    if ((traces.max_trials ?? 1) === DEFAULT_TRACES.max_trials)
      set("max_trials", Math.max(3, trials));
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  const { data: columns = [] } = useGetDatasetColumns(datasetId, {
    enabled: Boolean(datasetId),
  });
  const columnOptions = columns.map((c) => ({ label: c.name, value: c.name }));
  const by = traces.match?.by ?? "metadata";
  const setMatch = (patch) =>
    set("match", { ...(traces.match ?? {}), ...patch });
  const defaultColumn = (kind) =>
    (kind === "session"
      ? columns.find((c) => /session/i.test(c.name))
      : columns.find((c) => c.name === "instruction")) ??
    columns.find((c) => c.data_type === "text") ??
    columns[0];

  // Live match line (IM1 match_rows), debounced.
  const preview = useImportPreview();
  const [match, setMatchResult] = useState(null);
  const request = matchRequest({ datasetId, traces, project });
  const requestKey = JSON.stringify(request);
  useEffect(() => {
    if (!request) {
      setMatchResult(null);
      return undefined;
    }
    const t = setTimeout(() => {
      preview.mutate(request, {
        onSuccess: (result) => {
          setMatchResult(result?.summary ?? null);
          setValue?.(
            `${fieldPrefix}._matchedRows`,
            result?.summary?.matched ?? 0,
          );
          // The run reads the same count (Review: rows the run will have).
          set("matched_count", result?.summary?.matched ?? 0);
        },
        onError: () => setMatchResult(null),
      });
    }, 250);
    return () => clearTimeout(t);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [requestKey]);
  const line = matchLine(match);
  const filters = Array.isArray(traces.filters) ? traces.filters : [];
  const [draftFilter, setDraftFilter] = useState(null);

  return (
    <RunCardShell
      icon={
        <Iconify
          icon="mdi:transit-connection-variant"
          width={16}
          sx={{ color: "text.secondary" }}
        />
      }
      letters={letters}
      title="Traces I already have"
      typeLabel="Nothing runs again"
      onRemove={onRemove}
      error={showErrors ? error : null}
    >
      <Stack spacing={1}>
        <Row label="Project">
          <Pick
            value={traces.project_id}
            label="Project"
            options={projects.map((p) => ({
              label: p.name,
              value: p.id,
            }))}
            onChange={(v) => {
              set("project_id", v);
              set("project_version_id", null);
            }}
            width={300}
          />
        </Row>
        {project ? (
          <Row label="Which traces">
            {isExperiment ? (
              <Pick
                value={traces.project_version_id}
                label="Version"
                options={versions.map((v) => ({
                  label: `${v.name} (${v.traces.toLocaleString("en-US")} ${v.traces === 1 ? "trace" : "traces"})`,
                  value: v.name,
                }))}
                onChange={(v) => set("project_version_id", v)}
                width={300}
              />
            ) : (
              <Stack spacing={0.75} sx={{ flex: 1 }}>
                <Stack direction="row" spacing={1} alignItems="center">
                  <Pick
                    value={traces.window ?? DEFAULT_WINDOW}
                    label="Window"
                    options={WINDOW_OPTIONS}
                    onChange={(v) => set("window", v)}
                    width={160}
                  />
                  <Button
                    size="small"
                    data-testid="linked-add-filter"
                    startIcon={<Iconify icon="mdi:plus" width={14} />}
                    sx={{ typography: "s2" }}
                    onClick={() =>
                      setDraftFilter({ key: "", op: "=", value: "" })
                    }
                    disabled={Boolean(draftFilter)}
                  >
                    Filter
                  </Button>
                </Stack>
                {filters.length ? (
                  <Stack
                    direction="row"
                    spacing={0.5}
                    flexWrap="wrap"
                    useFlexGap
                  >
                    {filters.map((f, i) => (
                      <Chip
                        key={`${filterLabel(f)}-${i}`}
                        size="small"
                        label={filterLabel(f)}
                        data-testid="linked-filter-chip"
                        onDelete={() =>
                          set(
                            "filters",
                            filters.filter((_, j) => j !== i),
                          )
                        }
                      />
                    ))}
                  </Stack>
                ) : null}
                {draftFilter ? (
                  <Stack
                    direction="row"
                    spacing={1}
                    alignItems="center"
                    data-testid="linked-filter-form"
                  >
                    <Pick
                      value={draftFilter.key || null}
                      label="Attribute"
                      options={(match?.attributes ?? []).map((k) => ({
                        label: k,
                        value: k,
                      }))}
                      onChange={(v) =>
                        setDraftFilter((d) => ({ ...d, key: v }))
                      }
                      width={170}
                    />
                    <Pick
                      value={draftFilter.op}
                      label="Operator"
                      options={FILTER_OPS.map((o) => ({ label: o, value: o }))}
                      onChange={(v) => setDraftFilter((d) => ({ ...d, op: v }))}
                      width={110}
                    />
                    <TextField
                      size="small"
                      label="Value"
                      value={draftFilter.value}
                      inputProps={{ "data-testid": "linked-filter-value" }}
                      onChange={(e) =>
                        setDraftFilter((d) => ({ ...d, value: e.target.value }))
                      }
                      sx={{ width: 150 }}
                    />
                    <Button
                      size="small"
                      variant="contained"
                      disabled={!draftFilter.key}
                      onClick={() => {
                        set("filters", [...filters, draftFilter]);
                        setDraftFilter(null);
                      }}
                    >
                      Add
                    </Button>
                    <Button size="small" onClick={() => setDraftFilter(null)}>
                      Cancel
                    </Button>
                  </Stack>
                ) : null}
              </Stack>
            )}
          </Row>
        ) : null}
        <Row
          label="Match a trace to a row by"
          tip="Each trace becomes the result of the row it matches."
        >
          <Pick
            value={by}
            label="Match by"
            options={MATCH_BY_OPTIONS}
            onChange={(v) =>
              setMatch(
                v === "metadata"
                  ? { by: v, key: "row_id", column: undefined }
                  : {
                      by: v,
                      key: undefined,
                      column: defaultColumn(v)?.name ?? null,
                    },
              )
            }
          />
          {by === "metadata" ? (
            <Pick
              value={traces.match?.key ?? "row_id"}
              label="Key"
              options={[
                { label: "row_id", value: "row_id" },
                { label: "fi.row_id", value: "fi.row_id" },
              ]}
              onChange={(v) => setMatch({ key: v })}
              width={160}
            />
          ) : (
            <Pick
              value={traces.match?.column ?? ""}
              label="Column"
              options={columnOptions}
              onChange={(v) => setMatch({ column: v })}
              width={180}
            />
          )}
        </Row>
        <Row label="Output is">
          <Pick
            value={pathMode}
            label="Output"
            options={[
              { label: "Root span output", value: "root" },
              { label: "Path", value: "path" },
            ]}
            onChange={(v) => {
              setPathMode(v);
              set("output_path", v === "root" ? ROOT_OUTPUT_PATH : "");
            }}
            width={180}
          />
          {pathMode === "path" ? (
            <Box sx={{ width: 260 }}>
              <PathInput
                value={
                  traces.output_path === ROOT_OUTPUT_PATH
                    ? ""
                    : traces.output_path ?? ""
                }
                onChange={(v) => set("output_path", v)}
                placeholder="e.g. output.messages.0.content"
                size="small"
              />
            </Box>
          ) : null}
        </Row>
        <Row label="Trials">
          <Typography typography="s2">Use up to</Typography>
          <Pick
            value={traces.max_trials ?? 1}
            label="Traces"
            options={[1, 2, 3, 4, 5].map((n) => ({
              label: String(n),
              value: n,
            }))}
            onChange={(v) => set("max_trials", Number(v))}
            width={72}
          />
          <Typography typography="s2">
            matching traces per row (newest first)
          </Typography>
        </Row>
        {project ? (
          <Stack
            direction="row"
            alignItems="center"
            gap={1}
            data-testid="linked-match-line"
            sx={{
              px: 1.25,
              py: 0.75,
              borderRadius: 0.5,
              bgcolor: "background.neutral",
              minHeight: 32,
            }}
          >
            {preview.isPending ? (
              <CircularProgress size={12} thickness={5} />
            ) : (
              <Iconify
                icon={
                  line?.matched
                    ? "mdi:check-circle-outline"
                    : "mdi:alert-circle-outline"
                }
                width={16}
                sx={{ color: line?.matched ? "green.600" : "text.disabled" }}
              />
            )}
            {line ? (
              <Typography typography="s2" sx={{ minWidth: 0 }}>
                {line.head}
                {line.unmatched.length ? (
                  <>
                    {" · "}
                    {line.unmatchedHead}
                    {" ("}
                    {line.unmatched.map((u, i) => (
                      <React.Fragment key={u.id}>
                        {i ? ", " : ""}
                        <CustomTooltip
                          show
                          title="Open in Row view ↗"
                          size="small"
                          placement="top"
                          arrow
                        >
                          <Link
                            href={datasetGridHref(datasetId, null, {
                              row: u.id,
                            })}
                            target="_blank"
                            rel="noopener"
                            underline="hover"
                          >
                            {u.label}
                          </Link>
                        </CustomTooltip>
                      </React.Fragment>
                    ))}
                    {line.more ? `, +${line.more}` : ""}
                    {")"}
                  </>
                ) : null}
                {" · "}
                {line.tail}
              </Typography>
            ) : (
              <Typography typography="s2" color="text.secondary">
                Matching…
              </Typography>
            )}
          </Stack>
        ) : null}
      </Stack>
    </RunCardShell>
  );
};

LinkedTracesCard.propTypes = {
  index: PropTypes.number,
  control: PropTypes.object,
  setValue: PropTypes.func,
  fieldPrefix: PropTypes.string,
  onRemove: PropTypes.func,
  letters: PropTypes.arrayOf(PropTypes.string),
  showErrors: PropTypes.bool,
  error: PropTypes.string,
  trials: PropTypes.number,
};

/** Stepper issues (stepIssues.js): a project, and at least one matched row. */
export const issues = (item) => tracesIssues(item);

export default LinkedTracesCard;
