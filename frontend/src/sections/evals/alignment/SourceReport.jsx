import React, { useState } from "react";
import PropTypes from "prop-types";
import { useNavigate } from "react-router-dom";
import {
  Box,
  ButtonBase,
  Stack,
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableRow,
  TextField,
  Typography,
} from "@mui/material";
import { alpha, useTheme } from "@mui/material/styles";
import {
  cellRows,
  headlineOf,
  kappaText,
  openRowsHref,
  pctText,
  targetLine,
} from "./agreementModel";

const tabular = { fontVariantNumeric: "tabular-nums" };

/**
 * k×k matrix, laid out like the Dataset Summary's Judge vs human: rows =
 * the judge's verdict, columns = the reviewers'. Each cell opens its rows.
 */
function Matrix({ report, source, pairs }) {
  const theme = useTheme();
  const navigate = useNavigate();
  const labels = report.labels ?? [];
  return (
    <Box
      data-testid="w7-agreement-matrix"
      sx={{
        display: "grid",
        gridTemplateColumns: `120px repeat(${labels.length}, minmax(84px, 1fr))`,
        gap: 0.75,
        alignItems: "center",
        maxWidth: 140 + labels.length * 120,
      }}
    >
      <span />
      {labels.map((l) => (
        <Typography
          key={l}
          typography="s3"
          color="text.secondary"
          align="center"
        >
          Reviewers {l}
        </Typography>
      ))}
      {labels.map((jl, j) => (
        <React.Fragment key={jl}>
          <Typography typography="s3" color="text.secondary">
            Judge {jl}
          </Typography>
          {labels.map((hl, h) => {
            const count = report.matrix?.[h]?.[j] ?? 0;
            const agree = h === j;
            const tone = agree
              ? theme.palette.green[500]
              : theme.palette.red[500];
            const ids = cellRows(pairs, labels, h, j);
            const href = openRowsHref(
              source,
              ids,
              agree ? `judge ${jl} · reviewers ${hl}` : "judge ≠ reviewers",
            );
            return (
              <ButtonBase
                key={hl}
                disabled={!count || !href}
                onClick={() => href && navigate(href)}
                data-testid={`w7-cell-${hl}-${jl}`}
                aria-label={`${count} rows: judge ${jl}, reviewers ${hl}`}
                sx={{
                  height: 56,
                  borderRadius: 1,
                  flexDirection: "column",
                  bgcolor: alpha(tone, agree ? 0.14 : 0.1),
                  border: "1px solid",
                  borderColor: alpha(tone, 0.3),
                  "&:hover": { bgcolor: alpha(tone, agree ? 0.22 : 0.18) },
                  "&.Mui-disabled": { opacity: 0.6 },
                }}
              >
                <Typography typography="m3" fontWeight={600} sx={tabular}>
                  {count}
                </Typography>
                <Typography typography="s3" color="text.secondary">
                  {agree ? "Agree" : "Disagree"}
                </Typography>
              </ButtonBase>
            );
          })}
        </React.Fragment>
      ))}
    </Box>
  );
}

Matrix.propTypes = {
  report: PropTypes.object.isRequired,
  source: PropTypes.object,
  pairs: PropTypes.array,
};

/** The selected source: headline, matrix, per-label table, target. */
const SourceReport = ({ report, pairs }) => {
  const [target, setTarget] = useState("");
  const source = report.source;
  if (report.numeric)
    return (
      <Typography typography="s1" data-testid="w7-numeric-headline">
        Spearman ρ {kappaText(report.spearman)} · mean difference{" "}
        {kappaText(report.mad)} · n {report.n}
      </Typography>
    );
  const line = targetLine(report.accuracy, target);
  return (
    <Stack gap={2}>
      <Stack direction="row" gap={2} alignItems="center" flexWrap="wrap">
        <Typography
          typography="m3"
          fontWeight={600}
          sx={tabular}
          data-testid="w7-headline"
        >
          {headlineOf(report)}
        </Typography>
        <Stack direction="row" gap={1} alignItems="center">
          <Typography typography="s2" color="text.secondary">
            Target agreement
          </Typography>
          <TextField
            size="small"
            type="number"
            value={target}
            onChange={(e) => setTarget(e.target.value)}
            inputProps={{
              min: 1,
              max: 100,
              "aria-label": "Target agreement %",
              "data-testid": "w7-target",
            }}
            sx={{ width: 84 }}
          />
          <Typography typography="s2" color="text.secondary">
            %
          </Typography>
          {line ? (
            <Typography
              typography="s2"
              fontWeight={600}
              color={line === "Reached" ? "success.main" : "warning.dark"}
              data-testid="w7-target-line"
            >
              {line}
            </Typography>
          ) : null}
        </Stack>
      </Stack>
      <Stack direction={{ xs: "column", md: "row" }} gap={3}>
        <Matrix report={report} source={source} pairs={pairs} />
        <Box sx={{ flex: 1, minWidth: 0 }}>
          <Table
            size="small"
            data-testid="w7-per-label"
            sx={{ "& td, & th": { typography: "s2", borderColor: "divider" } }}
          >
            <TableHead>
              <TableRow sx={{ bgcolor: "background.neutral" }}>
                {["Label", "Precision", "Recall", "F1", "Reviewer rows"].map(
                  (h, i) => (
                    <TableCell
                      key={h}
                      align={i ? "right" : "left"}
                      sx={{ fontWeight: 600 }}
                    >
                      {h}
                    </TableCell>
                  ),
                )}
              </TableRow>
            </TableHead>
            <TableBody>
              {(report.per_label ?? []).map((l) => (
                <TableRow key={l.label}>
                  <TableCell>{l.label}</TableCell>
                  <TableCell align="right" sx={tabular}>
                    {pctText(l.precision)}
                  </TableCell>
                  <TableCell align="right" sx={tabular}>
                    {pctText(l.recall)}
                  </TableCell>
                  <TableCell align="right" sx={tabular}>
                    {kappaText(l.f1)}
                  </TableCell>
                  <TableCell align="right" sx={tabular}>
                    {l.support}
                  </TableCell>
                </TableRow>
              ))}
            </TableBody>
          </Table>
          <Typography
            typography="s3"
            color="text.secondary"
            sx={{ mt: 1 }}
            data-testid="w7-stored-note"
          >
            Stored verdicts, as the judge wrote them.{" "}
            {report.stored_vs_rescored
              ? `${report.stored_vs_rescored} of ${report.n} would read differently if scored again with today's definition.`
              : `Scoring again with today's definition gives the same ${report.n} verdicts.`}
          </Typography>
        </Box>
      </Stack>
    </Stack>
  );
};

SourceReport.propTypes = {
  report: PropTypes.object.isRequired,
  pairs: PropTypes.array,
};

export default SourceReport;
