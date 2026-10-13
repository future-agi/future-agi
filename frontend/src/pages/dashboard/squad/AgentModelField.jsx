import PropTypes from "prop-types";
import {
  Alert,
  Box,
  Button,
  Stack,
  TextField,
  Typography,
} from "@mui/material";

export default function AgentModelField({
  draft,
  selection,
  onChange,
  disabled,
  isEdit,
}) {
  const selected = selection?.selected;
  const effective =
    selected ||
    (selection?.models || []).find(
      (entry) =>
        entry.modelId ===
        (isEdit ? draft.savedModelId : selection?.defaultModelId),
    );
  const rate = effective?.rate;
  const price = (value) =>
    typeof value === "number" && Number.isFinite(value) && value >= 0
      ? `$${value}`
      : null;
  const inputPrice = price(rate?.inputPer1MUsd);
  const outputPrice = price(rate?.outputPer1MUsd);
  return (
    <Stack spacing={1}>
      <TextField
        select
        fullWidth
        SelectProps={{ native: true }}
        label="Agent model"
        value={draft.modelId || ""}
        disabled={disabled || !selection?.available || selection.loading}
        onChange={(event) => onChange({ modelId: event.target.value })}
        helperText={
          isEdit
            ? "A model change applies after you pause and start the agent to deploy it."
            : "Choose a model for this agent. Availability and pricing are checked again by the server."
        }
      >
        <option value="">
          {isEdit
            ? `Keep saved model${draft.savedModelId ? ` · ${draft.savedModelId}` : ""}`
            : `Runtime default${selection?.defaultModelId ? ` · ${selection.defaultModelId}` : ""}`}
        </option>
        {draft.modelId && !selected ? (
          <option value={draft.modelId} disabled>
            {draft.modelId} (unavailable)
          </option>
        ) : null}
        {(selection?.models || []).map((model) => (
          <option
            key={model.modelId}
            value={model.modelId}
            disabled={!model.selectable}
          >
            {model.modelId}
            {!model.selectable
              ? ` · ${model.reason || model.refusal || "Unavailable"}`
              : ""}
          </option>
        ))}
      </TextField>
      {selection?.loading ? (
        <Typography variant="caption">Reading available models…</Typography>
      ) : null}
      {selection?.error ? (
        <Alert
          severity="info"
          action={
            <Button onClick={selection.reload} disabled={disabled}>
              Retry
            </Button>
          }
        >
          {selection.error}
        </Alert>
      ) : null}
      {selected && !selected.selectable ? (
        <Alert severity="warning">
          {selected.reason || "This model cannot currently be selected."}
        </Alert>
      ) : null}
      {effective?.billing === "included" ? (
        <Typography variant="caption">
          Model usage is included under the runtime’s billing policy.
        </Typography>
      ) : null}
      {effective?.billing === "omega_served" && inputPrice && outputPrice ? (
        <Box>
          <Typography variant="caption">
            Published token rate: {inputPrice} input / {outputPrice} output per
            million tokens.
          </Typography>
          {price(rate.cachedInputPer1MUsd) && price(rate.cacheWritePer1MUsd) ? (
            <Typography variant="caption" display="block">
              Cache rates per million tokens: {price(rate.cachedInputPer1MUsd)}{" "}
              read / {price(rate.cacheWritePer1MUsd)} write.
            </Typography>
          ) : null}
          {typeof rate.rateCardVersion === "string" ? (
            <Typography variant="caption" display="block">
              Rate card: {rate.rateCardVersion}
            </Typography>
          ) : null}
          {rate.caveat ? (
            <Typography variant="caption" display="block">
              {rate.caveat}
            </Typography>
          ) : null}
        </Box>
      ) : null}
    </Stack>
  );
}
AgentModelField.propTypes = {
  draft: PropTypes.shape({
    modelId: PropTypes.string,
    savedModelId: PropTypes.string,
  }).isRequired,
  selection: PropTypes.shape({
    selected: PropTypes.object,
    models: PropTypes.arrayOf(PropTypes.object),
    defaultModelId: PropTypes.string,
    available: PropTypes.bool,
    loading: PropTypes.bool,
    error: PropTypes.string,
    reload: PropTypes.func,
  }),
  onChange: PropTypes.func.isRequired,
  disabled: PropTypes.bool,
  isEdit: PropTypes.bool,
};
