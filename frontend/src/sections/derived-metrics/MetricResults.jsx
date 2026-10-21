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
              <TableCell>
                {row.group_key ||
                  (rollup === "overall" ? "All units" : "No cohort")}
              </TableCell>
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
    </Box>
  );
}

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
    }),
  ).isRequired,
};
