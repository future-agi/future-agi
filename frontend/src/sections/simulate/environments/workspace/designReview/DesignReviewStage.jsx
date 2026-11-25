import PropTypes from "prop-types";
import { useEffect, useState } from "react";
import { enqueueSnackbar } from "notistack";
import {
  Box,
  Button,
  ButtonBase,
  Stack,
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableRow,
  TextField,
  Tooltip,
  Typography,
} from "@mui/material";
import { ROW_HEIGHT, TABLE_HEADER_HEIGHT } from "src/theme/sizes";
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
import ReviewLine from "./ReviewLine";
import ReviewBrief, { ReviewBanner } from "./ReviewBrief";
import ToolChip from "./ToolChip";
import {
  AGENT_TOOL_SOURCES,
  PARK_AFTER_MS,
  REVIEW_COPY,
  REVIEW_STATUS,
  sourceWords,
} from "./designReview.constants";
import {
  commonSource,
  isParked,
  kept,
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
    if (!busy && trimmed && trimmed !== rule.text) onSave(trimmed);
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
  const emptyWorld =
    !tables.length && !tools.length && !rules.length && !useCases.length;
  const parked =
    waiting && isParked(review.created_at, now, continuedAt, PARK_AFTER_MS);

  const toolSource = (tool) => sourceLabel(tool.source);
  const tableSource = (table) => sourceLabel(table.source);
  const toolCommon = commonSource(mapTools.map(toolSource));
  const tableCommon = commonSource(tables.map(tableSource));
  const useCaseCommon = commonSource(
    useCases.map((useCase) => sourceLabel(useCase.source)),
  );
  const ruleCommon = commonSource(
    rules
      .filter((rule) => !rule.edited)
      .map((rule) => sourceLabel(rule.source)),
  );

  const commitRemove = ({ kind, item, label, note }) => {
    setUndo(null);
    addEdit.mutate(
      { op: "remove", kind, id: item.id },
      {
        onSuccess: (next) =>
          setUndo({
            label,
            note,
            editId: next.edits[next.edits.length - 1].edit_id,
          }),
        onError: showError,
      },
    );
  };

  const remove = async (kind, item, label) => {
    const note =
      kind === "tool" && AGENT_TOOL_SOURCES.has(item.source)
        ? REVIEW_COPY.agentStillCalls
        : null;
    if (kind !== "tool") {
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
    const { editId } = undo;
    setUndo(null);
    undoEdit.mutate(editId, {
      onError: (error) => {
        showError(error);
        if (error?.statusCode === 409) refetch();
      },
    });
  };

  const saveRule = (rule, text) => {
    setUndo(null);
    addEdit.mutate(
      { op: "edit", kind: "rule", id: rule.id, text },
      { onSuccess: () => setEditingRule(null), onError: showError },
    );
  };

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

  const editingOpen = editingRule !== null;
  const approveTip = review.agent_changed
    ? REVIEW_COPY.agentChanged
    : emptyWorld
      ? REVIEW_COPY.emptyWorldTip
      : editingOpen
        ? REVIEW_COPY.finishEditing
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
                !canEdit ||
                busy ||
                editingOpen ||
                emptyWorld ||
                Boolean(review.agent_changed)
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

  const leftOutLines = leftOut.map((one) => (
    <ReviewLine key={`left-${one.tool}`}>
      <Box component="span" sx={{ color: "text.secondary" }}>
        {REVIEW_COPY.leftOut(one.tool)}
      </Box>
    </ReviewLine>
  ));

  const chipList = (list) =>
    list.length ? (
      <Stack direction="row" flexWrap="wrap" sx={{ gap: 0.75 }}>
        {list.map(chip)}
      </Stack>
    ) : (
      <Typography sx={{ typography: "s1", color: "text.disabled" }}>
        {REVIEW_COPY.none}
      </Typography>
    );

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
        <Table size="small">
          <TableHead>
            <TableRow sx={{ height: TABLE_HEADER_HEIGHT.default }}>
              <TableCell sx={{ width: 200 }}>
                {REVIEW_COPY.tableHeader.table}
              </TableCell>
              <TableCell sx={{ width: 110 }}>
                {REVIEW_COPY.tableHeader.columns}
              </TableCell>
              <TableCell>{REVIEW_COPY.tableHeader.changedBy}</TableCell>
              <TableCell>{REVIEW_COPY.tableHeader.readBy}</TableCell>
            </TableRow>
          </TableHead>
          <TableBody>
            {tables.map((table) => {
              const { changes, reads } = byTable.get(table.name);
              return (
                <TableRow
                  key={table.id}
                  hover
                  sx={{
                    height: ROW_HEIGHT.default,
                    "& td": { verticalAlign: "top", py: 1 },
                  }}
                >
                  <TableCell>
                    <Typography sx={{ typography: "s1", fontWeight: 500 }}>
                      {table.name}
                    </Typography>
                    {tableSource(table) !== tableCommon && (
                      <Typography
                        sx={{ typography: "s3", color: "text.tertiary" }}
                      >
                        {tableSource(table)}
                      </Typography>
                    )}
                  </TableCell>
                  <TableCell>
                    <Tooltip arrow title={table.columns.join(", ")}>
                      <Typography
                        component="span"
                        sx={{ typography: "s1", color: "text.secondary" }}
                      >
                        {REVIEW_COPY.columns(table.columns)}
                      </Typography>
                    </Tooltip>
                  </TableCell>
                  <TableCell>{chipList(changes)}</TableCell>
                  <TableCell>{chipList(reads)}</TableCell>
                </TableRow>
              );
            })}
            {none.map((tool) => (
              <TableRow
                key={`no-table-${tool.id}`}
                hover
                sx={{
                  height: ROW_HEIGHT.default,
                  "& td": { verticalAlign: "top", py: 1 },
                }}
              >
                <TableCell>
                  <Typography
                    sx={{ typography: "s1", color: "text.secondary" }}
                  >
                    {REVIEW_COPY.noTable}
                  </Typography>
                </TableCell>
                <TableCell />
                <TableCell colSpan={2}>
                  <Stack direction="row" alignItems="center" spacing={1}>
                    {chip(tool)}
                    <Typography
                      sx={{ typography: "s3", color: "text.tertiary" }}
                    >
                      {REVIEW_COPY.noTableWhy[tool.status] ||
                        REVIEW_COPY.noTableWhy.unknown}
                    </Typography>
                  </Stack>
                </TableCell>
              </TableRow>
            ))}
          </TableBody>
        </Table>
        {leftOutLines}
      </ReviewSection>
    );
  };

  const separateRecordsAndTools = (
    <>
      {tables.length > 0 && (
        <ReviewSection
          label={REVIEW_COPY.records}
          count={tables.length}
          source={tableCommon ? sourceWords(tableCommon) : null}
        >
          {tables.map((table) => (
            <ReviewLine key={table.id}>
              <Box component="span" sx={{ fontWeight: 500 }}>
                {table.name}
              </Box>
              <Box component="span" sx={{ color: "text.secondary", ml: 0.75 }}>
                {REVIEW_COPY.columns(table.columns)}
              </Box>
            </ReviewLine>
          ))}
        </ReviewSection>
      )}
      {(mapTools.length > 0 || leftOut.length > 0) && (
        <ReviewSection
          label={REVIEW_COPY.tools}
          count={mapTools.length}
          source={toolCommon ? sourceWords(toolCommon) : null}
        >
          {mapTools.map((tool) => (
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
          ))}
          {leftOutLines}
        </ReviewSection>
      )}
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
                  useCases: useCases.length,
                })
              : REVIEW_COPY.approved}
          </Typography>
          {waiting && !emptyWorld && scenarioTotal > 0 && (
            <Typography
              sx={{ typography: "s1", color: "text.secondary", mt: 0.25 }}
            >
              {REVIEW_COPY.writtenAfterApproval(scenarioTotal, useCases.length)}
            </Typography>
          )}
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
                <Stack
                  key={one.tool}
                  direction="row"
                  alignItems="center"
                  spacing={1}
                  sx={{
                    px: 1.5,
                    minHeight: ROW_HEIGHT.default,
                    "&:hover": { bgcolor: "action.hover" },
                  }}
                >
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
              );
            })}
          </ReviewSection>
        </Box>
      )}

      <Box sx={{ mt: 2 }}>
        {tables.length && mapTools.length
          ? recordsAndTools()
          : separateRecordsAndTools}

        {rules.length > 0 && (
          <ReviewSection
            label={REVIEW_COPY.rules}
            count={rules.length}
            source={ruleCommon ? sourceWords(ruleCommon) : null}
          >
            {rules.map((rule) =>
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
            )}
          </ReviewSection>
        )}

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

        {useCases.length > 0 && (
          <ReviewSection
            label={REVIEW_COPY.useCases}
            count={useCases.length}
            source={useCaseCommon ? sourceWords(useCaseCommon) : null}
          >
            {emptyWorld ? (
              <ReviewLine empty>{REVIEW_COPY.noScenarios}</ReviewLine>
            ) : (
              useCases.map((useCase) => (
                <ReviewLine
                  key={useCase.id}
                  onRemove={
                    editable
                      ? () =>
                          remove("use_case", useCase, REVIEW_COPY.theUseCase)
                      : undefined
                  }
                  removeBlocked={
                    useCases.length > 1 ? null : REVIEW_COPY.keepOneUseCase
                  }
                  disabled={busy}
                >
                  {useCase.text}
                </ReviewLine>
              ))
            )}
          </ReviewSection>
        )}
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
