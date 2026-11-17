import PropTypes from "prop-types";
import { Button, MenuItem, Stack, TextField, Typography } from "@mui/material";
import ModelSelector from "../components/ModelSelector";

export default function JurySeatEditor({
  seat,
  childrenList,
  onChange,
  onRemove,
  disabled,
}) {
  const patch = (value) => onChange({ ...seat, ...value });
  return (
    <Stack
      component="fieldset"
      spacing={1.5}
      sx={{
        border: 1,
        borderColor: "divider",
        borderRadius: 1,
        p: 2,
        minWidth: 0,
      }}
      disabled={disabled}
    >
      <Typography component="legend" variant="subtitle2">
        {seat.id}
      </Typography>
      {seat.kind === "check" ? (
        <TextField
          label="Check key"
          value={seat.check_key}
          onChange={(event) => patch({ check_key: event.target.value })}
          helperText="Read one boolean passed value from the mapped checks list. No judge call."
          size="small"
        />
      ) : (
        <>
          <TextField
            select
            label="Evaluation"
            size="small"
            value={seat.child_id}
            onChange={(event) => patch({ child_id: event.target.value })}
          >
            {childrenList.map((child) => (
              <MenuItem key={child.child_id} value={child.child_id}>
                {child.child_name}
              </MenuItem>
            ))}
          </TextField>
          <Stack direction="row" alignItems="center" gap={1} flexWrap="wrap">
            <Typography variant="body2">Judge model</Typography>
            <ModelSelector
              model={seat.model || ""}
              onModelChange={(model) => patch({ model: model || "" })}
              disabled={disabled}
              showMode={false}
              showPlus={false}
              hideDatasetContextToggle
            />
            <Typography variant="caption" color="text.secondary">
              Leave unset to use the child’s saved model.
            </Typography>
          </Stack>
          <TextField
            select
            label="Level"
            size="small"
            value={seat.level}
            onChange={(event) => {
              if (event.target.value === "step")
                patch({ level: "step", step_kind: "llm", reduce: "all_pass" });
              else {
                const { step_kind, reduce, ...row } = seat;
                onChange({ ...row, level: "row" });
              }
            }}
          >
            <MenuItem value="row">Whole row</MenuItem>
            <MenuItem value="step">Each step</MenuItem>
          </TextField>
          {seat.level === "step" && (
            <Stack direction={{ xs: "column", sm: "row" }} gap={1}>
              <TextField
                select
                label="Step kind"
                size="small"
                fullWidth
                value={seat.step_kind}
                onChange={(event) => patch({ step_kind: event.target.value })}
              >
                <MenuItem value="llm">LLM steps</MenuItem>
                <MenuItem value="tool">Tool steps</MenuItem>
              </TextField>
              <TextField
                select
                label="Step reducer"
                size="small"
                fullWidth
                value={seat.reduce}
                onChange={(event) => patch({ reduce: event.target.value })}
              >
                <MenuItem value="all_pass">All pass</MenuItem>
                <MenuItem value="mean">Mean</MenuItem>
                <MenuItem value="min">Lowest</MenuItem>
              </TextField>
            </Stack>
          )}
        </>
      )}
      <TextField
        label="Seat weight"
        size="small"
        type="number"
        value={seat.weight}
        inputProps={{ min: 0.001, max: 10, step: 0.1 }}
        onChange={(event) => patch({ weight: Number(event.target.value) })}
      />
      <Button
        onClick={onRemove}
        disabled={disabled}
        sx={{ alignSelf: "flex-start" }}
      >
        Remove seat
      </Button>
    </Stack>
  );
}
JurySeatEditor.propTypes = {
  seat: PropTypes.object.isRequired,
  childrenList: PropTypes.array.isRequired,
  onChange: PropTypes.func.isRequired,
  onRemove: PropTypes.func.isRequired,
  disabled: PropTypes.bool,
};
