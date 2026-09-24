import PropTypes from "prop-types";
import { useEffect, useReducer } from "react";
import { useSearchParams } from "react-router-dom";
import { Box, Button, Grid, Stack, Typography } from "@mui/material";

import Iconify from "src/components/iconify";

import { useEnvironmentsStore } from "../../store/useEnvironmentsStore";
import {
  READ_SECTIONS,
  READ_AUDIT_COPY,
  READER_STATUS,
  READER_STATUS_PARAM,
} from "../readAudit.constants";
import StatChip from "./StatChip";
import FactSection from "./FactSection";
import ReaderStatusBand from "./ReaderStatusBand";
import HardFailPage from "./HardFailPage";
import OpenQuestionsColumn from "./OpenQuestionsColumn";
import PreflightChecks from "./PreflightChecks";
import {
  initialReadAuditState,
  readAuditReducer,
  deriveCounts,
  resolveIssues,
} from "./readAuditReducer";

// The read-audit screen — the designer's AgentReadReceipt (69–292). It surfaces
// every fact the reader extracted (tagged by origin) and every ambiguity it
// couldn't decide alone (as a stepper of resolvable questions), then hands off to
// "Build the environment". Ported verbatim except: answer/reader state lives in
// readAuditReducer, real section gaps come from the audit (not local state, so
// per-section retry goes through the store), and `?readerStatus=` drives a demo
// recovery arc without a backend. The layout is copied from lines 176–282.
export default function ReadAudit({ audit, onBuild, onBack, onRetryRead }) {
  const [searchParams] = useSearchParams();
  const urlOverride = searchParams.get(READER_STATUS_PARAM);

  const [state, dispatch] = useReducer(
    readAuditReducer,
    undefined,
    () => initialReadAuditState(audit, urlOverride),
  );

  const retrySection = useEnvironmentsStore((s) => s.retrySection);
  const retryAll = useEnvironmentsStore((s) => s.retryAll);

  // A refetched audit (real flow only) can move the reader state off hard-fail;
  // the URL demo owns the state when it is set, so never hydrate over it.
  useEffect(() => {
    if (!urlOverride) dispatch({ type: "hydrate", audit });
  }, [audit, urlOverride]);

  if (!audit) return null;

  const issues = resolveIssues(state, audit);
  const {
    counts, inferredCount, resolvedCount, skippedCount, openCount, issueCount,
  } = deriveCounts(state, audit);

  // Hard-fail takes over the whole surface — a failed READ and a failed AGENT are
  // different diagnoses, so four empty sections would mislead.
  if (state.readerStatus === READER_STATUS.HARDFAIL) {
    return (
      <HardFailPage
        agentRef={audit.agentRef}
        reason={audit.hardfailReason || READ_AUDIT_COPY.hardfailDefault}
        onRetry={urlOverride ? () => dispatch({ type: "demoRetry" }) : onRetryRead}
        onChangeSource={onBack}
        onContinueWithDefaults={() => onBuild?.({ __skippedRead: true })}
      />
    );
  }

  const onRetryAll = () => {
    if (urlOverride) {
      dispatch({ type: "demoRetry" });
      return;
    }
    retryAll();
    onRetryRead?.();
  };

  // A skipped draft has a disabled query (no `onRetryRead`), so there is nothing
  // to re-read — hide the retry affordances rather than fire a bodyless request.
  const canRetry = urlOverride || !!onRetryRead;
  const hasQuestions = (audit.questions || []).length > 0;

  return (
    <Box sx={{ display: "flex", flexDirection: "column", height: "100%", minHeight: 0, bgcolor: "background.paper" }}>
      {issueCount > 0 && (
        <ReaderStatusBand
          issueCount={issueCount}
          issues={issues}
          onRetryAll={canRetry ? onRetryAll : undefined}
        />
      )}

      <Box
        sx={{
          flex: 1, minHeight: 0, display: "grid",
          gridTemplateColumns: hasQuestions
            ? { xs: "1fr", lg: "minmax(0, 1.35fr) minmax(0, 1fr)" }
            : "1fr",
        }}
      >
        <Box sx={{ minWidth: 0, overflowY: "auto", px: { xs: 3, md: 4 }, py: 3 }}>
          <Box sx={{ mb: 2 }}>
            <Typography sx={{ typography: "m2", fontWeight: "fontWeightBold" }}>
              {READ_AUDIT_COPY.title}
            </Typography>
            <Typography sx={{ typography: "s2", color: "text.subtitle", mt: 0.25 }}>
              {READ_AUDIT_COPY.subtitle}
            </Typography>
          </Box>

          {/* Summary stat bar — a section the reader couldn't complete shows "—". */}
          <Stack
            direction="row" alignItems="center" flexWrap="wrap" rowGap={1.25}
            sx={{ mb: 2.5, px: 2, py: 1.25, borderRadius: 1.5, border: "1px solid", borderColor: "divider", bgcolor: "background.neutral" }}
            divider={<Box sx={{ width: "1px", height: 18, bgcolor: "divider", mx: 2 }} />}
          >
            <StatChip label="Tools" value={issues.tools ? "—" : counts.tools} tone={issues.tools ? "amber" : "neutral"} />
            <StatChip label="Rules" value={issues.rules ? "—" : counts.rules} tone={issues.rules ? "amber" : "neutral"} />
            <StatChip label="Data" value={issues.data ? "—" : counts.data} tone={issues.data ? "amber" : "neutral"} />
            <StatChip label="Behavior" value={issues.behavior ? "—" : counts.behavior} tone={issues.behavior ? "amber" : "neutral"} />
            {inferredCount > 0 && <StatChip label="Inferred" value={inferredCount} tone="amber" />}
            {openCount > 0 && <StatChip label="Open" value={openCount} tone="red" />}
          </Stack>

          <PreflightChecks checks={audit.checks} />

          {/* Fact sections — 2×2 grid keeps everything above the fold on a laptop. */}
          <Grid container spacing={2}>
            {READ_SECTIONS.map((s) => (
              <Grid key={s.key} item xs={12} md={6}>
                <FactSection
                  icon={s.icon}
                  title={s.title}
                  subtitle={s.subtitle}
                  count={counts[s.key]}
                  facts={audit.reading?.[s.key] || []}
                  issue={issues[s.key]}
                  onRetry={() => {
                    // Drop the mock gap (store) AND re-read for real gaps — the
                    // mapper only suppresses mock gaps, so a real gap needs the refetch.
                    retrySection(s.key);
                    if (!urlOverride) onRetryRead?.();
                  }}
                />
              </Grid>
            ))}
          </Grid>

          {/* With no questions there is no right-hand column, and the build CTA
              lives in it — so the audit would be a dead end. Offer it here. */}
          {!hasQuestions && (
            <Stack direction="row" justifyContent="flex-end" sx={{ mt: 2.5 }}>
              <Button
                variant="contained"
                onClick={() => onBuild?.({})}
                startIcon={<Iconify icon="solar:magic-stick-3-linear" width={15} />}
                sx={{ typography: "s2", fontWeight: "fontWeightBold" }}
              >
                {READ_AUDIT_COPY.build}
              </Button>
            </Stack>
          )}
        </Box>

        {hasQuestions && (
          <OpenQuestionsColumn
            questions={audit.questions}
            activeIdx={state.activeIdx}
            answers={state.answers}
            openCount={openCount}
            resolvedCount={resolvedCount}
            skippedCount={skippedCount}
            dispatch={dispatch}
            onBuild={onBuild}
          />
        )}
      </Box>
    </Box>
  );
}

ReadAudit.propTypes = {
  audit: PropTypes.shape({
    status: PropTypes.string,
    hardfailReason: PropTypes.string,
    agentRef: PropTypes.string,
    reading: PropTypes.shape({
      tools: PropTypes.array,
      rules: PropTypes.array,
      data: PropTypes.array,
      behavior: PropTypes.array,
    }),
    questions: PropTypes.array,
    checks: PropTypes.array,
    sectionIssues: PropTypes.objectOf(PropTypes.shape({
      severity: PropTypes.string,
      icon: PropTypes.string,
      message: PropTypes.string,
      hint: PropTypes.string,
      retryLabel: PropTypes.string,
    })),
  }),
  onBuild: PropTypes.func,
  onBack: PropTypes.func,
  onRetryRead: PropTypes.func,
};
