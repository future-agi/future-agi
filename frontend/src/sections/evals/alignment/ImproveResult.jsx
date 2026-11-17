import React from "react";
import PropTypes from "prop-types";
import { useNavigate } from "react-router-dom";
import { Alert, Button, Stack, Typography } from "@mui/material";
import { NOT_BETTER, openRowsHref, pctText } from "./agreementModel";

const tabular = { fontVariantNumeric: "tabular-nums" };

/**
 * Improve judge step 3's answer: agreement before and after on the drafting
 * rows and on the rows the draft never saw, the stored verdicts for
 * reference, the not-better warning, and the rows that moved toward or away
 * from the reviewers (each set opens its rows).
 */
const ImproveResult = ({ result, source }) => {
  const navigate = useNavigate();
  const report = { source };
  return (
    <>
      <Typography typography="s2" sx={tabular}>
        Drafting rows: {pctText(result.before.draft)} →{" "}
        {pctText(result.after.draft)}
      </Typography>
      <Typography typography="s2" sx={tabular}>
        Rows the draft never saw: {pctText(result.before.check)} →{" "}
        {pctText(result.after.check)}
      </Typography>
      <Typography typography="s2" color="text.secondary" sx={tabular}>
        Stored verdicts (for reference): {pctText(result.before.stored)}
      </Typography>
      {(result.after.check ?? 0) <= (result.before.check ?? 0) ? (
        <Alert severity="warning" data-testid="w7-not-better">
          {NOT_BETTER}
        </Alert>
      ) : null}
      {["toward", "away from"].map((dir) => {
        const rows = result.flips.filter(
          (f) => f.toward_reviewer === (dir === "toward"),
        );
        if (!rows.length) return null;
        const href = openRowsHref(
          report.source,
          rows.map((f) => f.row_id),
          `draft moved ${dir} reviewers`,
        );
        return (
          <Stack key={dir} direction="row" gap={1} alignItems="center">
            <Typography typography="s2">
              {rows.length} {rows.length === 1 ? "row" : "rows"} moved {dir} the
              reviewers ({rows.map((f) => f.row_label).join(", ")})
            </Typography>
            {href ? (
              <Button
                size="small"
                onClick={() => navigate(href)}
                sx={{ flexShrink: 0, whiteSpace: "nowrap" }}
              >
                Open rows
              </Button>
            ) : null}
          </Stack>
        );
      })}
    </>
  );
};

ImproveResult.propTypes = {
  result: PropTypes.object.isRequired,
  source: PropTypes.object,
};

export default ImproveResult;
