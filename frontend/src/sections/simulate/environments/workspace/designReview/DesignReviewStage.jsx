import PropTypes from "prop-types";
import { useEffect, useState } from "react";
import { enqueueSnackbar } from "notistack";
import {
  Box,
  Button,
  ButtonBase,
  Stack,
  TextField,
  Tooltip,
  Typography,
} from "@mui/material";
import Iconify from "src/components/iconify";
import { ConfirmDialog } from "src/components/custom-dialog";
import ContentSkeleton from "src/components/content-skeleton";
import ErrorState from "src/components/error-state";
import { errorMessage } from "src/pages/dashboard/harness/harnessShared";
import {
  getDesignImpact,
  useAddDesignEdit,
  useApproveDesign,
  useDesignReview,
  useUndoDesignEdit,
} from "src/api/simulate-environments/designReview";
import ViewerLock from "../../components/ViewerLock";
import useSimulationAccess from "../../hooks/useSimulationAccess";
import ReviewSection from "./ReviewSection";
import ReviewLine, { LineAction } from "./ReviewLine";
import ReviewBrief, { ReviewBanner } from "./ReviewBrief";
import ToolChip from "./ToolChip";
import {
  PARK_AFTER_MS,
  REVIEW_COPY,
  REVIEW_STATUS,
  sourceWords,
} from "./designReview.constants";
import {
  commonSource,
  isParked,
  tableUsage,
  plannedScenarios,
  sourceLabel,
} from "./designReview.utils";

const showError = (error) =>
  enqueueSnackbar(errorMessage(error), { variant: "error" });

const actionSx = {
  height: 24,
  px: 1,
  minWidth: 0,
  typography: "s3",
  color: "text.primary",
  borderColor: "divider",
  flexShrink: 0,
};

const linkSx = {
  typography: "s2",
  fontWeight: 600,
  textDecoration: "underline",
  color: "text.secondary",
};

function useMinuteClock() {
  const [now, setNow] = useState(() => Date.now());
  useEffect(() => {
    const timer = setInterval(() => setNow(Date.now()), 30 * 1000);
    return () => clearInterval(timer);
  }, []);
  return now;
}

function RuleEditor({ rule, busy, onSave, onCancel }) {
  const [text, setText] = useState(rule.text);
  const trimmed = text.trim();
  const save = () => {
    if (trimmed && trimmed !== rule.text) onSave(trimmed);
  };
  return (
    <Stack
      direction="row"
      alignItems="center"
      spacing={1}
      sx={{ px: 1.5, py: 0.75 }}
    >
      <TextField
        size="small"
        fullWidth
        autoFocus
        value={text}
        onChange={(event) => setText(event.target.value)}
        onKeyDown={(event) => {
          if (event.key === "Enter") save();
          if (event.key === "Escape") onCancel();
        }}
        inputProps={{ "aria-label": REVIEW_COPY.ruleLabel, maxLength: 2000 }}
        sx={{ "& .MuiInputBase-input": { typography: "s2", py: 0.5 } }}
      />
      <Button
        size="small"
        variant="outlined"
        disabled={!trimmed || trimmed === rule.text || busy}
        onClick={save}
        sx={{ color: "text.primary", borderColor: "divider", flexShrink: 0 }}
      >
        {REVIEW_COPY.save}
      </Button>
      <ButtonBase onClick={onCancel} sx={{ ...linkSx, flexShrink: 0 }}>
        {REVIEW_COPY.cancel}
      </ButtonBase>
    </Stack>
  );
}

RuleEditor.propTypes = {
  rule: PropTypes.shape({ text: PropTypes.string.isRequired }).isRequired,
  busy: PropTypes.bool,
  onSave: PropTypes.func.isRequired,
  onCancel: PropTypes.func.isRequired,
};

export default function DesignReviewStage({ envId }) {
  const { canEdit } = useSimulationAccess();
  const { data: review, isLoading, isError, refetch } = useDesignReview(envId);
  const addEdit = useAddDesignEdit(envId);
  const undoEdit = useUndoDesignEdit(envId);
  const approve = useApproveDesign(envId);
  const now = useMinuteClock();
  const [editingRule, setEditingRule] = useState(null);
  const [undo, setUndo] = useState(null);
  const [continuedAt, setContinuedAt] = useState(null);
  const [checking, setChecking] = useState(false);
  const [confirmRemove, setConfirmRemove] = useState(null);

  if (isLoading)
    return <ContentSkeleton variant="page" sx={{ px: 3, py: 2.5 }} />;
  if (isError || !review) {
    return (
      <ErrorState
        title={REVIEW_COPY.loadError}
        onRetry={() => refetch()}
        sx={{ px: 3, py: 2.5 }}
      />
    );
  }

  const waiting = review.status === REVIEW_STATUS.AWAITING;
  const busy =
    addEdit.isPending || undoEdit.isPending || approve.isPending || checking;
  const editable = canEdit && waiting;
  const kept = (items) => (items || []).filter((item) => !item.removed);
  const tables = kept(review.tables);
  const tools = kept(review.tools);
  const rules = kept(review.rules);
  const useCases = kept(review.use_cases);
  const actors = review.actors || [];
  const pending = kept(review.unsupported);
  const leftOut = (review.unsupported || []).filter((one) => one.removed);
  const pendingNames = new Set(pending.map((one) => one.tool));
  const mapTools = tools.filter((tool) => !pendingNames.has(tool.name));
  const scenarioTotal = plannedScenarios(useCases);
  const emptyWorld = !tables.length && !tools.length;
  const parked =
    waiting && isParked(review.created_at, now, continuedAt, PARK_AFTER_MS);

  const toolSource = (tool) => sourceLabel(tool.source);
  const tableSource = (table) => sourceLabel(table.source);
  const toolCommon = commonSource(mapTools.map(toolSource));
  const tableCommon = commonSource(tables.map(tableSource));
  const ruleCommon = commonSource(
    rules
      .filter((rule) => !rule.edited)
      .map((rule) => sourceLabel(rule.source)),
  );

  const commitRemove = ({ kind, item, label, note }) =>
    addEdit.mutate(
      { op: "remove", kind, id: item.id },
      {
        onSuccess: (next) =>
          setUndo({
            label,
            note,
            index: next.edits[next.edits.length - 1].index,
          }),
        onError: showError,
      },
    );

  const remove = async (kind, item, label) => {
    const note =
      kind === "tool" && item.source === "agent_code"
        ? REVIEW_COPY.agentStillCalls
        : null;
    if (kind !== "tool" && kind !== "table") {
      commitRemove({ kind, item, label, note });
      return;
    }
    setChecking(true);
    try {
      const impact = await getDesignImpact(envId, kind, item.id);
      if (impact.tools.length || impact.rules.length)
        setConfirmRemove({ kind, item, label, note, impact });
      else commitRemove({ kind, item, label, note });
    } catch (error) {
      showError(error);
    } finally {
      setChecking(false);
    }
  };

  const onUndo = () => {
    const { index } = undo;
    setUndo(null);
    undoEdit.mutate(index, { onError: showError });
  };

  const saveRule = (rule, text) =>
    addEdit.mutate(
      { op: "edit", kind: "rule", id: rule.id, text },
      { onSuccess: () => setEditingRule(null), onError: showError },
    );

  const removeTool = (tool) => remove("tool", tool, tool.name);
  const onApprove = () => approve.mutate(undefined, { onError: showError });
  const onContinue = () => setContinuedAt(Date.now());

  const blocker = !waiting
    ? null
    : parked
      ? {
          text: REVIEW_COPY.parked(
            Math.max(Date.parse(review.created_at) || 0, continuedAt || 0),
          ),
          cta: REVIEW_COPY.continue,
          onClick: onContinue,
        }
      : review.agent_changed
        ? { text: REVIEW_COPY.agentChanged }
        : emptyWorld
          ? { text: REVIEW_COPY.emptyWorld }
          : null;

  const approveTip = review.agent_changed
    ? REVIEW_COPY.agentChanged
    : emptyWorld
      ? REVIEW_COPY.emptyWorldTip
      : "";

  const approveControl = parked ? (
    <Button
      size="small"
      variant="outlined"
      onClick={onContinue}
      sx={{ color: "text.primary", borderColor: "divider" }}
    >
      {REVIEW_COPY.continue}
    </Button>
  ) : (
    waiting && (
      <ViewerLock locked={!canEdit} title={REVIEW_COPY.viewerLocked}>
        <Tooltip arrow title={approveTip}>
          <span>
            <Button
              size="small"
              variant="contained"
              color="primary"
              disabled={
                !canEdit || busy || emptyWorld || Boolean(review.agent_changed)
              }
              onClick={onApprove}
              startIcon={
                <Iconify icon="solar:check-circle-linear" width={14} />
              }
            >
              {REVIEW_COPY.approve}
            </Button>
          </span>
        </Tooltip>
      </ViewerLock>
    )
  );

  const chip = (tool) => (
    <ToolChip
      key={tool.id}
      tool={tool}
      source={toolSource(tool) !== toolCommon ? toolSource(tool) : null}
      onRemove={editable && !busy ? () => removeTool(tool) : undefined}
    />
  );

  const usageRow = (label, list) => (
    <Stack direction="row" alignItems="flex-start" spacing={1}>
      <Typography
        sx={{
          typography: "s3",
          color: "text.tertiary",
          width: 56,
          flexShrink: 0,
          pt: 0.375,
        }}
      >
        {label}
      </Typography>
      <Stack
        direction="row"
        flexWrap="wrap"
        sx={{ gap: 0.75, flex: 1, minWidth: 0 }}
      >
        {list.map(chip)}
      </Stack>
    </Stack>
  );

  const leftOutLines = leftOut.map((one) => (
    <ReviewLine key={`left-${one.tool}`}>
      <Box component="span" sx={{ color: "text.secondary" }}>
        {REVIEW_COPY.leftOut(one.tool)}
      </Box>
    </ReviewLine>
  ));

  const recordsAndTools = () => {
    const { byTable, none } = tableUsage(tables, mapTools);
    return (
      <ReviewSection
        label={REVIEW_COPY.recordsAndTools}
        countText={REVIEW_COPY.recordsAndToolsCount(
          tables.length,
          mapTools.length,
        )}
        source={[
          tableCommon && `records ${sourceWords(tableCommon)}`,
          toolCommon && `tools ${sourceWords(toolCommon)}`,
        ]
          .filter(Boolean)
          .join(" · ")}
      >
        {tables.map((table) => (
          <Stack
            key={table.id}
            direction="row"
            alignItems="flex-start"
            spacing={2}
            sx={{ px: 1.5, py: 1 }}
          >
            <Stack
              direction="row"
              alignItems="flex-start"
              spacing={0.5}
              sx={{ width: 240, flexShrink: 0, minWidth: 0 }}
            >
              <Box sx={{ minWidth: 0 }}>
                <Typography noWrap sx={{ typography: "s1", fontWeight: 500 }}>
                  {table.name}
                </Typography>
                <Typography
                  noWrap
                  title={table.columns.join(", ")}
                  sx={{ typography: "s3", color: "text.tertiary" }}
                >
                  {[
                    REVIEW_COPY.columns(table.columns),
                    tableSource(table) !== tableCommon
                      ? tableSource(table)
                      : null,
                  ]
                    .filter(Boolean)
                    .join(" · ")}
                </Typography>
              </Box>
              {editable && (
                <LineAction
                  icon="solar:trash-bin-minimalistic-linear"
                  label={REVIEW_COPY.removeNamed(table.name)}
                  onClick={() => remove("table", table, table.name)}
                  disabled={busy}
                />
              )}
            </Stack>
            <Stack spacing={0.75} sx={{ flex: 1, minWidth: 0, pt: 0.25 }}>
              {(() => {
                const { changes, reads } = byTable.get(table.name);
                if (!changes.length && !reads.length)
                  return (
                    <Typography
                      sx={{ typography: "s3", color: "text.tertiary" }}
                    >
                      {REVIEW_COPY.noToolUsesIt}
                    </Typography>
                  );
                return (
                  <>
                    {changes.length > 0 &&
                      usageRow(REVIEW_COPY.changesLabel, changes)}
                    {reads.length > 0 &&
                      usageRow(REVIEW_COPY.readsLabel, reads)}
                  </>
                );
              })()}
            </Stack>
          </Stack>
        ))}
        {none.length > 0 && (
          <Stack direction="row" spacing={2} sx={{ px: 1.5, py: 1 }}>
            <Typography
              sx={{
                typography: "s1",
                color: "text.secondary",
                width: 240,
                flexShrink: 0,
              }}
            >
              {REVIEW_COPY.noTable}
            </Typography>
            <Stack spacing={0.75} sx={{ flex: 1, minWidth: 0 }}>
              {none.map((tool) => (
                <Stack
                  key={tool.id}
                  direction="row"
                  alignItems="center"
                  spacing={1}
                >
                  {chip(tool)}
                  <Typography sx={{ typography: "s3", color: "text.tertiary" }}>
                    {REVIEW_COPY.noTableWhy[tool.status] ||
                      REVIEW_COPY.noTableWhy.unknown}
                  </Typography>
                </Stack>
              ))}
            </Stack>
          </Stack>
        )}
        {leftOutLines}
      </ReviewSection>
    );
  };

  const separateRecordsAndTools = (
    <>
      <ReviewSection
        label={REVIEW_COPY.records}
        count={tables.length}
        source={tableCommon ? sourceWords(tableCommon) : null}
      >
        {tables.length ? (
          tables.map((table) => (
            <ReviewLine
              key={table.id}
              onRemove={
                editable ? () => remove("table", table, table.name) : undefined
              }
              removeLabel={REVIEW_COPY.removeNamed(table.name)}
              disabled={busy}
            >
              <Box component="span" sx={{ fontWeight: 500 }}>
                {table.name}
              </Box>
              <Box component="span" sx={{ color: "text.secondary", ml: 0.75 }}>
                {REVIEW_COPY.columns(table.columns)}
              </Box>
            </ReviewLine>
          ))
        ) : (
          <ReviewLine empty>{REVIEW_COPY.noRecords}</ReviewLine>
        )}
      </ReviewSection>
      <ReviewSection
        label={REVIEW_COPY.tools}
        count={mapTools.length}
        source={toolCommon ? sourceWords(toolCommon) : null}
      >
        {mapTools.length ? (
          mapTools.map((tool) => (
            <ReviewLine
              key={tool.id}
              onRemove={editable ? () => removeTool(tool) : undefined}
              removeLabel={REVIEW_COPY.removeNamed(tool.name)}
              disabled={busy}
            >
              <Box component="span" sx={{ fontWeight: 500 }}>
                {tool.name}
              </Box>
              {tool.description && (
                <Box
                  component="span"
                  sx={{ color: "text.secondary", ml: 0.75 }}
                >
                  {tool.description}
                </Box>
              )}
            </ReviewLine>
          ))
        ) : (
          <ReviewLine empty>{REVIEW_COPY.noneYet}</ReviewLine>
        )}
        {leftOutLines}
      </ReviewSection>
    </>
  );

  return (
    <Box sx={{ height: "100%", overflow: "auto", px: 3, py: 2.5 }}>
      <Stack direction="row" alignItems="flex-start" spacing={2}>
        <Box flex={1} minWidth={0}>
          <Typography sx={{ typography: "m2", fontWeight: 600 }}>
            {REVIEW_COPY.title}
          </Typography>
          <Typography
            sx={{ typography: "s1", color: "text.tertiary", mt: 0.25 }}
          >
            {waiting
              ? REVIEW_COPY.summary({
                  tables: tables.length,
                  tools: tools.length,
                  rules: rules.length,
                  scenarios: emptyWorld ? 0 : scenarioTotal,
                })
              : REVIEW_COPY.approved}
          </Typography>
        </Box>
        <Box sx={{ flexShrink: 0 }}>{approveControl}</Box>
      </Stack>

      {blocker && <ReviewBanner {...blocker} />}

      <ReviewBrief brief={review.brief} />

      {pending.length > 0 && (
        <Box sx={{ mt: 3 }}>
          <ReviewSection label={REVIEW_COPY.unsupported} count={pending.length}>
            {pending.map((one) => {
              const tool = tools.find((item) => item.name === one.tool);
              return (
                <ReviewLine key={one.tool}>
                  <Stack direction="row" alignItems="center" spacing={1}>
                    <Box sx={{ flex: 1, minWidth: 0 }}>
                      <Box component="span" sx={{ fontWeight: 500 }}>
                        {one.tool}
                      </Box>
                      <Box
                        component="span"
                        sx={{ color: "text.secondary", ml: 0.75 }}
                      >
                        {REVIEW_COPY.unsupportedWhy(one.reaches)}
                      </Box>
                    </Box>
                    {editable && tool && (
                      <Button
                        size="small"
                        variant="outlined"
                        disabled={busy}
                        onClick={() => removeTool(tool)}
                        sx={actionSx}
                      >
                        {REVIEW_COPY.leaveOut}
                      </Button>
                    )}
                  </Stack>
                </ReviewLine>
              );
            })}
          </ReviewSection>
        </Box>
      )}

      <Box sx={{ mt: 2 }}>
        {tables.length && mapTools.length
          ? recordsAndTools()
          : separateRecordsAndTools}

        <ReviewSection
          label={REVIEW_COPY.rules}
          count={rules.length}
          source={ruleCommon ? sourceWords(ruleCommon) : null}
        >
          {rules.length ? (
            rules.map((rule) =>
              editingRule === rule.id ? (
                <RuleEditor
                  key={`${rule.id}-edit`}
                  rule={rule}
                  busy={busy}
                  onCancel={() => setEditingRule(null)}
                  onSave={(text) => saveRule(rule, text)}
                />
              ) : (
                <ReviewLine
                  key={rule.id}
                  source={rule.edited ? sourceLabel("your_change") : null}
                  onEdit={editable ? () => setEditingRule(rule.id) : undefined}
                  onRemove={
                    editable
                      ? () => remove("rule", rule, REVIEW_COPY.theRule)
                      : undefined
                  }
                  disabled={busy}
                >
                  {rule.text}
                </ReviewLine>
              ),
            )
          ) : (
            <ReviewLine empty>{REVIEW_COPY.noneYet}</ReviewLine>
          )}
        </ReviewSection>

        <ReviewSection label={REVIEW_COPY.people} count={1 + actors.length}>
          <ReviewLine>
            {REVIEW_COPY.caller}
            <Box component="span" sx={{ color: "text.secondary", ml: 0.75 }}>
              {REVIEW_COPY.callerRole}
            </Box>
          </ReviewLine>
          {actors.map((actor) => (
            <ReviewLine
              key={actor.id}
              source={REVIEW_COPY.proposedForScenarios}
            >
              {actor.name}
              {actor.role && (
                <Box
                  component="span"
                  sx={{ color: "text.secondary", ml: 0.75 }}
                >
                  {actor.role}
                </Box>
              )}
            </ReviewLine>
          ))}
        </ReviewSection>

        <ReviewSection
          label={REVIEW_COPY.scenarios}
          count={emptyWorld ? 0 : scenarioTotal}
        >
          {emptyWorld || !useCases.length ? (
            <ReviewLine empty>{REVIEW_COPY.noScenarios}</ReviewLine>
          ) : (
            useCases.map((useCase) => (
              <ReviewLine
                key={useCase.id}
                onRemove={
                  editable && useCases.length > 1
                    ? () => remove("use_case", useCase, REVIEW_COPY.theUseCase)
                    : undefined
                }
                disabled={busy}
              >
                <Box
                  component="span"
                  sx={{ fontVariantNumeric: "tabular-nums" }}
                >
                  {useCase.planned_scenarios}
                </Box>
                <Box component="span" sx={{ ml: 0.75 }}>
                  {useCase.text}
                </Box>
              </ReviewLine>
            ))
          )}
        </ReviewSection>
      </Box>

      <ConfirmDialog
        open={Boolean(confirmRemove)}
        onClose={() => setConfirmRemove(null)}
        title={
          confirmRemove ? REVIEW_COPY.removeTitle(confirmRemove.item.name) : ""
        }
        content={
          confirmRemove ? REVIEW_COPY.removeContent(confirmRemove.impact) : ""
        }
        action={
          <Button
            variant="contained"
            color="error"
            disabled={addEdit.isPending}
            onClick={() => {
              const pendingRemove = confirmRemove;
              setConfirmRemove(null);
              commitRemove(pendingRemove);
            }}
          >
            {REVIEW_COPY.removeAnyway}
          </Button>
        }
      />

      {undo && (
        <Stack direction="row" alignItems="center" spacing={1} sx={{ mt: 1.5 }}>
          <Typography sx={{ typography: "s2", color: "text.secondary" }}>
            {REVIEW_COPY.removedFromPlan(undo.label)}
            {undo.note && (
              <Box component="span" sx={{ color: "status.warning.fg" }}>
                {` ${undo.note}`}
              </Box>
            )}
          </Typography>
          <Button
            size="small"
            variant="outlined"
            disabled={busy}
            onClick={onUndo}
            sx={actionSx}
          >
            {REVIEW_COPY.undo}
          </Button>
        </Stack>
      )}
    </Box>
  );
}

DesignReviewStage.propTypes = {
  envId: PropTypes.string.isRequired,
};
