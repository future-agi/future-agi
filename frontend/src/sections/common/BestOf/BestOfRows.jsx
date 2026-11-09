// Best of N result parts (W5-9 B9): each run's win share with its 95%
// interval, and the rows table (recorded order, each run's value, the
// winner or tie). Review M5: an output whose judged value differs from the
// score stored with it shows both (`stored Fail` under `Pass`).
import React from "react";
import PropTypes from "prop-types";
import { Box, Button, Stack, Typography } from "@mui/material";
import {
  judgedVsStored,
  orderText,
  outrightShares,
  pct,
  scoreText,
  shareText,
  verdictText,
  winsText,
} from "./bestOfModel";

const NUM = { fontVariantNumeric: "tabular-nums" };

export function Shares({ rec, labelOf, colorOf, onFilter, filter }) {
  // L7c g6: outright wins only (a tie is never a fractional win), so a
  // run's count equals the rows its filter opens; ties are one line.
  const { shares, tieRows, tieShare } = outrightShares(rec);
  return (
    <Stack gap={0.75} data-testid="best-of-shares">
      {shares.map((s) => (
        <Stack
          key={s.run_id}
          direction="row"
          alignItems="center"
          gap={1.5}
          data-run={labelOf(s.run_id)}
          data-share={s.share}
          data-wins={s.wins}
          data-ties={s.ties}
        >
          <Button
            size="small"
            variant={filter === s.run_id ? "contained" : "text"}
            onClick={() => onFilter(filter === s.run_id ? "all" : s.run_id)}
            sx={{ minWidth: 56, justifyContent: "flex-start" }}
          >
            {labelOf(s.run_id)}
          </Button>
          <Box
            sx={{
              flex: 1,
              height: 14,
              bgcolor: "action.hover",
              borderRadius: 0.5,
              position: "relative",
            }}
          >
            <Box
              sx={{
                width: `${s.share * 100}%`,
                height: "100%",
                bgcolor: colorOf(s.run_id),
                borderRadius: 0.5,
              }}
            />
            <Box
              title="95% interval"
              sx={{
                position: "absolute",
                top: 6,
                height: 2,
                left: `${s.ci.low * 100}%`,
                width: `${(s.ci.high - s.ci.low) * 100}%`,
                bgcolor: "text.primary",
                opacity: 0.6,
              }}
            />
          </Box>
          <Typography typography="s2" sx={{ ...NUM, width: 190 }}>
            {shareText(s)}
          </Typography>
          <Typography
            typography="s3"
            color="text.secondary"
            sx={{ ...NUM, width: 150 }}
          >
            {winsText(s)}
          </Typography>
        </Stack>
      ))}
      {tieRows > 0 && (
        <Typography
          typography="s3"
          color="text.secondary"
          data-testid="best-of-tie-share"
          data-ties={tieRows}
          sx={NUM}
        >
          {`Ties (no single best): ${tieRows} row${tieRows === 1 ? "" : "s"} · ${pct(tieShare)}`}
        </Typography>
      )}
    </Stack>
  );
}

Shares.propTypes = {
  rec: PropTypes.object.isRequired,
  labelOf: PropTypes.func.isRequired,
  colorOf: PropTypes.func.isRequired,
  onFilter: PropTypes.func.isRequired,
  filter: PropTypes.string,
};

export function RowsTable({
  rows,
  runIds,
  labelOf,
  outputType,
  onOpenRow,
  showOrder = false,
}) {
  const cell = { px: 1, py: 0.5, borderBottom: 1, borderColor: "divider" };
  const heads = [
    "Row",
    ...(showOrder ? ["Judged in order"] : []),
    ...runIds.map(labelOf),
    "Best",
  ];
  return (
    <Box
      component="table"
      sx={{ borderCollapse: "collapse", width: "100%", typography: "s2" }}
    >
      <thead>
        <tr>
          {heads.map((h) => (
            <Box
              component="th"
              key={h}
              sx={{ ...cell, textAlign: "left", color: "text.secondary" }}
            >
              {h}
            </Box>
          ))}
        </tr>
      </thead>
      <tbody>
        {rows.map((r) => (
          <tr
            key={r.example_id}
            data-testid="best-of-row"
            data-row={r.example_id}
            data-winner={r.winner ?? ""}
            data-order={r.order.join(",")}
          >
            <Box component="td" sx={cell}>
              <Button
                size="small"
                onClick={() => onOpenRow(r)}
                sx={{ minWidth: 0, p: 0 }}
              >
                {`Row ${r.row_order}`}
              </Button>
            </Box>
            {showOrder && (
              <Box component="td" sx={{ ...cell, color: "text.secondary" }}>
                {orderText(
                  r.order.filter((id) => r.scores?.[id] != null),
                  labelOf,
                )}
              </Box>
            )}
            {runIds.map((id) => {
              const both = judgedVsStored(r, id, outputType);
              return (
                <Box
                  component="td"
                  key={id}
                  data-run={id}
                  data-value={r.scores[id] ?? ""}
                  data-stored={r.stored?.[id] ?? ""}
                  data-differs={both ? "1" : "0"}
                  title={both ?? undefined}
                  sx={{
                    ...cell,
                    ...NUM,
                    fontWeight:
                      r.winner === id || r.tied?.includes(id) ? 600 : 400,
                  }}
                >
                  {scoreText(r.scores[id], outputType)}
                  {both && (
                    <Box
                      component="span"
                      data-testid="best-of-stored"
                      sx={{
                        display: "block",
                        typography: "s3",
                        fontWeight: 400,
                        color: "warning.dark",
                      }}
                    >
                      {`stored ${scoreText(r.stored[id], outputType)}`}
                    </Box>
                  )}
                </Box>
              );
            })}
            <Box component="td" sx={cell}>
              {verdictText(r, labelOf)}
            </Box>
          </tr>
        ))}
      </tbody>
    </Box>
  );
}

RowsTable.propTypes = {
  rows: PropTypes.array.isRequired,
  runIds: PropTypes.array.isRequired,
  labelOf: PropTypes.func.isRequired,
  outputType: PropTypes.string,
  onOpenRow: PropTypes.func.isRequired,
  showOrder: PropTypes.bool,
};
