import PropTypes from "prop-types";
import {
  Box,
  Checkbox,
  Chip,
  FormControlLabel,
  InputBase,
  MenuItem,
  Select,
  Stack,
  Typography,
} from "@mui/material";

import CustomTooltip from "src/components/tooltip";

import { formatCredits, formatMinutes } from "./fixRunView";
import { budgetSteps, marginSteps, minuteSteps } from "./launchFormView";

// The launch form's rows, shared by Run Epoch from a run and from an Error Feed
// issue: one label column, one value column, hairline between rows.

const selectSx = {
  height: 34,
  typography: "s2",
  "& .MuiSelect-select": { typography: "s2", py: 0.75 },
};

export function Row({ k, center = false, children }) {
  return (
    <Stack
      direction="row"
      spacing={1.5}
      alignItems={center ? "center" : "flex-start"}
      sx={{
        py: 1,
        minHeight: 38,
        borderTop: "1px solid",
        borderColor: "divider",
        "&:first-of-type": { borderTop: 0, pt: 0 },
      }}
    >
      <Typography
        sx={{
          typography: "s2",
          color: "text.subtitle",
          width: 110,
          flexShrink: 0,
        }}
      >
        {k}
      </Typography>
      <Box sx={{ flex: 1, minWidth: 0 }}>{children}</Box>
    </Stack>
  );
}
Row.propTypes = {
  k: PropTypes.string.isRequired,
  center: PropTypes.bool,
  children: PropTypes.node,
};

// Values typed one at a time, each added by Enter, a comma or leaving the field.
export function ChipsInput({ values, onChange, label, placeholder, testId }) {
  const add = (input) => {
    const v = input.value.trim().replace(/,$/, "").trim();
    if (!v) return;
    onChange([...new Set([...values, v])]);
    input.value = "";
  };
  return (
    <Stack
      direction="row"
      flexWrap="wrap"
      alignItems="center"
      gap={0.75}
      data-testid={testId}
    >
      {values.map((p) => (
        <Chip
          key={p}
          size="small"
          label={p}
          onDelete={() => onChange(values.filter((x) => x !== p))}
        />
      ))}
      <InputBase
        placeholder={
          values.length ? "Add another, then press Enter" : placeholder
        }
        inputProps={{ "aria-label": label }}
        onKeyDown={(e) => {
          if (e.key === "Enter") {
            e.preventDefault();
            add(e.target);
          }
        }}
        onChange={(e) => {
          if (e.target.value.endsWith(",")) add(e.target);
        }}
        onBlur={(e) => add(e.target)}
        sx={{
          flex: 1,
          minWidth: 180,
          "& .MuiInputBase-input": { typography: "s2", p: 0, height: 22 },
        }}
      />
    </Stack>
  );
}
ChipsInput.propTypes = {
  values: PropTypes.array.isRequired,
  onChange: PropTypes.func.isRequired,
  label: PropTypes.string.isRequired,
  placeholder: PropTypes.string.isRequired,
  testId: PropTypes.string,
};

// The parts of the agent an epoch may change; one the agent can't take says why.
export function SurfacesRow({ surfaces, value, onChange }) {
  return (
    <Row k="May change" center>
      <Stack direction="row" flexWrap="wrap" columnGap={2}>
        {surfaces.map((s) => (
          <CustomTooltip
            key={s.id}
            show={!s.allowed && !!s.reason}
            title={s.reason || ""}
            size="small"
            arrow
          >
            <Box component="span">
              <FormControlLabel
                label={s.label}
                disabled={!s.allowed}
                control={
                  <Checkbox
                    size="small"
                    checked={value.includes(s.id)}
                    onChange={() =>
                      onChange(
                        value.includes(s.id)
                          ? value.filter((x) => x !== s.id)
                          : [...value, s.id],
                      )
                    }
                  />
                }
                sx={{
                  mr: 0,
                  "& .MuiFormControlLabel-label": { typography: "s2" },
                }}
              />
            </Box>
          </CustomTooltip>
        ))}
      </Stack>
    </Row>
  );
}
SurfacesRow.propTypes = {
  surfaces: PropTypes.array.isRequired,
  value: PropTypes.array.isRequired,
  onChange: PropTypes.func.isRequired,
};

// Without a time limit of its own, the platform sizes one from the suite at launch.
const SUITE_SIZED = "";

// Models, Reply time, Agent's cost, Budget and Stops at. The caller owns the values;
// `modelsNote` replaces the model list when the agent's models can't be locked, and a
// null `minutes` leaves the time limit to the platform.
export default function LaunchLimits({
  limits,
  modelsNote,
  models,
  onModels,
  latencyMargin,
  onLatencyMargin,
  costMargin,
  onCostMargin,
  costLimit,
  onCostLimit,
  budget,
  onBudget,
  minutes,
  onMinutes,
  minutesFromSuite = false,
}) {
  return (
    <>
      <Row k="Models" center>
        {modelsNote ? (
          <Typography sx={{ typography: "s2", color: "text.subtitle" }}>
            {modelsNote}
          </Typography>
        ) : (
          <ChipsInput
            values={models}
            onChange={onModels}
            label="Add an allowed model"
            placeholder="The models it may call. Press Enter to add each."
            testId="allowed-models"
          />
        )}
      </Row>
      <Row k="Reply time" center>
        <Select
          size="small"
          value={latencyMargin}
          onChange={(e) => onLatencyMargin(Number(e.target.value))}
          sx={{ ...selectSx, width: 220 }}
          inputProps={{ "aria-label": "Reply time margin" }}
        >
          {marginSteps(latencyMargin).map((n) => (
            <MenuItem key={n} value={n} sx={{ typography: "s2" }}>
              {n === 0 ? "No increase" : `Up to ${n}% slower than now`}
            </MenuItem>
          ))}
        </Select>
      </Row>
      <Row k="Agent's cost" center>
        <Stack direction="row" spacing={1.5} alignItems="center">
          <Select
            size="small"
            value={costMargin}
            onChange={(e) => onCostMargin(Number(e.target.value))}
            sx={{ ...selectSx, width: 220 }}
            inputProps={{ "aria-label": "Agent's cost margin" }}
          >
            {marginSteps(costMargin).map((n) => (
              <MenuItem key={n} value={n} sx={{ typography: "s2" }}>
                {n === 0 ? "No increase" : `Up to ${n}% more than now`}
              </MenuItem>
            ))}
          </Select>
          <Typography sx={{ typography: "s2", color: "text.subtitle" }}>
            and at most $
          </Typography>
          <InputBase
            value={costLimit}
            onChange={(e) => onCostLimit(e.target.value)}
            placeholder="no limit"
            inputProps={{
              "aria-label": "Agent's cost limit per conversation",
              inputMode: "decimal",
            }}
            sx={{
              width: 80,
              "& .MuiInputBase-input": {
                typography: "s2",
                p: 0,
                height: 22,
              },
            }}
          />
          <Typography sx={{ typography: "s2", color: "text.subtitle" }}>
            a conversation
          </Typography>
        </Stack>
      </Row>
      <Row k="Budget" center>
        <Stack direction="row" spacing={1.5}>
          <Select
            size="small"
            value={budget}
            onChange={(e) => onBudget(Number(e.target.value))}
            sx={{ ...selectSx, width: 220 }}
            inputProps={{ "aria-label": "Budget" }}
          >
            {budgetSteps(budget, limits).map((n) => (
              <MenuItem key={n} value={n} sx={{ typography: "s2" }}>
                {formatCredits(n)}
              </MenuItem>
            ))}
          </Select>
          <Select
            size="small"
            value={minutes ?? SUITE_SIZED}
            displayEmpty
            onChange={(e) =>
              onMinutes(
                e.target.value === SUITE_SIZED ? null : Number(e.target.value),
              )
            }
            sx={{ ...selectSx, width: 220 }}
            inputProps={{ "aria-label": "Time limit" }}
          >
            {minutesFromSuite ? (
              <MenuItem value={SUITE_SIZED} sx={{ typography: "s2" }}>
                Sized from the suite
              </MenuItem>
            ) : null}
            {minuteSteps(minutes, limits).map((n) => (
              <MenuItem key={n} value={n} sx={{ typography: "s2" }}>
                {formatMinutes(n)}
              </MenuItem>
            ))}
          </Select>
        </Stack>
      </Row>
      <Row k="Stops at">
        <Typography sx={{ typography: "s2" }}>
          {`${formatCredits(budget)} or ${
            minutes == null
              ? "a time limit sized from the suite"
              : formatMinutes(minutes)
          }, whichever comes first.`}
        </Typography>
      </Row>
    </>
  );
}

LaunchLimits.propTypes = {
  limits: PropTypes.object.isRequired,
  modelsNote: PropTypes.string,
  models: PropTypes.array.isRequired,
  onModels: PropTypes.func.isRequired,
  latencyMargin: PropTypes.number.isRequired,
  onLatencyMargin: PropTypes.func.isRequired,
  costMargin: PropTypes.number.isRequired,
  onCostMargin: PropTypes.func.isRequired,
  costLimit: PropTypes.string.isRequired,
  onCostLimit: PropTypes.func.isRequired,
  budget: PropTypes.number.isRequired,
  onBudget: PropTypes.func.isRequired,
  minutes: PropTypes.number,
  onMinutes: PropTypes.func.isRequired,
  minutesFromSuite: PropTypes.bool,
};
