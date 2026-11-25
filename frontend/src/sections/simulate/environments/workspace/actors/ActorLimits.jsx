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
import { words } from "./actorUtils";

const L = ACTORS_COPY.limits;

// "" is the channel's default, "none" is no limit.
const shown = (limits, key) => {
  if (!(key in limits)) return "";
  return limits[key] === null ? L.none : String(limits[key]);
};

// Non-numbers go as typed so the server's refusal names what is wrong.
const asLimit = (typed) => {
  if (typed.toLowerCase() === L.none) return null;
  const number = Number(typed);
  return Number.isFinite(number) ? number : typed;
};

export default function ActorLimits({
  limits,
  fields: limitFields,
  models,
  locked,
  busy,
  error,
  onSave,
}) {
  const [fields, setFields] = useState({});
  const numbers = limitFields.filter((one) => one.kind !== "model");
  const hasModel = limitFields.some((one) => one.kind === "model");
  const value = (key) =>
    key in fields
      ? fields[key]
      : key === "model"
        ? limits.model ?? ""
        : shown(limits, key);
  const put = (key) => (event) =>
    setFields((current) => ({ ...current, [key]: event.target.value }));
  const parsed = {};
  for (const { key } of numbers) {
    const typed = String(value(key)).trim();
    if (typed !== "") parsed[key] = asLimit(typed);
  }
  if (value("model")) parsed.model = value("model");
  const changed = Object.keys(fields).length > 0;
  const problems = error?.error === "limits_invalid" ? error.problems : [];
  const problemOf = (key) =>
    problems
      .filter((one) => one.field === key)
      .map((one) => one.message)
      .join(" ");
  const keys = new Set(limitFields.map((one) => one.key));
  const unplaced = problems.filter((one) => !keys.has(one.field));
  return (
    <SectionCard title={L.title} subtitle={L.subtitle}>
      <Stack spacing={1.5} sx={{ maxWidth: 520, px: 1.5, py: 1.25 }}>
        {numbers.map(({ key, kind, default_label: defaultLabel }) => (
          <TextField
            key={key}
            size="small"
            label={L.labels[key] || words(key)}
            value={value(key)}
            onChange={put(key)}
            disabled={locked}
            placeholder={defaultLabel || undefined}
            error={Boolean(problemOf(key))}
            helperText={problemOf(key) || undefined}
            inputProps={{ inputMode: kind === "whole" ? "numeric" : "decimal" }}
          />
        ))}
        {hasModel && models.length > 0 && (
          <TextField
            select
            size="small"
            label={L.model}
            value={value("model")}
            onChange={put("model")}
            disabled={locked}
            SelectProps={{ displayEmpty: true }}
            error={Boolean(problemOf("model"))}
            helperText={problemOf("model") || L.modelHelp}
          >
            <MenuItem value="">{L.callerModel}</MenuItem>
            {models.map((one) => (
              <MenuItem key={one} value={one}>
                {one}
              </MenuItem>
            ))}
          </TextField>
        )}
        {error && problems.length === 0 && (
          <Alert severity="error">{refusalText(error, L.saveFailed)}</Alert>
        )}
        {unplaced.length > 0 && (
          <Alert severity="error">
            {unplaced.map((one) => one.message).join(" ")}
          </Alert>
        )}
        <Box>
          <Button
            size="small"
            variant="outlined"
            disabled={locked || busy || !changed}
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
  // Null is no limit; a missing one takes the channel's default.
  limits: PropTypes.object.isRequired,
  fields: PropTypes.arrayOf(
    PropTypes.shape({
      key: PropTypes.string.isRequired,
      kind: PropTypes.oneOf(["whole", "seconds", "model"]).isRequired,
      default_label: PropTypes.string,
    }),
  ).isRequired,
  models: PropTypes.arrayOf(PropTypes.string),
  locked: PropTypes.bool,
  busy: PropTypes.bool,
  error: PropTypes.object,
  onSave: PropTypes.func.isRequired,
};
