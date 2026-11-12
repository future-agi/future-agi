import React from "react";
import PropTypes from "prop-types";
import { useNavigate } from "react-router-dom";
import {
  Button,
  Stack,
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableRow,
  Typography,
} from "@mui/material";
import { agoText, openRowsHref } from "./agreementModel";

const cellSx = { verticalAlign: "top" };

/** Rows where the judge and the reviewers differ, with the reviewers' notes. */
const DisagreementsTable = ({ source, items }) => {
  const navigate = useNavigate();
  const ids = (items ?? []).map((d) => d.row_id);
  const href = openRowsHref(source, ids);
  return (
    <Stack gap={1} data-testid="w7-disagreements">
      <Stack direction="row" alignItems="center" gap={1.5}>
        <Typography typography="s1" fontWeight={600}>
          Disagreements · {ids.length}
        </Typography>
        {href ? (
          <Button
            size="small"
            variant="outlined"
            onClick={() => navigate(href)}
            data-testid="w7-open-disagreements"
          >
            Open rows
          </Button>
        ) : null}
      </Stack>
      {ids.length ? (
        <Table
          size="small"
          sx={{ "& td, & th": { typography: "s2", borderColor: "divider" } }}
        >
          <TableHead>
            <TableRow sx={{ bgcolor: "background.neutral" }}>
              {["Row", "Output", "Judge", "Reviewer"].map((h) => (
                <TableCell key={h} sx={{ fontWeight: 600 }}>
                  {h}
                </TableCell>
              ))}
            </TableRow>
          </TableHead>
          <TableBody>
            {items.map((d) => (
              <TableRow
                key={d.row_id}
                hover
                data-testid={`w7-dis-${d.row_label}`}
              >
                <TableCell sx={{ ...cellSx, whiteSpace: "nowrap" }}>
                  <Button
                    size="small"
                    onClick={() => {
                      const one = openRowsHref(source, [d.row_id], d.row_label);
                      if (one) navigate(one);
                    }}
                  >
                    {d.row_label}
                  </Button>
                </TableCell>
                <TableCell sx={{ ...cellSx, maxWidth: 360 }}>
                  <Typography typography="s2" sx={{ wordBreak: "break-word" }}>
                    {d.output ?? "–"}
                  </Typography>
                </TableCell>
                <TableCell sx={{ ...cellSx, maxWidth: 280 }}>
                  <Typography typography="s2" fontWeight={600}>
                    {d.judge?.verdict}
                  </Typography>
                  <Typography typography="s3" color="text.secondary">
                    {d.judge?.reason}
                  </Typography>
                </TableCell>
                <TableCell sx={{ ...cellSx, maxWidth: 260 }}>
                  <Typography typography="s2" fontWeight={600}>
                    {d.human?.verdict}
                  </Typography>
                  {d.human?.note ? (
                    <Typography typography="s2">“{d.human.note}”</Typography>
                  ) : null}
                  <Typography typography="s3" color="text.secondary">
                    {[
                      d.human?.by,
                      agoText(d.human?.at) &&
                        (agoText(d.human.at) === "now"
                          ? "just now"
                          : `${agoText(d.human.at)} ago`),
                    ]
                      .filter(Boolean)
                      .join(" · ")}
                  </Typography>
                </TableCell>
              </TableRow>
            ))}
          </TableBody>
        </Table>
      ) : (
        <Typography typography="s2" color="text.secondary">
          The judge agrees with the reviewers on every labelled row.
        </Typography>
      )}
    </Stack>
  );
};

DisagreementsTable.propTypes = {
  source: PropTypes.object,
  items: PropTypes.array,
};

export default DisagreementsTable;
