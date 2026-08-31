import PropTypes from "prop-types";
import { Box, Stack, Typography } from "@mui/material";
import { ALK_MONO } from "../../alkTokens";
import Tag from "../../parts/Tag";
import AudioBlock from "./AudioBlock";
import CallsTimeline from "./CallsTimeline";
import Fold from "./Fold";
import MetricBars from "./MetricBars";
import Transcript from "./Transcript";
import { splitMetrics } from "./metrics";

/**
 * Sub-goals, split by who decided them. Code and an eval are not the same kind of claim about
 * a result, and a reader deciding whether to trust a verdict needs to know which it is.
 */
const GoalGroup = ({ label, checks }) => (
  <Box sx={{ my: 1.4 }}>
    <Typography
      sx={{
        fontFamily: ALK_MONO,
        fontSize: 11.2,
        textTransform: "uppercase",
        letterSpacing: "0.06em",
        color: "text.secondary",
        mb: 0.5,
      }}
    >
      {label}
    </Typography>
    {checks.map((check) => (
      <Stack
        key={check.name}
        direction="row"
        spacing={2.2}
        alignItems="flex-start"
        sx={{
          py: 1.2,
          borderBottom: "1px solid",
          borderColor: "divider",
          "&:last-of-type": { borderBottom: 0 },
        }}
      >
        <Box
          component="span"
          aria-hidden
          sx={{
            flex: "0 0 auto",
            width: "1em",
            color: check.passed ? "accent.pass" : "accent.fail",
          }}
        >
          {check.passed ? "✓" : "✗"}
        </Box>
        <Box sx={{ minWidth: 0, flex: "1 1 auto" }}>
          <Box
            component="span"
            sx={{ fontFamily: ALK_MONO, fontSize: 12.8, color: "text.primary" }}
          >
            {check.name}
          </Box>
          {/* An eval that passed still shows its reasoning: the whole point of routing a claim
              through a named eval is that somebody can read what it decided and why. Red only
              on a failure, or the reasoning reads as one. */}
          {check.detail && (
            <Typography
              sx={{
                fontSize: 12.5,
                mt: 0.3,
                color: check.passed ? "text.secondary" : "accent.fail",
              }}
            >
              {check.detail}
            </Typography>
          )}
          {check.by && (
            <Typography
              sx={{
                fontFamily: ALK_MONO,
                fontSize: 11.5,
                color: "text.secondary",
                opacity: 0.8,
                mt: 0.2,
              }}
            >
              {check.by}
            </Typography>
          )}
        </Box>
      </Stack>
    ))}
  </Box>
);

GoalGroup.propTypes = {
  label: PropTypes.string.isRequired,
  checks: PropTypes.array.isRequired,
};

/**
 * One scenario, as something you read rather than scroll past.
 *
 * The order is the order the questions get asked in: did it pass, what was it testing, how did
 * the call go, which sub-goals held and who decided each, and only then the evidence.
 */
const ScenarioCard = ({ runId, result }) => {
  const checks = result.checkpoints || [];
  const measures = result.measured || {};
  const callsDetail = result.calls_detail || [];
  const tracks = result.tracks || [];

  // How the call went, before anything about whether it passed. A run that ended because the
  // caller hung up is a different result from one that ran out of turns.
  const facts = [];
  if (result.turns) facts.push(`${result.turns} turns`);
  const refused = callsDetail.filter((call) => call.refused).length;
  if (result.calls != null) {
    facts.push(
      `${result.calls} tool calls${refused ? `, ${refused} refused` : ""}`,
    );
  }
  if (result.seconds) facts.push(`${Math.round(result.seconds)}s`);
  if (measures.stop_reason)
    facts.push(`ended: ${String(measures.stop_reason).replace(/_/g, " ")}`);
  if (measures.score != null) {
    facts.push(
      `ALK score ${Number(measures.score).toFixed(2)}` +
        (measures.threshold != null ? ` vs ${measures.threshold}` : ""),
    );
  }
  if (measures.simulator?.model)
    facts.push(`caller on ${measures.simulator.model}`);

  const byCode = checks.filter(
    (one) => one.kind !== "eval" && one.kind !== "judged",
  );
  const byEval = checks.filter(
    (one) => one.kind === "eval" || one.kind === "judged",
  );
  const { measured, clean, absent } = splitMetrics(measures.metrics);
  const evidence = measures.evidence || [];

  return (
    <Box
      sx={{
        mb: 2,
        px: 3.6,
        py: 3,
        borderRadius: "8px",
        bgcolor: "background.paper",
        border: "1px solid",
        borderColor: "divider",
        borderLeft: "3px solid",
        borderLeftColor: result.passed ? "accent.pass" : "accent.fail",
      }}
    >
      <Stack
        direction="row"
        alignItems="center"
        spacing={2}
        flexWrap="wrap"
        useFlexGap
      >
        <Tag kind={result.passed ? "pass" : "fail"}>
          {result.passed ? "PASS" : "FAIL"}
        </Tag>
        <Typography
          component="span"
          sx={{
            fontFamily: ALK_MONO,
            fontSize: 14,
            fontWeight: 600,
            color: "text.primary",
          }}
        >
          {result.scenario}
        </Typography>
        <Box sx={{ flex: "1 1 auto" }} />
        <Typography component="span" variant="caption" color="text.secondary">
          {`${result.met ?? 0}/${checks.length} sub-goals`}
        </Typography>
      </Stack>

      {result.tests && (
        <Typography sx={{ fontSize: 13, color: "text.secondary", mt: 1.2 }}>
          {result.tests}
        </Typography>
      )}

      {facts.length > 0 && (
        <Typography
          sx={{
            fontSize: 12.2,
            color: "text.secondary",
            fontVariantNumeric: "tabular-nums",
            mt: 1,
            mb: 1.6,
          }}
        >
          {facts.join("  ·  ")}
        </Typography>
      )}

      {(result.problems || []).map((problem) => (
        <Box
          key={problem}
          sx={{
            fontFamily: ALK_MONO,
            fontSize: 11.8,
            color: "accent.fail",
            bgcolor: "action.hover",
            border: "1px solid",
            borderColor: "accent.fail",
            borderRadius: "3px",
            px: 2.2,
            py: 1.4,
            mb: 1,
          }}
        >
          {problem}
        </Box>
      ))}

      {checks.length > 0 && (
        <Box sx={{ mt: 1.6 }}>
          <Typography
            sx={{
              fontFamily: ALK_MONO,
              fontSize: 10.6,
              letterSpacing: "0.07em",
              textTransform: "uppercase",
              color: "text.secondary",
            }}
          >
            sub-goals
          </Typography>
          {byCode.length > 0 && (
            <GoalGroup label="settled by code" checks={byCode} />
          )}
          {byEval.length > 0 && (
            <GoalGroup label="decided by the eval harness" checks={byEval} />
          )}
        </Box>
      )}

      {tracks.length > 0 && (
        <AudioBlock runId={runId} scenario={result.scenario} tracks={tracks} />
      )}

      {result.transcript && (
        <Fold
          label={`the conversation${result.turns ? ` (${result.turns} turns)` : ""}`}
        >
          <Transcript spoken={result.transcript} />
        </Fold>
      )}

      {callsDetail.length > 0 && (
        <Fold label={`what the agent actually did (${callsDetail.length})`}>
          <CallsTimeline calls={callsDetail} />
        </Fold>
      )}

      {(measured.length > 0 || clean.length > 0 || absent.length > 0) && (
        <Fold
          label={`what the run measured (${measured.length} scored, ${clean.length} clean, ${absent.length} n/a)`}
        >
          <MetricBars metrics={measures.metrics} />
        </Fold>
      )}

      {evidence.length > 0 && (
        // What each source claims it can prove. A number derived from a source that reports no
        // latency is not a latency measurement, and this says which is which.
        <Fold label={`evidence (${evidence.length} sources)`}>
          {evidence.map((source) => (
            <Typography
              key={source.adapter || source.source}
              sx={{
                fontFamily: ALK_MONO,
                fontSize: 11.8,
                color: source.available ? "text.secondary" : "accent.fail",
                my: 0.2,
                ml: 1,
              }}
            >
              {`${source.adapter || source.source}${source.available ? "" : " (unavailable)"} — proves ${
                (source.proves || []).join(", ") || "nothing"
              }`}
            </Typography>
          ))}
        </Fold>
      )}
    </Box>
  );
};

ScenarioCard.propTypes = {
  runId: PropTypes.string.isRequired,
  result: PropTypes.object.isRequired,
};

export default ScenarioCard;
