import PropTypes from "prop-types";
import {
  Box,
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableRow,
} from "@mui/material";

const printable = (value) =>
  value == null
    ? "—"
    : new Intl.NumberFormat(undefined, { maximumFractionDigits: 6 }).format(
        value,
      );

export default function MetricResults({ results, rollup = "overall" }) {
  return (
    <Box sx={{ overflowX: "auto" }}>
      <Table size="small" aria-label="Metric results">
        <TableHead>
          <TableRow>
            {[
              "Group",
              "Value",
              "Numerator",
              "Denominator",
              "Status",
              "Units",
              "Valid",
              "Missing",
              "Invalid probability",
            ].map((label) => (
              <TableCell key={label}>{label}</TableCell>
            ))}
          </TableRow>
        </TableHead>
        <TableBody>
          {results.map((row) => (
            <TableRow key={row.group_key}>
              <TableCell>{groupLabel(row, rollup)}</TableCell>
              {[row.value, row.numerator, row.denominator].map(
                (value, index) => (
                  <TableCell key={index}>{printable(value)}</TableCell>
                ),
              )}
              <TableCell>{row.status.replaceAll("_", " ")}</TableCell>
              {[
                row.unit_count,
                row.valid_count,
                row.missing_count,
                row.invalid_probability_count,
              ].map((value, index) => (
                <TableCell key={index}>{value}</TableCell>
              ))}
            </TableRow>
          ))}
        </TableBody>
      </Table>
      <MetricUncertainty results={results} rollup={rollup} />
    </Box>
  );
}

const groupLabel = (row, rollup) =>
  row.group_key || (rollup === "overall" ? "All units" : "No cohort");

const METHOD_LABELS = {
  stratified_wor_fpc: "Stratified sample (finite population)",
  poisson: "Live sampling (Poisson)",
  iid_units: "Unweighted units (model-based)",
};

// A separate table so the persisted value table keeps its exact columns. The
// interval covers sampling error only; "not estimable" is shown with its reason.
function MetricUncertainty({ results, rollup }) {
  const rows = results.filter((row) => row.uncertainty);
  if (!rows.length) return null;
  return (
    <Table size="small" aria-label="Metric uncertainty" sx={{ mt: 1 }}>
      <TableHead>
        <TableRow>
          {[
            "Group",
            "95% interval",
            "Standard error",
            "Effective n",
            "Method",
          ].map((label) => (
            <TableCell key={label}>{label}</TableCell>
          ))}
        </TableRow>
      </TableHead>
      <TableBody>
        {rows.map(({ group_key: key, uncertainty: ci, ...row }) => (
          <TableRow key={key}>
            <TableCell>
              {groupLabel({ group_key: key, ...row }, rollup)}
            </TableCell>
            <TableCell>
              {ci.status === "ok"
                ? `${printable(ci.ci_low)} – ${printable(ci.ci_high)}`
                : `Not estimable: ${ci.reason.replaceAll("_", " ")}`}
            </TableCell>
            <TableCell>{printable(ci.std_error)}</TableCell>
            <TableCell>{printable(ci.effective_n)}</TableCell>
            <TableCell>
              {[METHOD_LABELS[ci.method], ci.interval === "wilson" && "Wilson"]
                .filter(Boolean)
                .join(" · ") || "—"}
            </TableCell>
          </TableRow>
        ))}
      </TableBody>
    </Table>
  );
}

const uncertaintyShape = PropTypes.shape({
  status: PropTypes.string.isRequired,
  reason: PropTypes.string.isRequired,
  method: PropTypes.string.isRequired,
  interval: PropTypes.string,
  std_error: PropTypes.number,
  ci_low: PropTypes.number,
  ci_high: PropTypes.number,
  effective_n: PropTypes.number,
});

MetricUncertainty.propTypes = {
  rollup: PropTypes.string.isRequired,
  results: PropTypes.arrayOf(
    PropTypes.shape({
      group_key: PropTypes.string.isRequired,
      uncertainty: uncertaintyShape,
    }),
  ).isRequired,
};

MetricResults.propTypes = {
  rollup: PropTypes.string,
  results: PropTypes.arrayOf(
    PropTypes.shape({
      group_key: PropTypes.string.isRequired,
      value: PropTypes.number,
      numerator: PropTypes.number,
      denominator: PropTypes.number,
      status: PropTypes.string.isRequired,
      unit_count: PropTypes.number.isRequired,
      valid_count: PropTypes.number.isRequired,
      missing_count: PropTypes.number.isRequired,
      invalid_probability_count: PropTypes.number.isRequired,
      uncertainty: uncertaintyShape,
    }),
  ).isRequired,
};
