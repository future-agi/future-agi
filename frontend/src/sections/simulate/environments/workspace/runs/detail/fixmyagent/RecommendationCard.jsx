import PropTypes from "prop-types";
import { useState } from "react";
import { alpha } from "@mui/material/styles";
import { Box, Stack, Typography, Chip, Collapse, Button } from "@mui/material";

import Iconify from "src/components/iconify";
import { BUILD_TONES } from "../../../../buildEnvironment/buildTones";

// The priority chip tone. The endpoint returns high | medium | low; anything
// else renders no chip. Colours are BUILD_TONES, never raw hex.
const PRIORITY_TONE = {
  high: { label: "High priority", tone: BUILD_TONES.red },
  medium: { label: "Medium priority", tone: BUILD_TONES.amber },
  low: { label: "Low priority", tone: BUILD_TONES.grey },
};

/**
 * One diagnosis recommendation, from the REAL `optimiser-analysis` endpoint.
 *
 * Everything shown here is a field the endpoint populates: the heading, the
 * recommendation body, the priority, the branch category, the count of calls it
 * addresses and — behind "View issue" — its concrete breaking points. The
 * designer's fabricated extras (a projected lift %, a prompt diff) are NOT shown:
 * there is no backend field for them, and a made-up number under a real finding
 * would read as a result.
 */
export default function RecommendationCard({ rec, index }) {
  const [open, setOpen] = useState(false);
  const pr = PRIORITY_TONE[rec.priority];
  const hasIssue = rec.breakingPoints.length > 0;

  return (
    <Box sx={{ p: 1.75, borderRadius: 1, border: "1px solid", borderColor: "divider" }}>
      <Stack direction="row" alignItems="flex-start" spacing={1.5}>
        <Typography
          sx={{
            typography: "s1",
            fontWeight: 800,
            color: "text.disabled",
            width: 18,
            textAlign: "center",
            flexShrink: 0,
            lineHeight: 1.35,
            fontVariantNumeric: "tabular-nums",
          }}
        >
          {index + 1}
        </Typography>
        <Box flex={1} minWidth={0}>
          <Stack
            direction="row"
            alignItems="center"
            spacing={0.625}
            flexWrap="wrap"
            rowGap={0.375}
            sx={{ mb: 0.5 }}
          >
            {pr && (
              <Chip
                size="small"
                label={pr.label}
                sx={{
                  height: 18,
                  borderRadius: 0.5,
                  color: pr.tone,
                  border: "1px solid",
                  borderColor: alpha(pr.tone, 0.4),
                  bgcolor: "transparent",
                  "& .MuiChip-label": { px: 0.625, typography: "s3", fontWeight: 700 },
                }}
              />
            )}
            {rec.branchCategory && (
              <Stack direction="row" alignItems="center" spacing={0.5}>
                <Iconify icon="solar:branching-paths-up-linear" width={12} sx={{ color: "text.subtitle" }} />
                <Typography sx={{ typography: "s3", fontWeight: 600, color: "text.secondary" }}>
                  {rec.branchCategory}
                </Typography>
              </Stack>
            )}
          </Stack>

          <Typography sx={{ typography: "s2", fontWeight: 700 }}>{rec.heading}</Typography>
          <Typography sx={{ typography: "s3", color: "text.subtitle", mt: 0.25 }}>
            {rec.recommendation}
          </Typography>

          <Stack direction="row" alignItems="center" spacing={1.5} sx={{ mt: 1 }}>
            <Stack direction="row" alignItems="center" spacing={0.5}>
              <Iconify icon="solar:play-circle-linear" width={13} sx={{ color: "text.subtitle" }} />
              <Typography
                sx={{ typography: "s3", fontWeight: 600, color: "text.secondary", fontVariantNumeric: "tabular-nums" }}
              >
                {rec.callsAffected} call{rec.callsAffected === 1 ? "" : "s"} affected
              </Typography>
            </Stack>
            {hasIssue && (
              <Button
                size="small"
                variant="outlined"
                onClick={() => setOpen((v) => !v)}
                startIcon={<Iconify icon="solar:eye-linear" width={14} />}
                sx={{
                  typography: "s3",
                  fontWeight: 700,
                  color: "text.primary",
                  borderColor: "divider",
                  height: 26,
                  px: 1,
                  "&:hover": { borderColor: "text.primary", bgcolor: "transparent" },
                }}
              >
                {open ? "Hide issue" : "View issue"}
              </Button>
            )}
          </Stack>

          {hasIssue && (
            <Collapse in={open}>
              <Stack spacing={0.5} sx={{ mt: 1.125 }}>
                {rec.breakingPoints.map((point) => (
                  <Stack
                    key={point}
                    direction="row"
                    spacing={0.875}
                    sx={{
                      px: 1.125,
                      py: 0.75,
                      borderRadius: 0.75,
                      bgcolor: (t) => alpha(BUILD_TONES.red, t.palette.mode === "dark" ? 0.1 : 0.05),
                    }}
                  >
                    <Iconify icon="solar:danger-triangle-linear" width={13} sx={{ color: BUILD_TONES.red, flexShrink: 0, mt: "2px" }} />
                    <Typography sx={{ typography: "s3", color: "text.secondary" }}>{point}</Typography>
                  </Stack>
                ))}
              </Stack>
            </Collapse>
          )}
        </Box>
      </Stack>
    </Box>
  );
}

RecommendationCard.propTypes = {
  rec: PropTypes.shape({
    heading: PropTypes.string,
    recommendation: PropTypes.string,
    breakingPoints: PropTypes.arrayOf(PropTypes.string),
    priority: PropTypes.string,
    callsAffected: PropTypes.number,
    branchCategory: PropTypes.string,
  }).isRequired,
  index: PropTypes.number.isRequired,
};
