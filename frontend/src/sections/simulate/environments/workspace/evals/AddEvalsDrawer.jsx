import PropTypes from "prop-types";
import { useMemo, useState } from "react";
import { Stack, Typography, Button, LinearProgress } from "@mui/material";
import Iconify from "src/components/iconify";
import { EvalPickerDrawer } from "src/sections/common/EvalPicker";
import { simulationPreviewData } from "src/api/simulate-environments/_fixtures/evalCatalog";
import { EVALS_COPY, ENV_SHAPE, ENV_STATE_SHAPE } from "./evals.constants";

/**
 * Add evals — tick several from the library, then map them one at a time.
 *
 * Both screens are the product's own eval picker: the list with its opt-in
 * `multiSelect` checkboxes, and the real config screen for the mapping. Tick
 * a few, "Add Evaluations (N)" builds a queue, and the config screen is walked
 * once per eval with a completion bar and a primary button that reads "Next"
 * until the last — so a batch of evals is one trip through the picker.
 *
 * A single row "Add" still works on its own: it maps that one eval and, because
 * the drawer stays open, the user can keep adding without re-opening it. Every
 * saved eval flows through `onAdd`.
 */
export default function AddEvalsDrawer({
  open,
  onClose,
  env,
  envState,
  existingIds,
  onAdd,
}) {
  const [checked, setChecked] = useState({});
  const [queue, setQueue] = useState([]);
  const [index, setIndex] = useState(0);
  const [collected, setCollected] = useState([]);

  const mapping = queue.length > 0;

  // "create-simulate" source: the picker renders the scenario chips and the
  // columns/value table with runtime fields resolved server-side, letting an
  // eval be bound before the sim has run.
  const previewData = useMemo(
    () =>
      simulationPreviewData(env, envState, {
        id: envState?.agent?.typeId,
        label: envState?.agent?.typeId,
      }),
    [env, envState]
  );

  const selected = useMemo(() => Object.values(checked), [checked]);
  const selectedIds = useMemo(() => new Set(Object.keys(checked)), [checked]);

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

  const toggle = (evalItem) =>
    setChecked((c) => {
      const next = { ...c };
      if (next[evalItem.id]) delete next[evalItem.id];
      else next[evalItem.id] = evalItem;
      return next;
    });

  const entry = (config) => ({
    id: config.templateId || config.id || `eval-${config.name}`,
    name: config.name || EVALS_COPY.pickerFallbackName,
    blurb:
      Object.entries(config.mapping || {})
        .map(([k, v]) => `${k} → ${v}`)
        .join(" · ") || EVALS_COPY.pickerBlurb,
    mapping: config.mapping || {},
    model: config.model,
    threshold: 0.8,
    custom: true,
  });

  // The config screen's primary button saves the eval it is on. In a queue
  // that doubles as "next": collect what it returns, move to the following
  // eval, and only hand the batch to the host once the last one is saved.
  const onQueueEvalAdded = (config) => {
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
      // Re-keys the provider per eval so each one opens at its own config.
      key={mapping ? queue[index]?.id : "list"}
      open={open}
      onClose={close}
      source="create-simulate"
      sourceId={env?.id || ""}
      sourcePreviewData={previewData}
      existingEvals={[...(existingIds || [])].map((id) => ({ id }))}
      onEvalAdded={
        mapping ? onQueueEvalAdded : (config) => onAdd([entry(config)])
      }
      initialEval={mapping ? queue[index] : null}
      // The picker must not close itself after each save — a single add keeps
      // the list open for more, and the queue closes only once the last eval
      // is done.
      keepOpenAfterSave
      keepOpenAfterEditSave={mapping}
      // The config step hides the picker's own header, so during a queued
      // mapping the close control has to be surfaced here.
      showClose={mapping}
      multiSelect={!mapping}
      selectedIds={selectedIds}
      onToggleSelect={toggle}
      headerAction={
        !mapping ? (
          <Button
            variant="contained"
            size="small"
            disabled={selected.length === 0}
            onClick={() => {
              setQueue(selected);
              setIndex(0);
              setCollected([]);
            }}
            startIcon={<Iconify icon="mingcute:add-line" width={16} />}
            sx={{ textTransform: "none", fontSize: "12px" }}
          >
            Add Evaluations{selected.length ? ` (${selected.length})` : ""}
          </Button>
        ) : null
      }
      progress={
        mapping ? (
          <CompletionBar pct={pct} index={index} total={total} />
        ) : null
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
  env: ENV_SHAPE,
  envState: ENV_STATE_SHAPE,
  existingIds: PropTypes.instanceOf(Set),
  onAdd: PropTypes.func,
};

function CompletionBar({ pct, index, total }) {
  return (
    <Stack
      direction="row"
      alignItems="center"
      spacing={1.25}
      sx={{ flexShrink: 0 }}
    >
      <Typography sx={{ typography: "s2", color: "text.subtitle" }}>
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
            bgcolor: "success.main",
            borderRadius: 3,
          },
        }}
      />
      <Typography
        sx={{
          typography: "s2",
          fontWeight: "fontWeightBold",
          color: pct ? "success.main" : "text.subtitle",
        }}
      >
        {pct}%
      </Typography>
      <Typography sx={{ typography: "s2", color: "text.subtitle" }}>
        ({index + 1}/{total})
      </Typography>
    </Stack>
  );
}

CompletionBar.propTypes = {
  pct: PropTypes.number,
  index: PropTypes.number,
  total: PropTypes.number,
};
