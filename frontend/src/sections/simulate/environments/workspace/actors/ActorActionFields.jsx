import PropTypes from "prop-types";
import {
  Alert,
  Autocomplete,
  Box,
  Checkbox,
  FormControlLabel,
  TextField,
  Typography,
} from "@mui/material";
import ActorField from "./ActorField";
import { ACTORS_COPY } from "./actors.constants";
import { ACTOR_FORM_SHAPE, OPTIONS_SHAPE } from "./actors.shapes";

const F = ACTORS_COPY.worldActors.form;

export default function ActorActionFields({
  form,
  actions,
  unknownActions,
  conflicts,
  shareConfirmed,
  onShare,
  plays,
}) {
  const permitted = form.value("permitted");
  const problem = form.problems.permitted;
  const describe = (id) =>
    actions.find((one) => one.action === id)?.description || "";
  return (
    <>
      <Autocomplete
        multiple
        size="small"
        options={actions.map((one) => one.action)}
        value={permitted}
        disabled={actions.length === 0 && permitted.length === 0}
        onChange={(_, next) => form.put("permitted", next)}
        getOptionLabel={(id) => id}
        renderOption={(props, id) => (
          <li {...props} key={id}>
            <Box>
              <Typography sx={{ typography: "s2" }}>{id}</Typography>
              <Typography sx={{ typography: "s3", color: "text.secondary" }}>
                {describe(id)}
              </Typography>
            </Box>
          </li>
        )}
        renderInput={(params) => (
          <TextField
            {...params}
            label={F.permitted}
            error={unknownActions.length > 0 || Boolean(problem)}
            helperText={
              problem ||
              (unknownActions.length
                ? F.permittedUnknown(unknownActions)
                : actions.length
                  ? F.permittedHelp
                  : F.noWorldActions)
            }
          />
        )}
      />
      {conflicts.length > 0 && (
        <Alert severity="warning" data-testid="actor-permission-conflict">
          {conflicts.map((one) => (
            <Typography key={one.action} sx={{ typography: "s2" }}>
              {F.permittedConflict(one.action, one.holder_name)}
            </Typography>
          ))}
          <FormControlLabel
            control={
              <Checkbox
                size="small"
                checked={shareConfirmed}
                onChange={(event) => onShare(event.target.checked)}
              />
            }
            label={F.permittedShare}
          />
        </Alert>
      )}
      <ActorField
        form={form}
        name="play"
        label={F.play}
        options={plays}
        helperText={F.playHelp}
      />
    </>
  );
}

ActorActionFields.propTypes = {
  form: ACTOR_FORM_SHAPE.isRequired,
  actions: PropTypes.arrayOf(
    PropTypes.shape({
      action: PropTypes.string,
      tool: PropTypes.string,
      description: PropTypes.string,
    }),
  ).isRequired,
  // Held actions the world no longer has.
  unknownActions: PropTypes.arrayOf(PropTypes.string).isRequired,
  // Actions only one other actor holds.
  conflicts: PropTypes.arrayOf(
    PropTypes.shape({
      action: PropTypes.string,
      holder_name: PropTypes.string,
    }),
  ).isRequired,
  shareConfirmed: PropTypes.bool,
  // (checked)
  onShare: PropTypes.func.isRequired,
  plays: OPTIONS_SHAPE.isRequired,
};
