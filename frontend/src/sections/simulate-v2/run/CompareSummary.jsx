import PropTypes from "prop-types";
import { alpha } from "@mui/material/styles";
import { Box, Stack, Typography } from "@mui/material";
import Iconify from "src/components/iconify";
import { summaryRows } from "../_mock/compareView";

/**
 * The comparison with the scenarios taken out.
 *
 * Sometimes the question is not "which scenario moved" but "is this version
 * better", and that is answered by a dozen numbers rather than by thirty rows.
 * Every figure here also exists on the runs table; what this adds is that only
 * the compared runs are present, and every one of them is read against the
 * baseline.
 */
export default function CompareSummary({ comparison, evals }) {
  const rows = summaryRows(comparison, evals);
  const { runs, baseline } = comparison;

  return (
    <Box sx={{ overflowX: "auto" }}>
      <Box sx={{ minWidth: 220 + runs.length * 180 }}>
        <Box
          sx={{
            display: "grid", gridTemplateColumns: `220px repeat(${runs.length}, 1fr)`,
            borderBottom: "1px solid", borderColor: "divider",
          }}
        >
          <Box sx={{ px: 2.5, py: 1.5 }} />
          {runs.map((r) => (
            <Stack key={r.id} direction="row" alignItems="center" spacing={1} sx={{ px: 2, py: 1.5 }}>
              <Box
                sx={{
                  width: 20, height: 20, borderRadius: 0.75, flexShrink: 0,
                  display: "grid", placeItems: "center", typography: "s3", fontWeight: 700,
                  color: r.color, bgcolor: (t) => alpha(r.color, t.palette.mode === "dark" ? 0.22 : 0.14),
                }}
              >
                {r.letter}
              </Box>
              <Box minWidth={0}>
                <Typography noWrap sx={{ typography: "s2", fontWeight: 700 }}>agent {r.agentVersion}</Typography>
                <Typography sx={{ typography: "s3", color: "text.subtitle" }}>
                  {r.id === baseline.id ? "baseline" : `Run ${r.index + 1}`}
                </Typography>
              </Box>
            </Stack>
          ))}
        </Box>

        <Stack divider={<Box sx={{ borderBottom: "1px solid", borderColor: "divider" }} />}>
          {rows.map((line) => (
            <Box
              key={line.id}
              sx={{ display: "grid", gridTemplateColumns: `220px repeat(${runs.length}, 1fr)`, alignItems: "center" }}
            >
              <Typography sx={{ px: 2.5, py: 1.5, typography: "s2", color: "text.secondary" }}>
                {line.label}
              </Typography>
              {line.values.map((v) => (
                <Stack key={v.run.id} direction="row" alignItems="baseline" spacing={0.75} sx={{ px: 2, py: 1.5 }}>
                  <Typography sx={{ typography: "s2", fontWeight: 600, fontVariantNumeric: "tabular-nums" }}>
                    {v.text}
                  </Typography>
                  {v.moved && (
                    <Stack direction="row" alignItems="center" spacing={0.125}>
                      <Iconify
                        icon={v.better ? "eva:arrow-upward-fill" : "eva:arrow-downward-fill"}
                        width={10}
                        sx={{ color: v.better ? "#5AA47B" : "#C2603F" }}
                      />
                      <Typography sx={{ typography: "s3", fontWeight: 600, color: v.better ? "#5AA47B" : "#C2603F" }}>
                        {v.better ? "better" : "worse"}
                      </Typography>
                    </Stack>
                  )}
                </Stack>
              ))}
            </Box>
          ))}
        </Stack>
      </Box>
    </Box>
  );
}

CompareSummary.propTypes = { comparison: PropTypes.object, evals: PropTypes.array };
