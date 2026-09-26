import PropTypes from "prop-types";
import { alpha } from "@mui/material/styles";
import { Box, Stack, Typography, CircularProgress } from "@mui/material";

import Iconify from "src/components/iconify";
import EmptyState from "../../../../components/EmptyState";
import { BUILD_TONES } from "../../../../buildEnvironment/buildTones";
import RecommendationCard from "./RecommendationCard";

/**
 * What is wrong, and what to change about it — over the REAL diagnosis.
 *
 * The designer's DiagnosisPane opened onto a six-analyzer animation and a set of
 * fabricated prompt diffs with projected lift percentages. None of that is
 * backed: the `optimiser-analysis` endpoint returns a summary and three levels
 * of actionable recommendations, nothing more. So this pane keeps the designer's
 * real spine — a summary box, then the recommendations ranked by severity — and
 * drops the theatre.
 *
 * The designer's fixable / not-fixable split is preserved because the endpoint
 * makes it real: agent- and domain-level findings are changes to the agent
 * ("fixable"); system-level findings belong to the environment / measurement
 * ("what to check"), and are shown apart so a team doesn't ship a prompt edit
 * for a problem that was never the agent's.
 */

function Section({ icon, tone, title, count, children }) {
  return (
    <Box sx={{ px: 2.5, pt: 2.5 }}>
      <Stack direction="row" alignItems="center" spacing={0.75} sx={{ mb: 1 }}>
        <Iconify icon={icon} width={15} sx={{ color: tone }} />
        <Typography sx={{ typography: "s2", fontWeight: 700, flex: 1 }}>{title}</Typography>
        <Typography sx={{ typography: "s3", color: "text.subtitle" }}>{count}</Typography>
      </Stack>
      {children}
    </Box>
  );
}
Section.propTypes = {
  icon: PropTypes.string,
  tone: PropTypes.string,
  title: PropTypes.string,
  count: PropTypes.node,
  children: PropTypes.node,
};

export default function DiagnosisPane({ analysis, isLoading }) {
  if (isLoading || analysis.isWorking) {
    return (
      <Stack alignItems="center" justifyContent="center" spacing={1.5} sx={{ flex: 1, py: 8 }}>
        <CircularProgress size={22} thickness={5} />
        <Typography sx={{ typography: "s2", color: "text.subtitle" }}>
          Reading this run…
        </Typography>
      </Stack>
    );
  }

  if (!analysis.hasResponse) {
    return (
      <Box sx={{ flex: 1 }}>
        <EmptyState
          icon="solar:magic-stick-3-linear"
          title="No diagnosis yet"
          body="Refresh to have the optimizer read this run's failures and suggest changes."
        />
      </Box>
    );
  }

  const noneFound = !analysis.fixable.length && !analysis.environmental.length;

  return (
    <Box sx={{ flex: 1, overflowY: "auto", minHeight: 0 }}>
      {/* Overall summary — the takeaway from the run's failures. */}
      {analysis.summary && (
        <Box
          sx={{
            mx: 2.5,
            mt: 2.5,
            px: 1.75,
            py: 1.5,
            borderRadius: 1,
            border: "1px solid",
            borderColor: (t) => alpha(BUILD_TONES.accent, t.palette.mode === "dark" ? 0.35 : 0.25),
            bgcolor: (t) => alpha(BUILD_TONES.accent, t.palette.mode === "dark" ? 0.1 : 0.05),
          }}
        >
          <Stack direction="row" alignItems="flex-start" spacing={1.25}>
            <Iconify icon="solar:lightbulb-bolt-linear" width={15} sx={{ color: BUILD_TONES.accent, flexShrink: 0, mt: "2px" }} />
            <Typography sx={{ typography: "s2" }}>{analysis.summary}</Typography>
          </Stack>
        </Box>
      )}

      {noneFound && (
        <Box sx={{ px: 2.5, pt: 2.5 }}>
          <Typography sx={{ typography: "s2", color: "text.subtitle", textAlign: "center", py: 4 }}>
            No recommended fixes for now. This may mean issues are rare,
            inconsistent, or below the current threshold. Consider adding more
            nuanced evaluations to your simulation.
          </Typography>
        </Box>
      )}

      {/* Fixable: change the agent, ranked by severity then reach. */}
      {analysis.fixable.length > 0 && (
        <Section
          icon="solar:magic-stick-3-linear"
          tone={BUILD_TONES.accent}
          title="Fix in this order"
          count={`${analysis.fixable.length} suggested`}
        >
          <Stack spacing={1}>
            {analysis.fixable.map((rec, i) => (
              <RecommendationCard key={rec.id} rec={rec} index={i} />
            ))}
          </Stack>
        </Section>
      )}

      {/* Environmental: what to check — belongs to the environment, not the agent. */}
      {analysis.environmental.length > 0 && (
        <Section
          icon="solar:settings-linear"
          tone={BUILD_TONES.amber}
          title="Check your environment"
          count={`${analysis.environmental.length} finding${analysis.environmental.length === 1 ? "" : "s"}`}
        >
          {analysis.humanComparison && (
            <Typography sx={{ typography: "s3", color: "text.subtitle", mb: 1 }}>
              {analysis.humanComparison}
            </Typography>
          )}
          <Stack spacing={1}>
            {analysis.environmental.map((rec, i) => (
              <RecommendationCard key={rec.id} rec={rec} index={i} />
            ))}
          </Stack>
        </Section>
      )}

      <Box sx={{ height: 20 }} />
    </Box>
  );
}

DiagnosisPane.propTypes = {
  analysis: PropTypes.shape({
    isWorking: PropTypes.bool,
    hasResponse: PropTypes.bool,
    summary: PropTypes.string,
    humanComparison: PropTypes.string,
    fixable: PropTypes.array,
    environmental: PropTypes.array,
  }).isRequired,
  isLoading: PropTypes.bool,
};
