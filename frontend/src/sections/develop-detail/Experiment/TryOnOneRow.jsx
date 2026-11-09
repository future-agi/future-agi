import React, { useEffect, useMemo, useRef, useState } from "react";
import PropTypes from "prop-types";
import { isSurfaceShown } from "src/launch/launchSwitch";
import { tryStopLine } from "src/sections/common/RunSettings/tryStopLine";
import { withRunScope } from "src/sections/common/RunSettings/tryScope";
import {
  Box,
  Button,
  ButtonGroup,
  CircularProgress,
  Collapse,
  IconButton,
  InputAdornment,
  MenuItem,
  MenuList,
  Popover,
  Stack,
  TextField,
  Typography,
} from "@mui/material";
import { alpha } from "@mui/material/styles";
import Iconify from "src/components/iconify";
import CustomTooltip from "src/components/tooltip";
import { useTryOnOneRow } from "src/api/develop/r2";
import { useDatasetKind } from "src/sections/common/Datasets/datasetKind";
import TraceChip from "src/sections/common/Trace/TraceChip";
import { cellTraceChipProps } from "src/sections/common/Trace/traceChipModel";
import { traceChipMenu } from "src/sections/common/Trace/traceChipMenu";
import TrajectoryPair from "src/sections/common/Trace/TrajectoryPair";
import { kindLabel } from "src/sections/common/Runs/runKind";
import TraceDetailDrawerV2 from "src/components/traceDetail/TraceDetailDrawerV2";
import { useExperimentForm } from "./ExperimentFormContext";
import {
  canTryKind,
  clampLines,
  evalGlyph,
  evalShortName,
  filterRows,
  formatUsd,
  formatWall,
  rowLabel,
  rowTitle,
  runningLabel,
  tryErrorText,
  unbackedClaim,
  unmappedWarning,
  visibleEvals,
} from "./tryOnOneRowModel";

const NEEDS = {
  prompt: "Pick a version and a model first",
  agent: "Pick a saved version first",
  harness: "Fill in the harness first",
};

/** Row picker: search by number or title; Esc closes. */
const RowPicker = ({
  anchorEl,
  rows,
  columns,
  datasetKind,
  onPick,
  onClose,
}) => {
  const [q, setQ] = useState("");
  const list = filterRows(rows, q, { columns, datasetKind }).slice(0, 50);
  return (
    <Popover
      open={Boolean(anchorEl)}
      anchorEl={anchorEl}
      onClose={() => {
        setQ("");
        onClose();
      }}
      anchorOrigin={{ vertical: "bottom", horizontal: "left" }}
      slotProps={{ paper: { sx: { width: 360, mt: 0.5 } } }}
    >
      <Box sx={{ p: 1, pb: 0.5 }}>
        <TextField
          fullWidth
          autoFocus
          size="small"
          placeholder="Search by row number or title"
          value={q}
          onChange={(e) => setQ(e.target.value)}
          onKeyDown={(e) => {
            if (e.key === "Enter" && list[0]) {
              e.preventDefault();
              onPick(list[0]);
              setQ("");
            }
          }}
          InputProps={{
            startAdornment: (
              <InputAdornment position="start">
                <Iconify
                  icon="eva:search-fill"
                  width={16}
                  sx={{ color: "text.disabled" }}
                />
              </InputAdornment>
            ),
          }}
          sx={{ "& .MuiOutlinedInput-root": { fontSize: 13, height: 30 } }}
        />
      </Box>
      <MenuList dense sx={{ maxHeight: 280, overflowY: "auto", py: 0.5 }}>
        {list.length ? (
          list.map((r) => (
            <MenuItem
              key={r.row_id}
              onClick={() => {
                onPick(r);
                setQ("");
              }}
              sx={{ gap: 1, py: 0.5 }}
            >
              <Typography
                typography="s2"
                fontWeight="fontWeightMedium"
                sx={{ minWidth: 44, fontVariantNumeric: "tabular-nums" }}
              >
                {rowLabel(r, { datasetKind })}
              </Typography>
              <Typography typography="s2" color="text.secondary" noWrap>
                {rowTitle(r, columns) || "Untitled row"}
              </Typography>
            </MenuItem>
          ))
        ) : (
          <Typography
            typography="s2"
            color="text.secondary"
            sx={{ px: 1.5, py: 1 }}
          >
            No rows match
          </Typography>
        )}
      </MenuList>
    </Popover>
  );
};
RowPicker.propTypes = {
  anchorEl: PropTypes.any,
  rows: PropTypes.array,
  columns: PropTypes.array,
  datasetKind: PropTypes.string,
  onPick: PropTypes.func.isRequired,
  onClose: PropTypes.func.isRequired,
};

/** The reply, first 4 lines with `Show all`. */
const Reply = ({ text }) => {
  const [all, setAll] = useState(false);
  const { head, more } = clampLines(text, 4);
  return (
    <Box>
      <Typography
        typography="s2"
        sx={{
          whiteSpace: "pre-wrap",
          wordBreak: "break-word",
          ...(!all && {
            display: "-webkit-box",
            WebkitLineClamp: 4,
            WebkitBoxOrient: "vertical",
            overflow: "hidden",
          }),
        }}
      >
        {all ? text : head || "Empty output"}
      </Typography>
      {more || String(text ?? "").length > 320 ? (
        <Button
          size="small"
          onClick={() => setAll((v) => !v)}
          sx={{ typography: "s3", px: 0.5, minWidth: 0 }}
        >
          {all ? "Show less" : "Show all"}
        </Button>
      ) : null}
    </Box>
  );
};
Reply.propTypes = { text: PropTypes.string };

/**
 * Try on one row (R2-SPEC §5.3.2): run the card's config once on one row
 * (I1 parentless quick test in the dataset's scratch experiment) and show
 * the output, the trajectory, the evals and the trace inside the card.
 * Nothing is listed; the panel says `not saved`.
 *
 * Props (frozen, R2 manifest row 18):
 * @param {string} props.datasetId
 * @param {object} props.config  the card's run config (r2Shapes.js RUN_CONFIG)
 * @param {string} props.kind  run kind (runKind.js RUN_KINDS)
 * @param {string} [props.rowId]  default: the first row in view
 * @param {boolean} [props.compact]
 * @param {boolean} [props.hasEvals]  additive: the Evaluations step has evals
 * @param {string[]} [props.unmapped]  additive (IT-R1 G1 P10): variables with
 *   no column; the button warns before a try sends empty text
 * @param {Function} [props.onMapNow]  additive: opens the Data step
 * @param {{reason: string, action?: string, onAction?: Function,
 *   href?: string}} [props.blocked]  additive (IT-R2 G1 P10): the real
 *   blocker and the button that unblocks it (a draft prompt, no model,
 *   unmapped variables)
 */
const TryOnOneRow = ({
  datasetId,
  config,
  kind,
  rowId,
  compact = false,
  hasEvals = false,
  unmapped = [],
  onMapNow,
  blocked = null,
}) => {
  const form = useExperimentForm();
  const { kind: datasetKind, dataset } = useDatasetKind(datasetId);
  const scratchId = dataset?.scratch_experiment_id ?? null;
  const rows = useMemo(
    () =>
      [...(form.rows ?? [])].sort(
        (a, b) => Number(a?.order ?? 0) - Number(b?.order ?? 0),
      ),
    [form.rows],
  );
  const [pickedId, setPickedId] = useState(rowId ?? null);
  // IT-R2 G1 (S8): the Data step's Sample row, never a different default.
  const row =
    rows.find((r) => r.row_id === pickedId) ??
    rows.find((r) => r.row_id === rowId) ??
    rows.find((r) => r.row_id === form.sampleRow?.row_id) ??
    rows[0] ??
    null;
  const [pickerAnchor, setPickerAnchor] = useState(null);
  const [state, setState] = useState({ status: "idle" });
  // L5 G1: the result belongs to the card config it ran with (version,
  // model, params, tools, mapping); any change marks it stale.
  const sent = useMemo(() => withRunScope(config, form), [config, form]);
  const configKey = useMemo(() => JSON.stringify(sent ?? null), [sent]);
  const [open, setOpen] = useState(true);
  const [elapsed, setElapsed] = useState(0);
  const [fullTrace, setFullTrace] = useState(false);
  const [softShown, setSoftShown] = useState(false);
  const [runAfterFix, setRunAfterFix] = useState(false);
  const startRef = useRef(null);
  const ticket = useRef(0);
  const footerRef = useRef(null);
  const resultRef = useRef(null);
  const mutation = useTryOnOneRow();
  // IT-R1 G1 P10: the result shows where the user looks (it lands below
  // the fold of the drawer otherwise).
  useEffect(() => {
    if (state.status !== "done" && state.status !== "error") return;
    resultRef.current?.scrollIntoView?.({
      block: "nearest",
      behavior: "smooth",
    });
  }, [state.status, state.ticket, state.attempt]);

  useEffect(() => {
    if (state.status !== "running") return undefined;
    const started = Date.now();
    const t = setInterval(() => setElapsed(Date.now() - started), 250);
    return () => clearInterval(t);
  }, [state.status, state.ticket]);

  // IT-R5 G1 S1: `Use <col>` maps the variable, then the try runs.
  useEffect(() => {
    if (!runAfterFix || blocked?.reason) return;
    setRunAfterFix(false);
    startRef.current?.();
  }, [runAfterFix, blocked?.reason]);

  if (!canTryKind(kind)) return null;

  const label = row ? rowLabel(row, { datasetKind }) : null;
  // IT-R4 G1 P1 (rule 31): a soft block (unmapped variables) keeps the
  // button live; a click shows `Map customer_email first [Go to Data]`.
  const softBlock = Boolean(blocked?.reason && blocked?.soft);
  const disabledReason =
    blocked?.reason && !softBlock
      ? blocked.reason
      : !config
        ? NEEDS[kind]
        : !scratchId
          ? "Not available on this dataset"
          : !row
            ? isSurfaceShown("dataset.versions")
              ? "This dataset version has no rows"
              : "This dataset has no rows"
            : null;

  const start = (target = row) => {
    if (softBlock) {
      setSoftShown(true);
      setTimeout(
        () =>
          footerRef.current?.parentElement
            ?.querySelector('[data-testid="try-blocked"]')
            ?.scrollIntoView?.({ block: "nearest", behavior: "smooth" }),
        50,
      );
      return;
    }
    if (!target || !config || !scratchId) return;
    ticket.current += 1;
    const mine = ticket.current;
    const runKey = configKey;
    setElapsed(0);
    setOpen(true);
    setState({ status: "running", row: target, ticket: mine, key: configKey });
    mutation.mutate(
      {
        experimentId: scratchId,
        datasetId,
        config: sent,
        rowId: target.row_id,
        trials: 1,
      },
      {
        onSuccess: (res) => {
          if (ticket.current !== mine) return;
          const attempt = res?.attempt ?? res?.attempts?.[0] ?? null;
          setState(
            attempt
              ? { status: "done", row: target, attempt, key: runKey }
              : {
                  status: "error",
                  row: target,
                  error: "Couldn't run this row: no result came back.",
                },
          );
        },
        onError: (e) => {
          if (ticket.current !== mine) return;
          setState({ status: "error", row: target, error: tryErrorText(e) });
        },
      },
    );
  };
  const cancel = () => {
    ticket.current += 1;
    setState({ status: "idle" });
  };

  const pick = (r) => {
    setPickerAnchor(null);
    setPickedId(r.row_id);
    if (state.status !== "idle") start(r);
  };

  const attempt = state.attempt;
  // The attempt carries one trace_summary (trial 1); the chip model reads
  // the per-trial list of a result.
  const chip = attempt
    ? cellTraceChipProps(
        {
          trace_summary: attempt.trace_summary ? [attempt.trace_summary] : [],
        },
        1,
        { sourceKind: kind },
      )
    : null;
  const shownEvals = attempt
    ? visibleEvals(attempt.evals, { hasEvals: hasEvals || kind !== "prompt" })
    : [];
  const kindWord = kindLabel(kind, { tools: config?.tools });
  const stop = tryStopLine(attempt);
  const resultRowLabel = state.row
    ? rowLabel(state.row, { datasetKind })
    : label;
  const claim = attempt
    ? unbackedClaim(attempt, {
        hasTools: kind !== "prompt" || (config?.tools ?? []).length > 0,
      })
    : null;
  startRef.current = start;
  const unmappedNote = unmappedWarning(unmapped, {});

  return (
    <Box data-testid="try-on-one-row">
      <Stack direction="row" alignItems="center" gap={1} ref={footerRef}>
        <CustomTooltip
          show={state.status !== "running"}
          title={
            disabledReason ??
            (softBlock ? blocked.reason : null) ??
            `Try this card on ${label ?? "one row"} now. Nothing is saved or listed.`
          }
          placement="top"
          size="small"
          type="black"
        >
          <span>
            <ButtonGroup
              size="small"
              variant="outlined"
              disabled={Boolean(disabledReason) || state.status === "running"}
              sx={{ "& .MuiButton-root": { typography: "s2", height: 28 } }}
            >
              <Button
                startIcon={<Iconify icon="mdi:play-outline" width={16} />}
                onClick={() => start()}
                sx={{ ...(compact && { px: 1 }) }}
              >
                Try on one row
              </Button>
              <Button
                endIcon={<Iconify icon="mdi:chevron-down" width={14} />}
                onClick={(e) => setPickerAnchor(e.currentTarget)}
                aria-label="Pick a row"
                aria-haspopup="dialog"
                sx={{
                  px: 0.75,
                  fontVariantNumeric: "tabular-nums",
                  "& .MuiButton-endIcon": { ml: 0.25 },
                }}
              >
                {label ?? "Row"}
              </Button>
            </ButtonGroup>
          </span>
        </CustomTooltip>
        {softBlock && softShown && state.status !== "running" ? (
          // IT-R5 G1 S1: the unmapped variable sits beside the button:
          // `Map {{ticket}} first: [Use user_request] [Go to Data]`.
          <Stack
            direction="row"
            alignItems="center"
            columnGap={0.75}
            flexWrap="wrap"
            data-testid="try-blocked"
            sx={{ minWidth: 0 }}
          >
            <Typography typography="s2" color="warning.dark">
              {`${blocked.reason}:`}
            </Typography>
            {blocked.fix ? (
              <Button
                size="small"
                variant="outlined"
                onClick={() => {
                  blocked.fix.onFix();
                  setSoftShown(false);
                  setRunAfterFix(true);
                }}
                data-testid="try-fix"
                sx={{ typography: "s2", minWidth: 0, px: 0.75, height: 26 }}
              >
                {blocked.fix.label}
              </Button>
            ) : null}
            {blocked.action ? (
              <Button
                size="small"
                onClick={blocked.onAction}
                sx={{ typography: "s2", minWidth: 0, px: 0.75, height: 26 }}
              >
                {blocked.action}
              </Button>
            ) : null}
          </Stack>
        ) : null}
        {state.status === "running" ? (
          <Stack direction="row" alignItems="center" gap={0.75}>
            <CircularProgress size={12} thickness={5} />
            <Typography typography="s2" color="text.secondary">
              {`${resultRowLabel} · ${runningLabel(kind, elapsed)}`}
            </Typography>
            <Button
              size="small"
              color="inherit"
              onClick={cancel}
              sx={{ typography: "s2", px: 0.75, minWidth: 0 }}
            >
              Cancel
            </Button>
          </Stack>
        ) : null}
      </Stack>

      {blocked?.reason && !softBlock ? (
        // Disabled = the reason + the button that unblocks it (rule 13).
        <Stack
          direction="row"
          alignItems="center"
          gap={0.75}
          data-testid="try-blocked"
          sx={{ mt: 0.75 }}
        >
          <Iconify
            icon="mdi:information-outline"
            width={16}
            sx={{ color: "warning.main", flexShrink: 0 }}
          />
          <Typography typography="s2" color="text.secondary">
            {blocked.reason}
          </Typography>
          {blocked.action ? (
            <Button
              size="small"
              variant="outlined"
              onClick={blocked.onAction}
              href={blocked.href}
              target={blocked.href ? "_blank" : undefined}
              rel={blocked.href ? "noopener noreferrer" : undefined}
              sx={{ typography: "s2", minWidth: 0, px: 0.75, flexShrink: 0 }}
            >
              {blocked.action}
            </Button>
          ) : null}
        </Stack>
      ) : unmappedNote && !softBlock && state.status !== "running" ? (
        <Stack
          direction="row"
          alignItems="center"
          gap={0.75}
          data-testid="try-unmapped"
          sx={{ mt: 0.75 }}
        >
          <Iconify
            icon="mdi:alert-outline"
            width={16}
            sx={{ color: "warning.main", flexShrink: 0 }}
          />
          <Typography typography="s2" color="text.secondary">
            {unmappedNote.text}
          </Typography>
          {onMapNow ? (
            <Button
              size="small"
              onClick={onMapNow}
              sx={{ typography: "s2", minWidth: 0, px: 0.75 }}
            >
              Map now
            </Button>
          ) : null}
        </Stack>
      ) : null}

      <RowPicker
        anchorEl={pickerAnchor}
        rows={rows}
        columns={form.columns ?? []}
        datasetKind={datasetKind}
        onPick={pick}
        onClose={() => setPickerAnchor(null)}
      />

      {state.status === "error" ? (
        <Stack
          ref={resultRef}
          direction="row"
          alignItems="center"
          gap={1}
          sx={{
            mt: 1,
            px: 1.25,
            py: 0.75,
            borderRadius: 0.5,
            border: "1px solid",
            borderColor: "error.main",
            bgcolor: (t) => alpha(t.palette.error.main, 0.12),
          }}
        >
          <Iconify
            icon="mdi:alert-circle-outline"
            width={16}
            color="error.main"
          />
          <Typography typography="s2" sx={{ flex: 1 }}>
            {state.error}
          </Typography>
          <Button size="small" onClick={() => start(state.row)}>
            Retry
          </Button>
        </Stack>
      ) : null}

      {state.status === "done" && attempt ? (
        <Box
          ref={resultRef}
          data-testid="try-result"
          sx={{
            scrollMarginBottom: 16,
            mt: 1,
            border: "1px solid",
            borderColor: "divider",
            borderRadius: 0.5,
            bgcolor: "background.paper",
          }}
        >
          <Stack
            direction="row"
            alignItems="center"
            gap={0.75}
            sx={{ px: 1.25, py: 0.75, flexWrap: "wrap", rowGap: 0.5 }}
          >
            <IconButton
              size="small"
              aria-label={open ? "Collapse result" : "Expand result"}
              onClick={() => setOpen((v) => !v)}
              sx={{ p: 0.25, ml: -0.5 }}
            >
              <Iconify
                icon={open ? "mdi:chevron-down" : "mdi:chevron-right"}
                width={16}
              />
            </IconButton>
            <Typography typography="s2" fontWeight="fontWeightMedium">
              Output
            </Typography>
            {state.key !== configKey ? (
              <Button
                size="small"
                color="warning"
                data-testid="try-stale"
                onClick={() => start(state.row)}
                sx={{ py: 0, minWidth: 0 }}
              >
                Stale · re-run
              </Button>
            ) : null}
            <Typography typography="s2" color="text.disabled">
              ·
            </Typography>
            <TraceChip
              {...chip}
              compact
              onOpen={attempt.trace_id ? () => setFullTrace(true) : undefined}
              menu={traceChipMenu({
                traceId: attempt.trace_id,
                onOpenTrajectory: () => setOpen(true),
              })}
            />
            <Typography
              typography="s2"
              color="text.secondary"
              sx={{ fontVariantNumeric: "tabular-nums" }}
            >
              {[
                formatUsd(attempt.cost_usd),
                formatWall(attempt.wall_ms),
                "not saved",
              ].join(" · ")}
            </Typography>
            <Box sx={{ flex: 1 }} />
            <Typography typography="s3" color="text.disabled" noWrap>
              {`${resultRowLabel} · ${kindWord}`}
            </Typography>
          </Stack>
          <Collapse in={open}>
            <Stack
              spacing={1.25}
              sx={{
                px: 1.25,
                pb: 1.25,
                borderTop: "1px solid",
                borderColor: "divider",
                pt: 1,
              }}
            >
              {claim ? (
                <CustomTooltip
                  show
                  title={claim.detail}
                  placement="top"
                  size="small"
                  type="black"
                >
                  <Box
                    component="span"
                    data-testid="try-unbacked-claim"
                    sx={{
                      alignSelf: "flex-start",
                      display: "inline-flex",
                      alignItems: "center",
                      gap: 0.5,
                      typography: "s2",
                      fontWeight: "fontWeightMedium",
                      px: 0.75,
                      py: 0.25,
                      borderRadius: 0.5,
                      color: "warning.dark",
                      bgcolor: (t) => alpha(t.palette.warning.main, 0.12),
                      cursor: "help",
                    }}
                  >
                    <Iconify icon="mdi:alert-outline" width={14} />
                    {claim.label}
                  </Box>
                </CustomTooltip>
              ) : null}
              {/* IT-R3 G1 P4: the tool calls, as the Playground shows them. */}
              {attempt.tool_calls?.length ? (
                <Stack spacing={0.25} data-testid="try-tool-calls">
                  {attempt.tool_calls.map((c, i) => (
                    <Typography
                      key={`${c.name}-${i}`}
                      typography="s2"
                      color="text.secondary"
                      sx={{ fontFamily: "monospace", fontSize: 12 }}
                    >
                      {`Called ${c.name}${c.args ? `(${JSON.stringify(c.args)})` : ""}${c.mode ? ` · ${c.mode}` : ""}${c.text ? ` → ${c.text}` : ""}`}
                    </Typography>
                  ))}
                </Stack>
              ) : null}
              {attempt.playground_differs ? (
                <Typography
                  typography="s2"
                  color="warning.dark"
                  data-testid="try-differs"
                >
                  {`Differs from Playground: ${attempt.playground_differs}`}
                </Typography>
              ) : null}
              <Reply text={attempt.output} />
              {shownEvals.length ? (
                <Stack direction="row" gap={0.75} flexWrap="wrap" rowGap={0.25}>
                  <Typography typography="s2" color="text.secondary">
                    Evals
                  </Typography>
                  {shownEvals.map((e) => (
                    <CustomTooltip
                      key={e.key}
                      show={Boolean(e.reason)}
                      title={e.reason ?? ""}
                      placement="top"
                      size="small"
                      type="black"
                    >
                      <Typography
                        typography="s2"
                        sx={{
                          color:
                            e.passed === false
                              ? "red.600"
                              : e.passed === true
                                ? "green.600"
                                : "text.disabled",
                          cursor: e.reason ? "help" : "default",
                        }}
                      >
                        {`${evalShortName(e)} ${evalGlyph(e)}`}
                      </Typography>
                    </CustomTooltip>
                  ))}
                </Stack>
              ) : null}
              <TrajectoryPair
                left={{
                  traceId: attempt.trace_id,
                  label: `${resultRowLabel} · Try`,
                }}
                right={null}
              />
              {stop ? (
                <Typography typography="s3" color="text.secondary">
                  {stop}
                </Typography>
              ) : null}
              {attempt.end_state_diff?.length ? (
                <Typography typography="s2" color="text.secondary">
                  {`End state: ${attempt.end_state_diff.length} ${attempt.end_state_diff.length === 1 ? "change" : "changes"}`}
                </Typography>
              ) : null}
              <Stack direction="row" gap={1}>
                <Button
                  size="small"
                  variant="outlined"
                  color="inherit"
                  startIcon={<Iconify icon="mdi:swap-vertical" width={14} />}
                  onClick={() => setPickerAnchor(footerRef.current)}
                  sx={{ typography: "s2" }}
                >
                  Try another row
                </Button>
                <Button
                  size="small"
                  variant="text"
                  disabled={!attempt.trace_id}
                  endIcon={<Iconify icon="mdi:arrow-top-right" width={14} />}
                  onClick={() => setFullTrace(true)}
                  sx={{ typography: "s2" }}
                >
                  Open full trace
                </Button>
              </Stack>
            </Stack>
          </Collapse>
        </Box>
      ) : null}

      {fullTrace && attempt?.trace_id ? (
        <TraceDetailDrawerV2
          traceId={attempt.trace_id}
          open={fullTrace}
          onClose={() => setFullTrace(false)}
        />
      ) : null}
    </Box>
  );
};

TryOnOneRow.propTypes = {
  datasetId: PropTypes.string,
  config: PropTypes.object,
  kind: PropTypes.string,
  rowId: PropTypes.string,
  compact: PropTypes.bool,
  hasEvals: PropTypes.bool,
  unmapped: PropTypes.arrayOf(PropTypes.string),
  blocked: PropTypes.object,
  onMapNow: PropTypes.func,
};

export default TryOnOneRow;
