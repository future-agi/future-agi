import { useState } from "react";
import PropTypes from "prop-types";
import {
  Alert,
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

const percentage = (value) =>
  value == null ? "Unavailable" : `${Math.round(value * 100)}%`;

export default function JuryDatasetReport({ report, runId, onRunChange }) {
  const [selectedRow, setSelectedRow] = useState("");
  const [onlyDisagreements, setOnlyDisagreements] = useState(false);
  const rows = report.rows.filter(
    (row) => !onlyDisagreements || row.disagreement,
  );
  const active = rows.find((row) => row.row_id === selectedRow) || rows[0];
  return (
    <Stack spacing={2} component="section" aria-label="Dataset jury report">
      <TextField
        select
        label="Dataset jury run"
        value={runId || report.run_id}
        onChange={(event) => {
          setSelectedRow("");
          onRunChange(event.target.value);
        }}
      >
        {report.runs.map((run) => (
          <MenuItem key={run.run_id} value={run.run_id}>
            {run.run_label}
          </MenuItem>
        ))}
      </TextField>
      <Typography role="status">{`Judges disagree on ${report.split_rows.length} of ${report.n} rows`}</Typography>
      {!report.complete && (
        <Alert severity="warning">
          Some rows are missing verdicts. Agreement includes only pairs with two
          scored seats.
        </Alert>
      )}
      <Table size="small" aria-label="Pairwise jury agreement">
        <TableHead>
          <TableRow>
            <TableCell>Pair</TableCell>
            <TableCell>Agreement</TableCell>
            <TableCell>Scored rows</TableCell>
          </TableRow>
        </TableHead>
        <TableBody>
          {report.pairs.map((pair) => (
            <TableRow key={`${pair.a}-${pair.b}`}>
              <TableCell>{`${pair.a} / ${pair.b}`}</TableCell>
              <TableCell>{percentage(pair.agreement)}</TableCell>
              <TableCell>{pair.n}</TableCell>
            </TableRow>
          ))}
        </TableBody>
      </Table>
      <Typography variant="body2" color="text.secondary">
        Agreement is read from saved verdicts, not re-scored. Small samples may
        not represent future runs.
      </Typography>
      <Button
        sx={{ alignSelf: "flex-start" }}
        onClick={() => {
          setOnlyDisagreements(!onlyDisagreements);
          setSelectedRow("");
        }}
      >
        {onlyDisagreements ? "Show all rows" : "Open disagreement rows"}
      </Button>
      {!rows.length ? (
        <Typography>No disagreement rows.</Typography>
      ) : (
        <>
          <TextField
            select
            label="Jury row"
            value={active.row_id}
            onChange={(event) => setSelectedRow(event.target.value)}
          >
            {rows.map((row) => (
              <MenuItem
                key={row.row_id}
                value={row.row_id}
              >{`Row ${row.row_number}${row.disagreement ? " — judges disagree" : ""}`}</MenuItem>
            ))}
          </TextField>
          <Typography variant="body2">{`Saved row ${active.row_number} · ${active.row_id}`}</Typography>
          {report.dataset_id && (
            <Button
              component="a"
              sx={{ alignSelf: "flex-start" }}
              href={`/dashboard/develop/${encodeURIComponent(report.dataset_id)}?row=${encodeURIComponent(active.row_id)}`}
            >
              Open dataset row
            </Button>
          )}
          <Table size="small" aria-label="Dataset row jury verdicts">
            <TableHead>
              <TableRow>
                <TableCell>Seat</TableCell>
                <TableCell>Verdict</TableCell>
                <TableCell>Reason</TableCell>
              </TableRow>
            </TableHead>
            <TableBody>
              {active.seats.map((seat) => (
                <TableRow key={seat.id}>
                  <TableCell>{seat.id}</TableCell>
                  <TableCell>
                    {seat.passed == null
                      ? "Unavailable"
                      : seat.passed
                        ? "Pass"
                        : "Fail"}
                  </TableCell>
                  <TableCell>
                    {seat.error || seat.reason || "Stored check"}
                  </TableCell>
                </TableRow>
              ))}
            </TableBody>
          </Table>
        </>
      )}
    </Stack>
  );
}
JuryDatasetReport.propTypes = {
  report: PropTypes.object.isRequired,
  runId: PropTypes.string,
  onRunChange: PropTypes.func.isRequired,
};
