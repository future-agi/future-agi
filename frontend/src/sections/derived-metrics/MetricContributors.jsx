import PropTypes from "prop-types";
import { useState } from "react";
import { useQuery } from "@tanstack/react-query";
import {
  Alert,
  Box,
  Button,
  Link,
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
import axios, { endpoints } from "src/utils/axios";
import { contributorLink } from "./contributorLinks";

const number = (value) =>
  value == null
    ? "—"
    : new Intl.NumberFormat(undefined, { maximumSignificantDigits: 6 }).format(
        value,
      );

export default function MetricContributors({
  scope,
  runId,
  resultId,
  projectId,
  rollup,
  groups,
}) {
  const [open, setOpen] = useState(false);
  const [group, setGroup] = useState(groups[0]?.group_key ?? "");
  const query = useQuery({
    queryKey: ["metric-contributors", scope, runId, resultId, rollup, group],
    enabled:
      open &&
      Boolean(resultId) &&
      groups.some((row) => row.group_key === group),
    queryFn: async ({ signal }) =>
      (
        await axios.get(endpoints.derivedMetrics.contributors(runId), {
          signal,
          params: { rollup, group_key: group, limit: 20 },
        })
      ).data,
  });
  return (
    <Stack spacing={1}>
      <Button sx={{ alignSelf: "flex-start" }} onClick={() => setOpen(!open)}>
        {open ? "Hide contributors" : "Inspect contributors"}
      </Button>
      {open && (
        <>
          <Typography variant="body2" color="text.secondary">
            Ranked by the change in value when one unit is removed. Weights
            apply once. Source links open current telemetry, which may have
            expired.
          </Typography>
          <TextField
            select
            label="Contributor group"
            value={group}
            onChange={(event) => setGroup(event.target.value)}
          >
            {groups.map((row) => (
              <MenuItem key={row.group_key} value={row.group_key}>
                {row.group_key ||
                  (rollup === "overall" ? "All units" : "No cohort")}
              </MenuItem>
            ))}
          </TextField>
          {query.isLoading && (
            <Typography role="status">Loading contributors…</Typography>
          )}
          {query.isError && (
            <Alert severity="error">
              Contributors could not be loaded.{" "}
              <Button onClick={() => query.refetch()}>
                Retry contributors
              </Button>
            </Alert>
          )}
          {query.data && (
            <>
              <Typography variant="body2">
                {query.data.diagnostics.valid_count} valid ·{" "}
                {query.data.diagnostics.missing_count} missing ·{" "}
                {query.data.diagnostics.invalid_probability_count} invalid
                probability ·{" "}
                {query.data.diagnostics.undefined_arithmetic_count} undefined
                arithmetic
              </Typography>
              <Typography variant="caption" color="text.secondary">
                Missing by input:{" "}
                {Object.entries(query.data.diagnostics.missing_by_input)
                  .map(([key, count]) => `${key}: ${count}`)
                  .join(" · ")}
              </Typography>
              <Box sx={{ overflowX: "auto" }}>
                <Table size="small" aria-label="Metric contributors">
                  <TableHead>
                    <TableRow>
                      {[
                        "Unit",
                        "Weight",
                        "Numerator",
                        "Denominator",
                        "Influence",
                      ].map((label) => (
                        <TableCell key={label}>{label}</TableCell>
                      ))}
                    </TableRow>
                  </TableHead>
                  <TableBody>
                    {query.data.results.map((row) => (
                      <TableRow key={row.unit_id}>
                        <TableCell
                          sx={{
                            maxWidth: 280,
                            overflowWrap: "anywhere",
                            fontFamily: "monospace",
                          }}
                        >
                          {contributorLink(projectId, row) ? (
                            <Link href={contributorLink(projectId, row)}>
                              {row.unit_id}
                            </Link>
                          ) : (
                            row.unit_id
                          )}
                        </TableCell>
                        {[
                          row.weight,
                          row.numerator,
                          row.denominator,
                          row.influence,
                        ].map((value, index) => (
                          <TableCell
                            key={index}
                            sx={{ fontVariantNumeric: "tabular-nums" }}
                          >
                            {number(value)}
                          </TableCell>
                        ))}
                      </TableRow>
                    ))}
                  </TableBody>
                </Table>
              </Box>
              {query.data.results.length === 0 && (
                <Typography>No valid contributors in this group.</Typography>
              )}
              {query.data.truncated && (
                <Typography variant="caption">
                  Showing the 20 largest influences. Counts above include all
                  units.
                </Typography>
              )}
            </>
          )}
        </>
      )}
    </Stack>
  );
}

MetricContributors.propTypes = {
  scope: PropTypes.string.isRequired,
  runId: PropTypes.string.isRequired,
  resultId: PropTypes.string.isRequired,
  projectId: PropTypes.string.isRequired,
  rollup: PropTypes.string.isRequired,
  groups: PropTypes.arrayOf(
    PropTypes.shape({ group_key: PropTypes.string.isRequired }),
  ).isRequired,
};
