import {
  Alert,
  Box,
  Button,
  Checkbox,
  FormControlLabel,
  IconButton,
  TextField,
  Typography,
} from "@mui/material";
import PropTypes from "prop-types";
import Iconify from "src/components/iconify";

export default function JevOutputConfig({
  outputType,
  choiceScores,
  mapping,
  onChange,
  errors,
  hasExtraMessages,
  disabled,
  locked,
}) {
  const levels = mapping.score?.levels || [];
  const setLevels = (next) => onChange({ ...mapping, score: { levels: next } });
  const moveLevel = (index, direction) => {
    const next = [...levels];
    [next[index], next[index + direction]] = [
      next[index + direction],
      next[index],
    ];
    setLevels(next);
  };
  return (
    <Box sx={{ display: "flex", flexDirection: "column", gap: 1.5 }}>
      <Alert severity="info">
        Jev returns typed decisions with probabilities; no written reasoning.
      </Alert>
      {locked && (
        <Alert severity="warning">
          Jev models are not included in your plan or availability is still
          being checked. Your saved selection is kept; testing is disabled.
        </Alert>
      )}
      {errors.map((error) => (
        <Alert key={error} severity="error">
          {error}
        </Alert>
      ))}
      {outputType === "pass_fail" &&
        [
          ["criteria_true", "Describe a pass (true)"],
          ["criteria_false", "Describe a fail (false)"],
        ].map(([key, label]) => (
          <TextField
            key={key}
            label={label}
            size="small"
            fullWidth
            disabled={disabled}
            value={mapping.pass?.[key] ?? ""}
            inputProps={{ maxLength: 2000 }}
            onChange={(event) =>
              onChange({
                ...mapping,
                pass: { ...mapping.pass, [key]: event.target.value },
              })
            }
            helperText="Optional"
          />
        ))}
      {outputType === "deterministic" &&
        Object.keys(choiceScores || {}).map((label) => (
          <TextField
            key={label}
            label={`Description for ${label}`}
            size="small"
            fullWidth
            disabled={disabled}
            value={mapping.choice?.descriptions?.[label] ?? ""}
            inputProps={{ maxLength: 2000 }}
            onChange={(event) =>
              onChange({
                ...mapping,
                choice: {
                  ...mapping.choice,
                  descriptions: {
                    ...mapping.choice?.descriptions,
                    [label]: event.target.value,
                  },
                },
              })
            }
            helperText="Optional"
          />
        ))}
      {outputType === "percentage" && (
        <>
          <Typography variant="body2" fontWeight={600}>
            Ordered levels
          </Typography>
          {levels.map((level, index) => (
            <Box
              key={index}
              sx={{ display: "flex", alignItems: "center", gap: 1 }}
            >
              <TextField
                label={`Level ${index}`}
                size="small"
                fullWidth
                disabled={disabled}
                value={level}
                inputProps={{ maxLength: 2000 }}
                onChange={(event) =>
                  setLevels(
                    levels.map((value, i) =>
                      i === index ? event.target.value : value,
                    ),
                  )
                }
              />
              <IconButton
                aria-label={`Move level ${index} up`}
                disabled={disabled || index === 0}
                onClick={() => moveLevel(index, -1)}
              >
                <Iconify icon="mdi:arrow-up" />
              </IconButton>
              <IconButton
                aria-label={`Move level ${index} down`}
                disabled={disabled || index === levels.length - 1}
                onClick={() => moveLevel(index, 1)}
              >
                <Iconify icon="mdi:arrow-down" />
              </IconButton>
              <IconButton
                aria-label={`Remove level ${index}`}
                disabled={disabled}
                onClick={() => setLevels(levels.filter((_, i) => i !== index))}
              >
                <Iconify icon="mdi:close" />
              </IconButton>
            </Box>
          ))}
          <Button
            size="small"
            onClick={() => setLevels([...levels, ""])}
            disabled={disabled || levels.length >= 10}
            sx={{ alignSelf: "flex-start" }}
          >
            Add level
          </Button>
          <Typography variant="caption" color="text.secondary">
            {levels.length} / 10 levels · minimum 2. Normalized score = raw
            score / (level count − 1).
          </Typography>
        </>
      )}
      {hasExtraMessages && (
        <FormControlLabel
          control={
            <Checkbox
              checked={mapping.include_messages === true}
              disabled={disabled}
              onChange={(event) =>
                onChange({ ...mapping, include_messages: event.target.checked })
              }
            />
          }
          label="Include extra messages as instructions (chat roles are not preserved)"
        />
      )}
    </Box>
  );
}
JevOutputConfig.propTypes = {
  outputType: PropTypes.string.isRequired,
  choiceScores: PropTypes.object,
  mapping: PropTypes.object.isRequired,
  onChange: PropTypes.func.isRequired,
  errors: PropTypes.arrayOf(PropTypes.string).isRequired,
  hasExtraMessages: PropTypes.bool,
  disabled: PropTypes.bool,
  locked: PropTypes.bool,
};
