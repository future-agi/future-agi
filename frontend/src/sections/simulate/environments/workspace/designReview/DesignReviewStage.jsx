import PropTypes from "prop-types";
import { useState } from "react";
import { useNavigate } from "react-router-dom";
import { enqueueSnackbar } from "notistack";
import { Box, Button, Stack, TextField, Typography } from "@mui/material";
import Iconify from "src/components/iconify";
import { ConfirmDialog } from "src/components/custom-dialog";
import ContentSkeleton from "src/components/content-skeleton";
import ErrorState from "src/components/error-state";
import { paths } from "src/routes/paths";
import { errorMessage } from "src/pages/dashboard/harness/harnessShared";
import {
  getDesignImpact,
  useAddDesignEdit,
  useApproveDesign,
  useDesignReview,
  useDiscardDesign,
  useUndoDesignEdit,
} from "src/api/simulate-environments/designReview";
import ViewerLock from "../../components/ViewerLock";
import useSimulationAccess from "../../hooks/useSimulationAccess";
import ReviewSection from "./ReviewSection";
import ReviewLine from "./ReviewLine";
import { REVIEW_COPY, REVIEW_STATUS } from "./designReview.constants";

const showError = (error) =>
  enqueueSnackbar(errorMessage(error), { variant: "error" });

const lastEditIndex = (edits, kind, id) => {
  for (let index = edits.length - 1; index >= 0; index -= 1) {
    if (edits[index].kind === kind && edits[index].id === id)
      return edits[index].index;
  }
  return null;
};

function RuleEditor({ rule, busy, onSave, onCancel }) {
  const [text, setText] = useState(rule.text);
  const trimmed = text.trim();
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
          if (event.key === "Enter" && trimmed) onSave(trimmed);
          if (event.key === "Escape") onCancel();
        }}
        inputProps={{ "aria-label": REVIEW_COPY.ruleLabel, maxLength: 2000 }}
      />
      <Button
        size="small"
        variant="contained"
        color="primary"
        disabled={!trimmed || trimmed === rule.text || busy}
        onClick={() => onSave(trimmed)}
      >
        {REVIEW_COPY.save}
      </Button>
      <Button
        size="small"
        variant="outlined"
        onClick={onCancel}
        sx={{ color: "text.primary", borderColor: "divider" }}
      >
        {REVIEW_COPY.cancel}
      </Button>
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
  const navigate = useNavigate();
  const { canEdit } = useSimulationAccess();
  const { data: review, isLoading, isError, refetch } = useDesignReview(envId);
  const addEdit = useAddDesignEdit(envId);
  const undoEdit = useUndoDesignEdit(envId);
  const approve = useApproveDesign(envId);
  const discard = useDiscardDesign(envId);
  const [editingRule, setEditingRule] = useState(null);
  const [confirmRemove, setConfirmRemove] = useState(null);
  const [discardOpen, setDiscardOpen] = useState(false);
  const [checking, setChecking] = useState(false);

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
    addEdit.isPending ||
    undoEdit.isPending ||
    approve.isPending ||
    discard.isPending;
  const locked = !canEdit || !waiting || busy || checking;
  const keptTables = review.tables.filter((item) => !item.removed);
  const keptTools = review.tools.filter((item) => !item.removed);
  const keptRules = review.rules.filter((item) => !item.removed);
  const plannedScenarios = review.use_cases.reduce(
    (total, item) => total + (item.planned_scenarios || 0),
    0,
  );

  const edit = (body) => addEdit.mutate(body, { onError: showError });
  const undo = (kind, id) => {
    const index = lastEditIndex(review.edits, kind, id);
    if (index !== null) undoEdit.mutate(index, { onError: showError });
  };

  const remove = async (kind, item) => {
    const body = { op: "remove", kind, id: item.id };
    if (kind !== "tool" && kind !== "table") {
      edit(body);
      return;
    }
    setChecking(true);
    try {
      const impact = await getDesignImpact(envId, kind, item.id);
      if (impact.tools.length || impact.rules.length) {
        setConfirmRemove({ body, name: item.name, impact });
      } else {
        edit(body);
      }
    } catch (error) {
      showError(error);
    } finally {
      setChecking(false);
    }
  };

  const onApprove = () => approve.mutate(undefined, { onError: showError });
  const onDiscard = () =>
    discard.mutate(undefined, {
      onSuccess: () => navigate(paths.dashboard.simulate.environments.root),
      onError: (error) => {
        setDiscardOpen(false);
        showError(error);
      },
    });

  return (
    <Box sx={{ height: "100%", overflow: "auto", px: 3, py: 2.5 }}>
      <Stack direction="row" alignItems="flex-start" spacing={2}>
        <Box flex={1} minWidth={0}>
          <Typography sx={{ typography: "m1", fontWeight: 600 }}>
            {REVIEW_COPY.title}
          </Typography>
          <Typography
            sx={{ typography: "s1", color: "text.tertiary", mt: 0.25 }}
          >
            {waiting
              ? REVIEW_COPY.summary({
                  tables: keptTables.length,
                  tools: keptTools.length,
                  rules: keptRules.length,
                  scenarios: plannedScenarios,
                })
              : REVIEW_COPY.approved}
          </Typography>
        </Box>
        {waiting && (
          <Stack direction="row" spacing={1} sx={{ flexShrink: 0 }}>
            <ViewerLock locked={!canEdit} title={REVIEW_COPY.viewerLocked}>
              <Button
                size="small"
                variant="outlined"
                disabled={locked}
                onClick={() => setDiscardOpen(true)}
                sx={{ color: "text.primary", borderColor: "divider" }}
              >
                {REVIEW_COPY.discard}
              </Button>
            </ViewerLock>
            <ViewerLock locked={!canEdit} title={REVIEW_COPY.viewerLocked}>
              <Button
                size="small"
                variant="contained"
                color="primary"
                disabled={locked || review.agent_changed}
                onClick={onApprove}
                endIcon={
                  <Iconify icon="solar:alt-arrow-right-linear" width={14} />
                }
              >
                {REVIEW_COPY.approve}
              </Button>
            </ViewerLock>
          </Stack>
        )}
      </Stack>

      {waiting && review.agent_changed && (
        <Stack
          direction="row"
          alignItems="center"
          spacing={1.25}
          sx={{
            mt: 2,
            px: 1.5,
            py: 1,
            borderRadius: 1,
            border: "1px solid",
            borderColor: "status.warning.border",
            bgcolor: "status.warning.bg",
          }}
        >
          <Iconify
            icon="solar:danger-triangle-linear"
            width={16}
            sx={{ color: "status.warning.fg", flexShrink: 0 }}
          />
          <Typography sx={{ typography: "s1", color: "text.primary" }}>
            {REVIEW_COPY.agentChanged}
          </Typography>
        </Stack>
      )}

      <Box sx={{ mt: 2 }}>
        <ReviewSection
          label={REVIEW_COPY.records}
          count={review.tables.length}
          source={REVIEW_COPY.recordsSource}
        >
          {review.tables.map((table) => (
            <ReviewLine
              key={table.id}
              primary={table.name}
              secondary={REVIEW_COPY.columns(table.columns)}
              removed={table.removed}
              removeLabel={REVIEW_COPY.remove(table.name)}
              onRemove={() => remove("table", table)}
              onUndo={() => undo("table", table.id)}
              disabled={locked}
            />
          ))}
        </ReviewSection>

        <ReviewSection
          label={REVIEW_COPY.tools}
          count={review.tools.length}
          source={REVIEW_COPY.toolsSource}
        >
          {review.tools.map((tool) => (
            <ReviewLine
              key={tool.id}
              primary={tool.name}
              secondary={tool.description}
              removed={tool.removed}
              removeLabel={REVIEW_COPY.remove(tool.name)}
              onRemove={() => remove("tool", tool)}
              onUndo={() => undo("tool", tool.id)}
              disabled={locked}
            />
          ))}
        </ReviewSection>

        <ReviewSection
          label={REVIEW_COPY.rules}
          count={review.rules.length}
          source={REVIEW_COPY.rulesSource}
        >
          {review.rules.map((rule) =>
            editingRule === rule.id ? (
              <RuleEditor
                key={rule.id}
                rule={rule}
                busy={busy}
                onCancel={() => setEditingRule(null)}
                onSave={(text) =>
                  addEdit.mutate(
                    { op: "edit", kind: "rule", id: rule.id, text },
                    {
                      onSuccess: () => setEditingRule(null),
                      onError: showError,
                    },
                  )
                }
              />
            ) : (
              <ReviewLine
                key={rule.id}
                primary={rule.text}
                secondary={rule.edited ? rule.original_text : null}
                removed={rule.removed}
                edited={rule.edited}
                removeLabel={REVIEW_COPY.remove(
                  REVIEW_COPY.ruleLabel.toLowerCase(),
                )}
                onEdit={() => setEditingRule(rule.id)}
                onRemove={() => remove("rule", rule)}
                onUndo={() => undo("rule", rule.id)}
                disabled={locked}
              />
            ),
          )}
        </ReviewSection>

        <ReviewSection
          label={REVIEW_COPY.useCases}
          count={review.use_cases.length}
          source={REVIEW_COPY.useCasesSource}
        >
          {review.use_cases.map((useCase) => (
            <ReviewLine
              key={useCase.id}
              primary={useCase.text}
              aside={REVIEW_COPY.scenarios(useCase.planned_scenarios)}
              removed={useCase.removed}
              removeLabel={REVIEW_COPY.remove(useCase.text)}
              onRemove={() => remove("use_case", useCase)}
              onUndo={() => undo("use_case", useCase.id)}
              disabled={locked}
            />
          ))}
        </ReviewSection>
      </Box>

      <ConfirmDialog
        open={Boolean(confirmRemove)}
        onClose={() => setConfirmRemove(null)}
        title={confirmRemove ? REVIEW_COPY.removeTitle(confirmRemove.name) : ""}
        content={
          confirmRemove ? REVIEW_COPY.removeContent(confirmRemove.impact) : ""
        }
        action={
          <Button
            variant="contained"
            color="error"
            disabled={addEdit.isPending}
            onClick={() => {
              const { body } = confirmRemove;
              setConfirmRemove(null);
              edit(body);
            }}
          >
            {REVIEW_COPY.removeAnyway}
          </Button>
        }
      />

      <ConfirmDialog
        open={discardOpen}
        onClose={() => setDiscardOpen(false)}
        closeDisabled={discard.isPending}
        title={REVIEW_COPY.discardTitle}
        content={REVIEW_COPY.discardContent}
        action={
          <Button
            variant="contained"
            color="error"
            disabled={discard.isPending}
            onClick={onDiscard}
          >
            {REVIEW_COPY.discard}
          </Button>
        }
      />
    </Box>
  );
}

DesignReviewStage.propTypes = {
  envId: PropTypes.string.isRequired,
};
