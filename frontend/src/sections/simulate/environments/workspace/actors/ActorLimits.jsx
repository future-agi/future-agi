import PropTypes from "prop-types";
import { useState } from "react";
import {
  Alert,
  Box,
  Button,
  MenuItem,
  Stack,
  TextField,
  Typography,
} from "@mui/material";
import SectionCard from "../../components/SectionCard";
import { refusalText } from "../evals/refusalText";
import { ACTORS_COPY } from "./actors.constants";

const L = ACTORS_COPY.limits;
const NUMBERS = [
  "max_active",
  "caller_takes_back_after_turns",
  "actor_seconds_per_call",
  "max_messages",
  "live_turns_per_actor",
  "live_turns_per_trial",
];

// What the form shows for a limit: its number, "" for the channel's default, "none" for no limit.
const shown = (limits, key) => {
  if (!(key in (limits || {}))) return "";
  return limits[key] === null ? L.none : String(limits[key]);
};

/**
 * The environment's actor limits and the model actors are played on, edited in its draft. Every limit is a setting:
 * empty is the channel's default, "none" is no limit; a scenario can set its own.
 */
export default function ActorLimits({
  limits,
  models,
  locked,
  busy,
  error,
  onSave,
}) {
  const [fields, setFields] = useState({});
  const value = (key) =>
    key in fields
      ? fields[key]
      : key === "model"
        ? limits?.model || ""
        : shown(limits, key);
  const put = (key) => (event) =>
    setFields((current) => ({ ...current, [key]: event.target.value }));
  const parsed = {};
  const problems = [];
  for (const key of NUMBERS) {
    const typed = String(value(key)).trim();
    if (typed === "") continue;
    if (typed.toLowerCase() === L.none) parsed[key] = null;
    else if (
      Number(typed) > 0 &&
      (key === "actor_seconds_per_call" || Number.isInteger(Number(typed)))
    )
      parsed[key] = Number(typed);
    else problems.push(L.labels[key]);
  }
  if (value("model")) parsed.model = value("model");
  const changed = Object.keys(fields).length > 0;
  return (
    <SectionCard title={L.title} subtitle={L.subtitle}>
      <Stack spacing={1.5} sx={{ maxWidth: 520, px: 1.5, py: 1.25 }}>
        {NUMBERS.map((key) => (
          <TextField
            key={key}
            size="small"
            label={L.labels[key]}
            value={value(key)}
            onChange={put(key)}
            disabled={locked}
            placeholder={L.defaults[key]}
            error={problems.includes(L.labels[key])}
            helperText={
              problems.includes(L.labels[key])
                ? key === "actor_seconds_per_call"
                  ? L.badNumber
                  : L.badWholeNumber
                : undefined
            }
          />
        ))}
        {models?.length > 0 && (
          <TextField
            select
            size="small"
            label={L.model}
            value={value("model")}
            onChange={put("model")}
            disabled={locked}
            SelectProps={{ displayEmpty: true }}
            helperText={L.modelHelp}
          >
            <MenuItem value="">{L.callerModel}</MenuItem>
            {models.map((one) => (
              <MenuItem key={one} value={one}>
                {one}
              </MenuItem>
            ))}
          </TextField>
        )}
        {error && (
          <Alert severity="error">{refusalText(error, L.saveFailed)}</Alert>
        )}
        <Box>
          <Button
            size="small"
            variant="outlined"
            disabled={locked || busy || !changed || problems.length > 0}
            onClick={() => onSave(parsed, () => setFields({}))}
          >
            {L.save}
          </Button>
        </Box>
        <Typography sx={{ typography: "s3", color: "text.secondary" }}>
          {L.scenarioOverride}
        </Typography>
      </Stack>
    </SectionCard>
  );
}

ActorLimits.propTypes = {
  limits: PropTypes.object,
  models: PropTypes.arrayOf(PropTypes.string),
  locked: PropTypes.bool,
  busy: PropTypes.bool,
  error: PropTypes.object,
  onSave: PropTypes.func.isRequired,
};
