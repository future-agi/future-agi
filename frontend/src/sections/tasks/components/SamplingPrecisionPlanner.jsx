import React, { useState } from "react";
import PropTypes from "prop-types";
import {
  Alert,
  Box,
  Button,
  MenuItem,
  Stack,
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableRow,
  TextField,
  Typography,
} from "@mui/material";
import {
  allocate,
  marginOfError,
  requiredSampleSize,
  stratifiedMarginOfError,
} from "./samplingStats";
import { samplingStratumLabel } from "./samplingPolicyForm";
import { applyCellQuotas } from "./samplingPrecisionPlan";

const marginLabel = (value) =>
  value == null ? "Not estimable" : `±${(value * 100).toFixed(2)} pp`;
const numeric = (value) => (value === "" ? NaN : Number(value));

// Advisory only: this panel never writes to the form until the explicit Apply
// action, and it is unmounted when the policy or preview population changes.
export default function SamplingPrecisionPlanner({ policy, preview, onApply }) {
  const cells = preview.strata;
  const [margin, setMargin] = useState("5");
  const [budget, setBudget] = useState(
    String(cells.reduce((sum, cell) => sum + cell.selected_units, 0)),
  );
  const [minimum, setMinimum] = useState("1");
  const [method, setMethod] = useState("proportional");
  const [anticipated, setAnticipated] = useState(cells.map(() => "50"));
  const [suggested, setSuggested] = useState(null);
  const [error, setError] = useState("");
  const change = (setter, value) => {
    setter(value);
    setSuggested(null);
    setError("");
  };
  const validMargin = numeric(margin) > 0 && numeric(margin) < 100;
  let application = null;
  let applicationError = "";
  if (suggested) {
    try {
      application = applyCellQuotas(policy, cells, suggested);
    } catch (err) {
      applicationError = err.message;
    }
  }
  const suggest = () => {
    try {
      setSuggested(
        allocate(
          cells.map((cell) => cell.eligible_units),
          numeric(budget),
          method,
          numeric(minimum),
          anticipated.map((value) => numeric(value) / 100),
        ),
      );
      setError("");
    } catch (err) {
      setSuggested(null);
      setError(err.message);
    }
  };
  if (!cells.length) return null;
  return (
    <Box
      component="details"
      sx={{ mt: 2, borderTop: "1px solid", borderColor: "divider", pt: 1.5 }}
    >
      <Typography
        component="summary"
        variant="subtitle2"
        sx={{ cursor: "pointer" }}
      >
        Precision planner · advisory
      </Typography>
      <Stack
        spacing={1.5}
        sx={{ pt: 1.5 }}
        aria-label="Sampling precision planner"
      >
        <Typography variant="body2" color="text.secondary">
          Plan a proportion over sampled units, not evaluation rows. These are
          approximate 95% normal margins with finite-population correction and
          an assumed proportion of 50%, not confidence intervals for observed
          outcomes. Small samples may not support the normal approximation.
        </Typography>
        {preview.precision && (
          <Typography variant="body2">
            Current stratified margin:{" "}
            {marginLabel(preview.precision.stratified_margin_of_error)}.
            {preview.precision.unsampled_strata > 0 &&
              ` ${preview.precision.unsampled_strata} non-empty cell(s) have no sample.`}
          </Typography>
        )}
        <TextField
          label="Requested margin (percentage points)"
          type="number"
          size="small"
          value={margin}
          onChange={(e) => setMargin(e.target.value)}
          error={!validMargin}
          helperText={
            !validMargin
              ? "Use a value greater than 0 and less than 100."
              : "Minimum n below uses 50% and 95% confidence in each cell."
          }
          inputProps={{ min: 0, max: 100 }}
        />
        {policy.mode === "closed" ? (
          <>
            <Stack direction={{ xs: "column", sm: "row" }} spacing={1}>
              <TextField
                select
                label="Allocation method"
                size="small"
                value={method}
                onChange={(e) => change(setMethod, e.target.value)}
                sx={{ minWidth: 170 }}
              >
                <MenuItem value="proportional">Proportional</MenuItem>
                <MenuItem value="balanced">Balanced</MenuItem>
                <MenuItem value="neyman">Neyman</MenuItem>
              </TextField>
              <TextField
                label="Total unit budget"
                type="number"
                size="small"
                value={budget}
                onChange={(e) => change(setBudget, e.target.value)}
                inputProps={{ min: 0, step: 1 }}
              />
              <TextField
                label="Per-cell minimum"
                type="number"
                size="small"
                value={minimum}
                onChange={(e) => change(setMinimum, e.target.value)}
                inputProps={{ min: 0, step: 1 }}
              />
            </Stack>
            {method === "neyman" && (
              <Typography variant="caption">
                Neyman uses N × √(p(1 − p)). Set anticipated proportions below;
                equal proportions give proportional allocation. Zero variance
                receives only the minimum.
              </Typography>
            )}
            <Button variant="outlined" onClick={suggest}>
              Suggest quotas
            </Button>
          </>
        ) : (
          <Typography variant="body2">
            Live selections are random counts. These margins describe this
            preview only; exact quotas can be applied only in closed mode.
          </Typography>
        )}
        <Box sx={{ overflowX: "auto" }}>
          <Table size="small" aria-label="Precision planning by cell">
            <TableHead>
              <TableRow>
                {[
                  "Cell",
                  "Population N",
                  "Current n",
                  "Current 95% margin",
                  "Minimum n for margin",
                  ...(method === "neyman" ? ["Anticipated proportion"] : []),
                  ...(suggested ? ["Suggested n", "Planned 95% margin"] : []),
                ].map((text) => (
                  <TableCell key={text}>{text}</TableCell>
                ))}
              </TableRow>
            </TableHead>
            <TableBody>
              {cells.map((cell, i) => (
                <TableRow key={cell.stratum}>
                  <TableCell>{samplingStratumLabel(cell.stratum)}</TableCell>
                  <TableCell>{cell.eligible_units}</TableCell>
                  <TableCell>{cell.selected_units}</TableCell>
                  <TableCell>
                    {marginLabel(
                      marginOfError(cell.selected_units, cell.eligible_units),
                    )}
                  </TableCell>
                  <TableCell>
                    {validMargin
                      ? requiredSampleSize(
                          numeric(margin) / 100,
                          cell.eligible_units,
                        )
                      : "—"}
                  </TableCell>
                  {method === "neyman" && (
                    <TableCell>
                      <TextField
                        size="small"
                        type="number"
                        label={`Anticipated proportion (%) for cell ${i + 1}`}
                        value={anticipated[i]}
                        onChange={(e) =>
                          change(
                            setAnticipated,
                            anticipated.map((value, j) =>
                              i === j ? e.target.value : value,
                            ),
                          )
                        }
                        inputProps={{ min: 0, max: 100 }}
                      />
                    </TableCell>
                  )}
                  {suggested && (
                    <>
                      <TableCell>{suggested[i]}</TableCell>
                      <TableCell>
                        {marginLabel(
                          marginOfError(suggested[i], cell.eligible_units),
                        )}
                      </TableCell>
                    </>
                  )}
                </TableRow>
              ))}
            </TableBody>
          </Table>
        </Box>
        {suggested && (
          <>
            <Typography variant="body2">
              Suggested total: {suggested.reduce((sum, n) => sum + n, 0)} units.
              Conservative stratified margin:{" "}
              {marginLabel(
                stratifiedMarginOfError(
                  cells.map((cell, i) => [
                    cell.eligible_units,
                    suggested[i],
                    0.5,
                  ]),
                ),
              )}
              .
            </Typography>
            <Typography variant="caption" color="text.secondary">
              Applying replaces rate, floor, cap and census for these preview
              cells only. New or unlisted cells keep their original rules. The
              population is not reserved: preview again before saving. No policy
              changes until you apply.
            </Typography>
            <Button
              variant="outlined"
              disabled={!application}
              onClick={() => onApply(application)}
            >
              Apply suggested quotas
            </Button>
          </>
        )}
        {(error || applicationError) && (
          <Alert severity="warning">{error || applicationError}</Alert>
        )}
      </Stack>
    </Box>
  );
}
SamplingPrecisionPlanner.propTypes = {
  policy: PropTypes.object.isRequired,
  preview: PropTypes.object.isRequired,
  onApply: PropTypes.func.isRequired,
};
