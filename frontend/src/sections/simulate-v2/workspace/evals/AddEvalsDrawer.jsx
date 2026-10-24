import PropTypes from "prop-types";
import { useMemo, useState } from "react";
import {
  Box,
  Stack,
  Typography,
  Button,
  LinearProgress,
  Tooltip,
} from "@mui/material";
import Iconify from "src/components/iconify";
import { EvalPickerDrawer } from "src/sections/common/EvalPicker";
import {
  simulationPreviewData,
  resolveEval,
  TOOL_CALL_JUDGE_ID,
  MAX_APPLIED_EVALS,
  EVAL_CAP_NOTE,
} from "../../_mock/evals";
import { getAgentType } from "../../_mock/agentTypes";
import { evalRailOf } from "../../_mock/metering";
import { railInputOf } from "./evalRail";

/**
 * Add evals — tick several, then map them one at a time.
 *
 * Both screens are the product's own: the list with its opt-in `multiSelect`
 * checkboxes, and the real config screen for the mapping. The only additions
 * are a completion bar in the config header and a primary button that reads
 * "Next" until the last eval, so a queue of evals is one trip through the
 * picker instead of one trip each.
 */
export default function AddEvalsDrawer({
  open,
  onClose,
  env,
  envState,
  existingIds,
  onAdd,
  slotsLeft = Infinity,
}) {
  const [checked, setChecked] = useState({});
  const [queue, setQueue] = useState([]);
  const [index, setIndex] = useState(0);
  const [collected, setCollected] = useState([]);

  const mapping = queue.length > 0;

  /*
    "create-simulate" rather than "workbench": both let an eval be bound before
    anything has run, but workbench mode shows only the mapping selects, while
    this one renders the scenario chips and the columns/value table with
    runtime fields marked as resolved server-side — which is the panel this
    flow is supposed to have.
  */
  const previewData = useMemo(
    () =>
      simulationPreviewData(
        env,
        envState,
        getAgentType(envState?.agent?.typeId),
      ),
    [env, envState],
  );
  const selected = useMemo(() => Object.values(checked), [checked]);
  /* The evals already on this environment, by name, for the picker's
     "Added evals" section — so they sit in their own box instead of showing
     as disabled rows scattered through the library. */
  /* Callers pass ids, or (the run screen) the eval records themselves. */
  const addedIds = useMemo(
    () =>
      [...(existingIds || [])]
        .map((x) => (typeof x === "string" ? x : x?.id))
        .filter(Boolean),
    [existingIds],
  );
  const addedEvals = useMemo(() => {
    const byId = new Map(
      (envState?.evals || []).map((e) => [
        typeof e === "string" ? e : e?.id,
        e,
      ]),
    );
    return addedIds.map((id) => {
      const ev = resolveEval(byId.get(id) || id);
      return {
        id,
        name: ev?.instanceName || ev?.name || id,
        /* The rail beside the kind — free, or credits per call. */
        meta: [ev?.category, ev?.type, ev && evalRailOf(ev).label]
          .filter(Boolean)
          .join(" · "),
      };
    });
  }, [envState, addedIds]);
  const selectedIds = useMemo(() => new Set(Object.keys(checked)), [checked]);
  /* What the ticked evals cost per call, before they're added (AC-19.6). */
  const selectedRail = useMemo(() => {
    const rails = selected.map((e) => evalRailOf(railInputOf(e)));
    const metered = rails.filter((r) => r.metered);
    return {
      free: rails.length - metered.length,
      metered: metered.length,
      credits: metered.reduce((a, r) => a + (r.credits || 0), 0),
    };
  }, [selected]);

  const reset = () => {
    setChecked({});
    setQueue([]);
    setIndex(0);
    setCollected([]);
  };

  const close = () => {
    reset();
    onClose();
  };

  /* Up to 8 catalogue evaluations plus the tool-call judge: ticking stops
     at the slots this environment has left. */
  const toggle = (evalItem) =>
    setChecked((c) => {
      const next = { ...c };
      if (next[evalItem.id]) delete next[evalItem.id];
      else if (
        Object.values(next).filter((e) => e.id !== TOOL_CALL_JUDGE_ID).length >=
          slotsLeft &&
        evalItem.id !== TOOL_CALL_JUDGE_ID
      )
        return c;
      else next[evalItem.id] = evalItem;
      return next;
    });
  const full =
    Number.isFinite(slotsLeft) &&
    selected.filter((e) => e.id !== TOOL_CALL_JUDGE_ID).length >= slotsLeft;

  const entry = (config) => ({
    id: config.templateId || config.id || `eval-${config.name}`,
    name: config.name || "Eval",
    blurb:
      Object.entries(config.mapping || {})
        .map(([k, v]) => `${k} → ${v}`)
        .join(" · ") || "Added from the eval library",
    mapping: config.mapping || {},
    model: config.model,
    threshold: 0.8,
    custom: true,
    /* Kept so the pencil reopens the config screen as it was saved. */
    templateId: config.templateId || config.id,
    instanceName: config.name,
    config: config.config,
    runConfig: {
      agent_mode: config.agent_mode,
      check_internet: config.check_internet,
      summary: config.summary,
      tools: config.tools,
      knowledge_bases: config.knowledge_bases,
      data_injection: config.data_injection,
      error_localizer_enabled: config.error_localizer_enabled,
    },
  });

  /**
   * The config screen's own primary button saves the eval it is on. In a queue
   * that doubles as "next": collect what it returns, move to the following
   * eval, and only hand the batch over once the last one is saved.
   */
  const onEvalAdded = (config) => {
    const done = [...collected, entry(config)];
    if (index === queue.length - 1) {
      onAdd(done);
      close();
      return;
    }
    setCollected(done);
    setIndex(index + 1);
  };

  const total = queue.length;
  const last = index === total - 1;
  const pct = total ? Math.round((collected.length / total) * 100) : 0;

  return (
    <EvalPickerDrawer
      // Re-keys the provider per eval, so each one opens at its own config.
      key={mapping ? queue[index]?.id : "list"}
      open={open}
      onClose={close}
      // Same surface as every other drawer in this flow — see SideDrawer for
      // why the theme rule has to be outranked to get it.
      paperSx={{ backgroundColor: "background.paper", backgroundImage: "none" }}
      source="create-simulate"
      // No sourceId, same as the legacy run setup: with one the picker lists
      // from develops/<dataset uuid>/get_evals_list/, and an environment id
      // isn't a dataset uuid, so the library came back empty.
      sourcePreviewData={previewData}
      existingEvals={addedIds.map((id) => ({ id }))}
      addedEvals={addedEvals}
      onEvalAdded={
        mapping
          ? onEvalAdded
          : (config) => {
              onAdd([entry(config)]);
              close();
            }
      }
      initialEval={mapping ? queue[index] : null}
      // In a queue the drawer must not close itself after each save — this
      // component decides when the last eval is done.
      keepOpenAfterSave={mapping}
      // The drawer hides its own header on the config step, so without this
      // the only way out of a queued mapping is the back arrow.
      showClose={mapping}
      // Built-in evals hide their version controls elsewhere; this screen
      // shows them, matching the mapping screen in production.
      showVersionControls
      multiSelect={!mapping}
      selectedIds={selectedIds}
      onToggleSelect={toggle}
      headerAction={
        !mapping && (
          <Stack direction="row" alignItems="center" spacing={1.25}>
            {Number.isFinite(slotsLeft) && (
              <Tooltip arrow title={EVAL_CAP_NOTE}>
                <Typography
                  sx={{
                    typography: "s3",
                    color: full ? "text.primary" : "text.tertiary",
                    fontWeight: full ? 600 : 400,
                    whiteSpace: "nowrap",
                    cursor: "help",
                    fontVariantNumeric: "tabular-nums",
                  }}
                >
                  {full
                    ? `No slots left · up to ${MAX_APPLIED_EVALS}`
                    : `${slotsLeft - selected.filter((e) => e.id !== TOOL_CALL_JUDGE_ID).length} of ${MAX_APPLIED_EVALS} slots left`}
                </Typography>
              </Tooltip>
            )}
            {selected.length > 0 && (
              <Tooltip
                arrow
                title="Managed LLM judges are metered in AI credits per call. Rule-based evals and ones judged by your own model key are free."
              >
                <Typography
                  sx={{
                    typography: "s3",
                    color: "text.tertiary",
                    whiteSpace: "nowrap",
                    cursor: "help",
                    fontVariantNumeric: "tabular-nums",
                  }}
                >
                  {[
                    selectedRail.free > 0 && `${selectedRail.free} free`,
                    selectedRail.metered > 0 &&
                      `${selectedRail.metered} at ${selectedRail.credits} credit${selectedRail.credits === 1 ? "" : "s"} / call`,
                  ]
                    .filter(Boolean)
                    .join(" · ")}
                </Typography>
              </Tooltip>
            )}
            <Button
              variant="contained"
              // Deliberately not `color="primary"`: this sits in a row of black
              // per-eval "Add" buttons, so it inherits the same default and
              // matches its neighbours instead of being the one purple thing in
              // the list. Only this button — everything else stays brand purple.
              size="small"
              disabled={selected.length === 0}
              onClick={() => {
                setQueue(selected);
                setIndex(0);
                setCollected([]);
              }}
              startIcon={<Iconify icon="solar:add-linear" width={16} />}
            >
              Add Evaluations{selected.length ? ` (${selected.length})` : ""}
            </Button>
          </Stack>
        )
      }
      progress={
        mapping ? <CompletionBar pct={pct} index={index} total={total} /> : null
      }
      primaryLabel={
        mapping
          ? last
            ? `Add ${total} ${total === 1 ? "evaluation" : "evaluations"}`
            : "Next"
          : null
      }
    />
  );
}

AddEvalsDrawer.propTypes = {
  open: PropTypes.bool,
  onClose: PropTypes.func,
  env: PropTypes.object,
  envState: PropTypes.object,
  existingIds: PropTypes.oneOfType([PropTypes.array, PropTypes.object]),
  onAdd: PropTypes.func,
  slotsLeft: PropTypes.number,
};

function CompletionBar({ pct, index, total }) {
  return (
    <Stack
      direction="row"
      alignItems="center"
      spacing={1.25}
      sx={{ flexShrink: 0 }}
    >
      <Typography sx={{ typography: "s2", color: "text.tertiary" }}>
        Completion rate
      </Typography>
      <LinearProgress
        variant="determinate"
        value={pct}
        sx={{
          width: 180,
          height: 5,
          borderRadius: 3,
          bgcolor: "background.neutral",
          "& .MuiLinearProgress-bar": {
            bgcolor: "status.success.solid",
            borderRadius: 3,
          },
        }}
      />
      <Typography
        sx={{
          typography: "s2",
          fontWeight: 600,
          color: pct ? "status.success.fg" : "text.tertiary",
        }}
      >
        {pct}%
      </Typography>
      <Typography sx={{ typography: "s2", color: "text.tertiary" }}>
        ({index + 1}/{total})
      </Typography>
      <Box />
    </Stack>
  );
}
CompletionBar.propTypes = {
  pct: PropTypes.number,
  index: PropTypes.number,
  total: PropTypes.number,
};
