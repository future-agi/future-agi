import PropTypes from "prop-types";
import { useMemo, useState } from "react";
import { Box, Stack, Typography, Button, LinearProgress } from "@mui/material";
import Iconify from "src/components/iconify";
import { EvalPickerDrawer } from "src/sections/common/EvalPicker";
import { simulationPreviewData } from "../../_mock/evals";
import { getAgentType } from "../../_mock/agentTypes";

/**
 * Add evals — tick several, then map them one at a time.
 *
 * Both screens are the product's own: the list with its opt-in `multiSelect`
 * checkboxes, and the real config screen for the mapping. The only additions
 * are a completion bar in the config header and a primary button that reads
 * "Next" until the last eval, so a queue of evals is one trip through the
 * picker instead of one trip each.
 */
export default function AddEvalsDrawer({ open, onClose, env, envState, existingIds, onAdd }) {
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
    () => simulationPreviewData(env, envState, getAgentType(envState?.agent?.typeId)),
    [env, envState],
  );
  const selected = useMemo(() => Object.values(checked), [checked]);
  const selectedIds = useMemo(() => new Set(Object.keys(checked)), [checked]);

  const reset = () => {
    setChecked({});
    setQueue([]);
    setIndex(0);
    setCollected([]);
  };

  const close = () => { reset(); onClose(); };

  const toggle = (evalItem) =>
    setChecked((c) => {
      const next = { ...c };
      if (next[evalItem.id]) delete next[evalItem.id];
      else next[evalItem.id] = evalItem;
      return next;
    });

  const entry = (config) => ({
    id: config.templateId || config.id || `eval-${config.name}`,
    name: config.name || "Eval",
    blurb: Object.entries(config.mapping || {}).map(([k, v]) => `${k} → ${v}`).join(" · ")
      || "Added from the eval library",
    mapping: config.mapping || {},
    model: config.model,
    threshold: 0.8,
    custom: true,
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
      source="create-simulate"
      sourceId={env?.id || ""}
      sourcePreviewData={previewData}
      existingEvals={[...(existingIds || [])].map((id) => ({ id }))}
      onEvalAdded={mapping ? onEvalAdded : (config) => { onAdd([entry(config)]); close(); }}
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
          <Button
            variant="contained"
            // Deliberately not `color="primary"`: this sits in a row of black
            // per-eval "Add" buttons, so it inherits the same default and
            // matches its neighbours instead of being the one purple thing in
            // the list. Only this button — everything else stays brand purple.
            size="small"
            disabled={selected.length === 0}
            onClick={() => { setQueue(selected); setIndex(0); setCollected([]); }}
            startIcon={<Iconify icon="mingcute:add-line" width={16} />}
            sx={{ textTransform: "none", fontSize: "12px" }}
          >
            Add Evaluations{selected.length ? ` (${selected.length})` : ""}
          </Button>
        )
      }
      progress={mapping ? <CompletionBar pct={pct} index={index} total={total} /> : null}
      primaryLabel={
        mapping
          ? last ? `Add ${total} ${total === 1 ? "evaluation" : "evaluations"}` : "Next"
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
  existingIds: PropTypes.object,
  onAdd: PropTypes.func,
};

function CompletionBar({ pct, index, total }) {
  return (
    <Stack direction="row" alignItems="center" spacing={1.25} sx={{ flexShrink: 0 }}>
      <Typography sx={{ typography: "s2", color: "text.subtitle" }}>Completion rate</Typography>
      <LinearProgress
        variant="determinate"
        value={pct}
        sx={{
          width: 180, height: 5, borderRadius: 3,
          bgcolor: "background.neutral",
          "& .MuiLinearProgress-bar": { bgcolor: "#16A34A", borderRadius: 3 },
        }}
      />
      <Typography sx={{ typography: "s2", fontWeight: 700, color: pct ? "#16A34A" : "text.subtitle" }}>
        {pct}%
      </Typography>
      <Typography sx={{ typography: "s2", color: "text.subtitle" }}>
        ({index + 1}/{total})
      </Typography>
      <Box />
    </Stack>
  );
}
CompletionBar.propTypes = { pct: PropTypes.number, index: PropTypes.number, total: PropTypes.number };
