import React, { useEffect, useId, useState } from "react";
import PropTypes from "prop-types";
import {
  Alert,
  Box,
  Button,
  Dialog,
  DialogActions,
  DialogContent,
  DialogTitle,
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
  const groups = preview.strata;
  const [open, setOpen] = useState(false);
  const [margin, setMargin] = useState("5");
  const [budget, setBudget] = useState(
    String(groups.reduce((sum, group) => sum + group.selected_units, 0)),
  );
  const [minimum, setMinimum] = useState("1");
  const [method, setMethod] = useState("proportional");
  const [anticipated, setAnticipated] = useState(groups.map(() => "50"));
  const [suggested, setSuggested] = useState(null);
  const [error, setError] = useState("");
  const change = (setter, value) => {
    setter(value);
    setSuggested(null);
    setError("");
  };
  const validMargin = numeric(margin) > 0 && numeric(margin) < 100;
  const advisoryId = useId();
  const scrollHintId = useId();
  const [resultsRegion, setResultsRegion] = useState(null);
  const [resultsOverflow, setResultsOverflow] = useState(false);
  useEffect(() => {
    const region = resultsRegion;
    if (!region) return undefined;
    const checkOverflow = () =>
      setResultsOverflow(region.scrollWidth > region.clientWidth);
    const observer =
      typeof ResizeObserver === "undefined"
        ? null
        : new ResizeObserver(checkOverflow);
    observer?.observe(region);
    observer?.observe(region.querySelector("table"));
    const details = region.closest("details");
    details?.addEventListener("toggle", checkOverflow);
    window.addEventListener("resize", checkOverflow);
    checkOverflow();
    return () => {
      observer?.disconnect();
      details?.removeEventListener("toggle", checkOverflow);
      window.removeEventListener("resize", checkOverflow);
    };
  }, [groups, method, suggested, resultsRegion]);
  const unmetCells =
    suggested && validMargin
      ? groups
          .map((group, index) => ({
            label: samplingStratumLabel(group.stratum),
            required: requiredSampleSize(
              numeric(margin) / 100,
              group.eligible_units,
            ),
            suggested: suggested[index],
          }))
          .filter((group) => group.suggested < group.required)
      : [];
  const goalAdvisory = unmetCells.length
    ? groups.length === 1
      ? `This budget does not meet the requested ${numeric(margin)} pp margin; this group needs ${unmetCells[0].required} units.`
      : `This budget does not meet the requested ${numeric(margin)} pp margin in ${unmetCells.length} groups. Minimum units needed by group: ${unmetCells.map((group) => `${group.label}: ${group.required}`).join("; ")}.`
    : "";
  let application = null;
  let applicationError = "";
  if (suggested) {
    try {
      application = applyCellQuotas(policy, groups, suggested);
    } catch (err) {
      applicationError = err.message;
    }
  }
  const suggest = () => {
    try {
      setSuggested(
        allocate(
          groups.map((group) => group.eligible_units),
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
  if (!groups.length) return null;
  return (
    <>
      <Button variant="outlined" onClick={() => setOpen(true)}>
        Plan sample size
      </Button>
      <Dialog
        open={open}
        onClose={() => setOpen(false)}
        maxWidth="lg"
        fullWidth
        aria-labelledby="sample-planner-title"
        PaperProps={{ sx: { maxWidth: 960 } }}
      >
        <DialogTitle id="sample-planner-title">
          Plan sample size · advisory
        </DialogTitle>
        <DialogContent>
          <Stack
            spacing={1.5}
            sx={{ pt: 1.5 }}
            aria-label="Sampling precision planner"
          >
            <Typography variant="body2" color="text.secondary">
              Plan a proportion over sampled units, not evaluation rows. These
              are approximate 95% normal margins with finite-population
              correction and an assumed proportion of 50%, not confidence
              intervals for observed outcomes. Small samples may not support the
              normal approximation.
            </Typography>
            {preview.precision && (
              <Typography variant="body2">
                Current stratified margin:{" "}
                {marginLabel(preview.precision.stratified_margin_of_error)}.
                {preview.precision.unsampled_strata > 0 &&
                  ` ${preview.precision.unsampled_strata} non-empty group(s) have no sample.`}
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
                  : "Minimum n below uses 50% and 95% confidence in each group."
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
                    label="Per-group minimum"
                    type="number"
                    size="small"
                    value={minimum}
                    onChange={(e) => change(setMinimum, e.target.value)}
                    inputProps={{ min: 0, step: 1 }}
                  />
                </Stack>
                {method === "neyman" && (
                  <Typography variant="caption">
                    Neyman uses N × √(p(1 − p)). Set anticipated proportions
                    below; equal proportions give proportional allocation. Zero
                    variance receives only the minimum.
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
            {resultsOverflow && (
              <Typography
                id={scrollHintId}
                variant="caption"
                color="text.secondary"
              >
                Scroll horizontally to see all planner columns.
              </Typography>
            )}
            <Box
              ref={setResultsRegion}
              role="region"
              aria-label="Planner results, scrolls horizontally"
              aria-describedby={resultsOverflow ? scrollHintId : undefined}
              tabIndex={0}
              sx={{
                overflowX: "auto",
                "&:focus-visible": {
                  outline: (theme) => `2px solid ${theme.palette.text.primary}`,
                  outlineOffset: 2,
                },
              }}
            >
              <Table size="small" aria-label="Precision planning by group">
                <TableHead>
                  <TableRow>
                    {[
                      "Group",
                      "Population N",
                      "Current n",
                      "Current 95% margin",
                      "Minimum n for margin",
                      ...(method === "neyman"
                        ? ["Anticipated proportion"]
                        : []),
                      ...(suggested
                        ? ["Suggested n", "Planned 95% margin"]
                        : []),
                    ].map((text) => (
                      <TableCell key={text}>{text}</TableCell>
                    ))}
                  </TableRow>
                </TableHead>
                <TableBody>
                  {groups.map((group, i) => (
                    <TableRow key={group.stratum}>
                      <TableCell>
                        {samplingStratumLabel(group.stratum)}
                      </TableCell>
                      <TableCell>{group.eligible_units}</TableCell>
                      <TableCell>{group.selected_units}</TableCell>
                      <TableCell>
                        {marginLabel(
                          marginOfError(
                            group.selected_units,
                            group.eligible_units,
                          ),
                        )}
                      </TableCell>
                      <TableCell>
                        {validMargin
                          ? requiredSampleSize(
                              numeric(margin) / 100,
                              group.eligible_units,
                            )
                          : "—"}
                      </TableCell>
                      {method === "neyman" && (
                        <TableCell>
                          <TextField
                            size="small"
                            type="number"
                            label={`Anticipated proportion (%) for group ${i + 1}`}
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
                              marginOfError(suggested[i], group.eligible_units),
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
                  Suggested total: {suggested.reduce((sum, n) => sum + n, 0)}{" "}
                  units. Conservative stratified margin:{" "}
                  {marginLabel(
                    stratifiedMarginOfError(
                      groups.map((group, i) => [
                        group.eligible_units,
                        suggested[i],
                        0.5,
                      ]),
                    ),
                  )}
                  .
                </Typography>
                <Typography variant="caption" color="text.secondary">
                  Applying replaces rate, floor, cap and census for these
                  preview groups only. New or unlisted groups keep their
                  original rules. The population is not reserved: preview again
                  before saving. No policy changes until you apply.
                </Typography>
                {goalAdvisory && (
                  <Typography
                    id={advisoryId}
                    role="status"
                    variant="body2"
                    sx={{
                      color: "text.secondary",
                    }}
                  >
                    {goalAdvisory}
                  </Typography>
                )}
                <Button
                  variant="outlined"
                  aria-describedby={goalAdvisory ? advisoryId : undefined}
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
        </DialogContent>
        <DialogActions>
          <Button onClick={() => setOpen(false)}>Cancel</Button>
        </DialogActions>
      </Dialog>
    </>
  );
}
SamplingPrecisionPlanner.propTypes = {
  policy: PropTypes.object.isRequired,
  preview: PropTypes.object.isRequired,
  onApply: PropTypes.func.isRequired,
};
