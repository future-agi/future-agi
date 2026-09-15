import PropTypes from "prop-types";
import { useState, useMemo } from "react";
import { useSearchParams } from "react-router-dom";
import { alpha } from "@mui/material/styles";
import { Box, Grid, Stack, Typography, Button, IconButton, Tooltip, TextField, Divider, Link } from "@mui/material";
import Iconify from "src/components/iconify";
import { OriginChip } from "../components/primitives";

/*
  Reader can fail three ways. Each has its own affordance in this
  component:

    healthy   — everything read; no status band, no inline warnings.
    warning   — some dimensions blank OR facts conflict; slim status
                band at top + inline reasons on the affected sections.
                Retry-per-section restores the missing dimension.
    hardfail  — nothing came back. The entire receipt is replaced by
                a centered error page with Retry / Change source /
                Continue without reading affordances.

  For demoing without touching product code, ?readerStatus=warning|
  hardfail on the URL forces the state. Absent the param, the parent
  passes the real reader payload and shape drives the UI.
*/

/**
 * What we read from your agent.
 *
 * The read-audit sits between "connect the agent" and "build the world".
 * It surfaces every fact the reader extracted (with a source tag on each)
 * and every ambiguity it couldn't decide alone (as a stepper of resolvable
 * questions). Building the world becomes a deliberate act on named facts,
 * not a hidden pipeline.
 *
 * Layout is document-style — a fixed-width column that reads top-to-bottom
 * like a briefing, not a dashboard. Sections stack vertically. Each fact
 * is a table row, not a card. The Build CTA lives in a footer bar with
 * a running "N of M resolved" counter so the user always knows the state
 * of their answers.
 */

const SECTIONS = [
  { key: "tools", title: "Tools it can call", subtitle: "From the agent's config and call-graph", icon: "solar:code-square-linear" },
  { key: "rules", title: "Rules it must hold", subtitle: "Data from policy.yaml — not judgement", icon: "solar:shield-check-linear" },
  { key: "data", title: "Data it starts with", subtitle: "Fixtures that seed the world every run", icon: "solar:database-linear" },
  { key: "behavior", title: "How it behaves", subtitle: "Prompt, voice, routing", icon: "solar:playlist-linear" },
];

/* Baseline warnings used when `?readerStatus=warning` is on and the
   payload didn't supply its own. Each entry says WHY the section
   is empty and, where sensible, offers a per-section retry. */
const DEFAULT_DEMO_ISSUES = {
  rules: {
    severity: "warning",
    icon: "solar:file-remove-linear",
    message: "No policy.yaml found in the repo",
    hint: "Rules can still be captured — add them manually or point us at a different branch.",
    retryLabel: "Retry read",
  },
  data: {
    severity: "warning",
    icon: "solar:clock-circle-linear",
    message: "Call-graph timed out at 30s — 2 fixtures not resolved",
    hint: "The scan is deterministic; retrying often succeeds once other jobs free the workers.",
    retryLabel: "Retry read",
  },
};

export default function AgentReadReceipt({
  agentRef,
  reading, // { tools, rules, data, behavior, status?, hardfailReason?, sectionIssues? }
  questions, // [{id, title, why, kind, options?}]
  onBuild,
  onBack,
}) {
  const [answers, setAnswers] = useState({});
  const [activeIdx, setActiveIdx] = useState(0);

  /*
    Read the URL override once; use it to bias local state so Retry
    inside the receipt can transition without touching the URL.

    Precedence: URL override > payload status > "warning".

    The default is "warning" so a fresh demo lands on the receipt
    already showing gaps + inline retry buttons — the reader made
    progress but didn't finish. From there:
      warning   → Retry read → healthy
      healthy   → Build the environment
    The hard-fail page is still available via ?readerStatus=hardfail
    for cases where the reader returned nothing at all, but it never
    fires as the default.
  */
  const [searchParams] = useSearchParams();
  const urlOverride = searchParams.get("readerStatus");
  const initialStatus = urlOverride || reading?.status || "warning";
  const [readerStatus, setReaderStatus] = useState(initialStatus);
  /* Local view of section issues — Retry-per-section clears them. */
  const initialIssues = readerStatus === "warning" || urlOverride === "warning"
    ? (reading?.sectionIssues || DEFAULT_DEMO_ISSUES)
    : (reading?.sectionIssues || {});
  const [sectionIssues, setSectionIssues] = useState(initialIssues);

  /* Retry cycles hard-fail → warning → healthy so a demo can walk
     through the recovery arc in one place. Section retries clear
     the issue on that dimension. */
  const cycleReaderState = () => {
    setReaderStatus((s) => (s === "hardfail" ? "warning" : "healthy"));
    if (readerStatus === "hardfail") setSectionIssues(DEFAULT_DEMO_ISSUES);
    else setSectionIssues({});
  };
  const retrySection = (key) => setSectionIssues((prev) => {
    const next = { ...prev };
    delete next[key];
    return next;
  });

  const isResolved = (a) => !!a && !a.skipped && (a.pick != null || (a.other && a.other.trim().length > 0));

  const resolvedCount = useMemo(
    () => questions.filter((q) => isResolved(answers[q.id])).length,
    [answers, questions],
  );
  const skippedCount = useMemo(
    () => questions.filter((q) => answers[q.id]?.skipped).length,
    [answers, questions],
  );
  const openCount = questions.length - resolvedCount - skippedCount;

  const setAnswer = (id, patch) => setAnswers((prev) => ({
    ...prev,
    [id]: { ...(prev[id] || {}), ...patch, skipped: false },
  }));
  const skipAnswer = (id) => setAnswers((prev) => ({
    ...prev,
    [id]: { pick: null, other: "", skipped: true },
  }));

  const counts = {
    tools: reading.tools?.length || 0,
    rules: reading.rules?.length || 0,
    data: reading.data?.length || 0,
    behavior: reading.behavior?.length || 0,
  };
  const inferredCount = [...(reading.behavior || []), ...(reading.tools || []), ...(reading.rules || []), ...(reading.data || [])]
    .filter((f) => f.origin === "inferred").length;

  /* Hard-fail takes over the whole surface. Nothing came back from the
     reader, so rendering four empty sections would be misleading — a
     failed READ and a failed AGENT are different diagnoses. */
  if (readerStatus === "hardfail") {
    return (
      <HardFailPage
        agentRef={agentRef}
        reason={reading?.hardfailReason || "No response from the agent source. The reader gave up after 30s."}
        onRetry={cycleReaderState}
        onChangeSource={onBack}
        onContinueWithDefaults={() => onBuild?.({ __skippedRead: true })}
      />
    );
  }

  const issueCount = Object.keys(sectionIssues).length;

  return (
    <Box sx={{ display: "flex", flexDirection: "column", height: "100%", minHeight: 0, bgcolor: "background.paper" }}>
      {/* ── warning band — slim strip above the doc body ── */}
      {issueCount > 0 && (
        <ReaderStatusBand
          issueCount={issueCount}
          issues={sectionIssues}
          onRetryAll={cycleReaderState}
        />
      )}

      {/* ── document body — two columns so the build action never falls below
            the fold: "what we read" scrolls on the left, the open questions +
            build stay visible on the right. Collapses to one column when there
            are no questions, or on small screens. ── */}
      <Box
        sx={{
          flex: 1, minHeight: 0, display: "grid",
          gridTemplateColumns: questions.length > 0
            ? { xs: "1fr", lg: "minmax(0, 1.35fr) minmax(0, 1fr)" }
            : "1fr",
        }}
      >
        <Box sx={{ minWidth: 0, overflowY: "auto", px: { xs: 3, md: 4 }, py: 3 }}>

          {/* Title block */}
          <Box sx={{ mb: 2 }}>
            <Typography sx={{ typography: "m2", fontWeight: 700 }}>
              What we read from your agent
            </Typography>
            <Typography sx={{ typography: "s2", color: "text.subtitle", mt: 0.25 }}>
              Every fact below is tagged by how we know it — resolve the open questions on the right before we build.
            </Typography>
          </Box>

          {/* Summary stat bar — one glance at what the read produced. A section
              the reader couldn't complete shows "—" (amber) rather than a count. */}
          <Stack
            direction="row" alignItems="center" flexWrap="wrap" rowGap={1.25}
            sx={{ mb: 2.5, px: 2, py: 1.25, borderRadius: 1.5, border: "1px solid", borderColor: "divider", bgcolor: "background.neutral" }}
            divider={<Box sx={{ width: "1px", height: 18, bgcolor: "divider", mx: 2 }} />}
          >
            <StatChip label="Tools" value={sectionIssues.tools ? "—" : counts.tools} tone={sectionIssues.tools ? "amber" : "neutral"} />
            <StatChip label="Rules" value={sectionIssues.rules ? "—" : counts.rules} tone={sectionIssues.rules ? "amber" : "neutral"} />
            <StatChip label="Data" value={sectionIssues.data ? "—" : counts.data} tone={sectionIssues.data ? "amber" : "neutral"} />
            <StatChip label="Behavior" value={sectionIssues.behavior ? "—" : counts.behavior} tone={sectionIssues.behavior ? "amber" : "neutral"} />
            {inferredCount > 0 && <StatChip label="Inferred" value={inferredCount} tone="amber" />}
            {openCount > 0 && <StatChip label="Open" value={openCount} tone="red" />}
          </Stack>

          {/* Fact sections — 2×2 grid keeps everything above the fold on a laptop */}
          <Grid container spacing={2}>
            {SECTIONS.map((s) => (
              <Grid key={s.key} item xs={12} md={6}>
                <FactSection
                  icon={s.icon}
                  title={s.title}
                  subtitle={s.subtitle}
                  count={counts[s.key]}
                  facts={reading[s.key] || []}
                  issue={sectionIssues[s.key]}
                  onRetry={() => retrySection(s.key)}
                />
              </Grid>
            ))}
          </Grid>
        </Box>

        {/* right column — open questions + the build action, kept in view */}
        {questions.length > 0 && (
          <Box sx={{ minWidth: 0, overflowY: "auto", px: { xs: 3, md: 4 }, py: 3, borderLeft: { lg: "1px solid" }, borderColor: { lg: "divider" } }}>
              <SectionHead
                icon="solar:question-circle-linear"
                title="Open questions"
                subtitle={openCount === 0
                  ? "All answered — the world will be built on named facts"
                  : `${openCount} of ${questions.length} still open — resolve them before we build against them`}
                count={questions.length}
                accent={openCount > 0 ? "#DC2626" : "#16A34A"}
              />
              <AskUserQuestionCard
                step={activeIdx}
                total={questions.length}
                question={questions[activeIdx]}
                answer={answers[questions[activeIdx].id]}
                onPick={(idx) => {
                  const q = questions[activeIdx];
                  setAnswer(q.id, { pick: idx });
                  /* Auto-advance to the next question on a real choice — but not
                     for "Other" (the user still has to type) and not on the last
                     question (it turns into "Build the environment"). */
                  const optionCount = (q?.options
                    || (q?.kind === "boolean" ? [0, 1] : [])).length;
                  const pickedOther = idx === optionCount;
                  const isLastQ = activeIdx === questions.length - 1;
                  if (!pickedOther && !isLastQ) {
                    setActiveIdx((i) => Math.min(questions.length - 1, i + 1));
                  }
                }}
                onOtherChange={(text) => setAnswer(questions[activeIdx].id, { other: text })}
                onBack={activeIdx > 0 ? () => setActiveIdx((i) => Math.max(0, i - 1)) : null}
                onSkip={() => {
                  skipAnswer(questions[activeIdx].id);
                  if (activeIdx < questions.length - 1) setActiveIdx(activeIdx + 1);
                }}
                onNext={() => setActiveIdx((i) => Math.min(questions.length - 1, i + 1))}
                onBuild={() => onBuild?.(answers)}
                canSubmit={isResolved(answers[questions[activeIdx].id])}
                isLast={activeIdx === questions.length - 1}
                skipped={answers[questions[activeIdx].id]?.skipped}
                openCount={openCount}
                resolvedCount={resolvedCount}
                skippedCount={skippedCount}
              />
          </Box>
        )}
      </Box>
    </Box>
  );
}

AgentReadReceipt.propTypes = {
  agentRef: PropTypes.string,
  reading: PropTypes.object,
  questions: PropTypes.array,
  onBuild: PropTypes.func,
  onBack: PropTypes.func,
};

/* ── build footer bar — sits below the questionnaire ─────────────────────── */

function BuildFooter({ resolvedCount, skippedCount, openCount, total, onBuild }) {
  const disabled = openCount > 0;
  const summary = disabled
    ? `${openCount} open ${openCount === 1 ? "question" : "questions"} still block the build`
    : skippedCount > 0
      ? `${skippedCount} skipped — the affected facts will carry an INFERRED marker in the built world`
      : "Every question answered — the world will be built on named facts";
  return (
    <Stack
      direction="row" alignItems="center" spacing={1.5}
      sx={{
        mt: 3, pt: 2, borderTop: "1px solid", borderColor: "divider",
      }}
    >
      <Box flex={1} minWidth={0}>
        <Typography sx={{ typography: "s2", color: "text.primary", fontWeight: 600 }}>
          {disabled ? "Resolve the remaining questions to continue" : "Ready to build"}
        </Typography>
        <Typography sx={{ typography: "s3", color: "text.subtitle", mt: 0.25 }}>
          {summary}
          {total > 0 && ` · ${resolvedCount + skippedCount} of ${total} resolved`}
        </Typography>
      </Box>
      <Tooltip arrow title={disabled ? summary : ""}>
        <span>
          <Button
            variant="contained" color="primary"
            onClick={onBuild}
            disabled={disabled}
            endIcon={<Iconify icon="solar:alt-arrow-right-linear" width={15} />}
            sx={{ typography: "s2", fontWeight: 700, px: 2 }}
          >
            Build the environment
          </Button>
        </span>
      </Tooltip>
    </Stack>
  );
}
BuildFooter.propTypes = {
  resolvedCount: PropTypes.number, skippedCount: PropTypes.number,
  openCount: PropTypes.number, total: PropTypes.number, onBuild: PropTypes.func,
};

/* ── metadata strip ───────────────────────────────────────────────────────── */

function StatChip({ label, value, tone = "neutral" }) {
  const color = tone === "red" ? "#DC2626" : tone === "amber" ? "#CA8A04" : null;
  return (
    <Stack direction="row" alignItems="center" spacing={0.875}>
      <Box sx={{ width: 7, height: 7, borderRadius: "50%", flexShrink: 0, bgcolor: color || "text.disabled" }} />
      <Typography sx={{ typography: "s2", fontWeight: 700, color: color || "text.primary", fontVariantNumeric: "tabular-nums" }}>
        {value}
      </Typography>
      <Typography sx={{ typography: "s3", color: "text.subtitle" }}>{label}</Typography>
    </Stack>
  );
}
StatChip.propTypes = { label: PropTypes.string, value: PropTypes.node, tone: PropTypes.oneOf(["neutral", "amber", "red"]) };

/* ── section head + fact table ────────────────────────────────────────────── */

function SectionHead({ icon, title, subtitle, count, accent }) {
  return (
    <Stack
      direction="row" alignItems="center" spacing={1.25}
      sx={{ py: 0.75, borderBottom: "1px solid", borderColor: "divider", mb: 0.25 }}
    >
      <Box
        sx={{
          width: 20, height: 20, borderRadius: 0.5, display: "grid", placeItems: "center",
          bgcolor: (t) => alpha(accent || t.palette.text.primary, t.palette.mode === "dark" ? 0.14 : 0.08),
          color: accent || "text.secondary",
          flexShrink: 0,
        }}
      >
        <Iconify icon={icon} width={12} />
      </Box>
      <Stack direction="row" alignItems="baseline" spacing={0.75} sx={{ flex: 1, minWidth: 0 }}>
        <Typography sx={{ typography: "s2", fontWeight: 700 }}>{title}</Typography>
        {count != null && (
          <Typography sx={{ typography: "s3", color: "text.subtitle", fontVariantNumeric: "tabular-nums" }}>
            {count}
          </Typography>
        )}
        {subtitle && (
          <Typography noWrap sx={{ typography: "s3", color: "text.subtitle", ml: "auto !important" }}>
            {subtitle}
          </Typography>
        )}
      </Stack>
    </Stack>
  );
}
SectionHead.propTypes = {
  icon: PropTypes.string, title: PropTypes.string, subtitle: PropTypes.string,
  count: PropTypes.number, accent: PropTypes.string,
};

function FactSection({ icon, title, subtitle, count, facts, issue, onRetry }) {
  const isIssued = !!issue;
  const showFacts = !isIssued && facts.length > 0;
  return (
    <Box
      sx={{
        height: "100%", display: "flex", flexDirection: "column",
        border: "1px solid", borderColor: "divider", borderRadius: 1.5, overflow: "hidden",
      }}
    >
      {/* card header */}
      <Stack
        direction="row" alignItems="center" spacing={1}
        sx={{ px: 1.75, py: 1.25, borderBottom: "1px solid", borderColor: "divider" }}
      >
        <Box
          sx={{
            width: 22, height: 22, borderRadius: 0.75, display: "grid", placeItems: "center", flexShrink: 0,
            bgcolor: (t) => alpha(isIssued ? "#CA8A04" : t.palette.text.primary, isIssued ? 0.14 : (t.palette.mode === "dark" ? 0.08 : 0.06)),
            color: isIssued ? "#CA8A04" : "text.secondary",
          }}
        >
          <Iconify icon={icon} width={13} />
        </Box>
        <Typography noWrap sx={{ typography: "s2", fontWeight: 700, minWidth: 0 }}>{title}</Typography>
        {!isIssued && count != null && (
          <Box sx={{ px: 0.625, height: 16, borderRadius: 0.5, display: "inline-flex", alignItems: "center", flexShrink: 0, bgcolor: (t) => alpha(t.palette.text.primary, 0.08) }}>
            <Typography sx={{ typography: "s3", fontWeight: 700, color: "text.secondary", fontVariantNumeric: "tabular-nums" }}>{count}</Typography>
          </Box>
        )}
        {subtitle && (
          <Tooltip arrow title={subtitle}>
            <Box sx={{ ml: "auto", display: "flex", flexShrink: 0, color: "text.disabled" }}>
              <Iconify icon="solar:info-circle-linear" width={13} />
            </Box>
          </Tooltip>
        )}
      </Stack>

      {/* card body */}
      <Box sx={{ flex: 1, minHeight: 0, px: 1.75, py: isIssued ? 1.5 : 0.25 }}>
        {isIssued ? (
          <SectionIssue issue={issue} onRetry={onRetry} />
        ) : showFacts ? (
          <Stack divider={<Divider sx={{ borderColor: (t) => alpha(t.palette.divider, 0.6) }} />}>
            {facts.map((f) => (
              <Stack
                key={f.name}
                direction="row" alignItems="center" spacing={1.25}
                sx={{
                  py: 0.75, mx: -0.75, px: 0.75, borderRadius: 0.75,
                  transition: "background-color .1s ease",
                  "&:hover": { bgcolor: (t) => alpha(t.palette.text.primary, t.palette.mode === "dark" ? 0.04 : 0.03) },
                }}
              >
                <Typography
                  noWrap
                  sx={{ typography: "s2", fontWeight: 500, fontFamily: "ui-monospace, Menlo, monospace", flex: 1, minWidth: 0, color: "text.primary" }}
                >
                  {f.name}
                </Typography>
                {f.warning && (
                  <Tooltip arrow title={f.warning}>
                    <Box sx={{ display: "flex", alignItems: "center", color: "#CA8A04", flexShrink: 0 }}>
                      <Iconify icon="solar:danger-triangle-bold" width={13} />
                    </Box>
                  </Tooltip>
                )}
                {f.note && (
                  <Typography noWrap sx={{ typography: "s3", color: "text.subtitle", flexShrink: 0 }}>{f.note}</Typography>
                )}
                <OriginChip origin={f.origin} showPath={false} />
              </Stack>
            ))}
          </Stack>
        ) : (
          <Typography sx={{ typography: "s3", color: "text.subtitle", py: 1 }}>
            Nothing read from this dimension.
          </Typography>
        )}
      </Box>
    </Box>
  );
}
FactSection.propTypes = {
  icon: PropTypes.string, title: PropTypes.string, subtitle: PropTypes.string,
  count: PropTypes.number, facts: PropTypes.array,
  issue: PropTypes.object, onRetry: PropTypes.func,
};

/* ── section-level issue block (inline "why this is empty") ─────────────── */

function SectionIssue({ issue, onRetry }) {
  return (
    <Stack
      direction="row" alignItems="flex-start" spacing={1.25}
      sx={{
        mt: 1, px: 1.5, py: 1.25,
        borderRadius: 1,
        border: "1px solid",
        borderColor: (t) => alpha("#CA8A04", t.palette.mode === "dark" ? 0.35 : 0.3),
        bgcolor: (t) => alpha("#CA8A04", t.palette.mode === "dark" ? 0.08 : 0.05),
      }}
    >
      <Iconify icon={issue.icon || "solar:danger-triangle-linear"} width={15} sx={{ color: "#CA8A04", flexShrink: 0, mt: "2px" }} />
      <Box flex={1} minWidth={0}>
        <Typography sx={{ typography: "s2", fontWeight: 700, color: "text.primary" }}>
          {issue.message}
        </Typography>
        {issue.hint && (
          <Typography sx={{ typography: "s3", color: "text.subtitle", mt: 0.25 }}>
            {issue.hint}
          </Typography>
        )}
      </Box>
      {onRetry && issue.retryLabel && (
        <Button
          size="small" variant="outlined"
          onClick={onRetry}
          startIcon={<Iconify icon="solar:refresh-linear" width={13} />}
          sx={{
            typography: "s3", fontWeight: 700,
            color: "text.primary", borderColor: "divider",
            "&:hover": { borderColor: "text.primary", bgcolor: "transparent" },
          }}
        >
          {issue.retryLabel}
        </Button>
      )}
    </Stack>
  );
}
SectionIssue.propTypes = { issue: PropTypes.object, onRetry: PropTypes.func };

/* ── reader status band — slim strip between top bar and doc body ── */

function ReaderStatusBand({ issueCount, issues, onRetryAll }) {
  const list = Object.keys(issues || {}).join(", ");
  return (
    <Stack
      direction="row" alignItems="center" spacing={1.25}
      sx={{
        px: 3, py: 1, flexShrink: 0,
        borderBottom: "1px solid",
        borderColor: (t) => alpha("#CA8A04", 0.3),
        bgcolor: (t) => alpha("#CA8A04", t.palette.mode === "dark" ? 0.08 : 0.05),
      }}
    >
      <Iconify icon="solar:danger-triangle-bold" width={15} sx={{ color: "#CA8A04", flexShrink: 0 }} />
      <Box flex={1} minWidth={0}>
        <Typography sx={{ typography: "s2", fontWeight: 700, color: "text.primary" }}>
          Reader completed with {issueCount} {issueCount === 1 ? "gap" : "gaps"}
          <Box component="span" sx={{ color: "text.subtitle", fontWeight: 500 }}>
            {" "}· {list} could not be read completely
          </Box>
        </Typography>
      </Box>
      <Button
        size="small" variant="text"
        onClick={onRetryAll}
        startIcon={<Iconify icon="solar:refresh-linear" width={13} />}
        sx={{ typography: "s3", fontWeight: 700, color: "text.primary" }}
      >
        Retry read
      </Button>
    </Stack>
  );
}
ReaderStatusBand.propTypes = {
  issueCount: PropTypes.number, issues: PropTypes.object, onRetryAll: PropTypes.func,
};

/* ── hard-fail page — takes over when reading returns nothing ── */

function HardFailPage({ agentRef, reason, onRetry, onChangeSource, onContinueWithDefaults }) {
  return (
    <Box sx={{ display: "flex", flexDirection: "column", height: "100%", minHeight: 0, bgcolor: "background.paper" }}>
      <Stack
        direction="row" alignItems="center" spacing={2}
        sx={{ px: 3, py: 1.75, borderBottom: "1px solid", borderColor: "divider", flexShrink: 0 }}
      >
        <Tooltip title="Back" arrow>
          <IconButton size="small" onClick={onChangeSource}>
            <Iconify icon="solar:alt-arrow-left-linear" width={17} />
          </IconButton>
        </Tooltip>
        <Typography sx={{ typography: "s2", color: "text.subtitle", fontFamily: "ui-monospace, Menlo, monospace" }}>
          {agentRef}
        </Typography>
      </Stack>

      <Stack alignItems="center" justifyContent="center" sx={{ flex: 1, px: 3, textAlign: "center" }}>
        <Box
          sx={{
            width: 56, height: 56, borderRadius: 2, display: "grid", placeItems: "center", mb: 2,
            bgcolor: (t) => alpha("#DC2626", t.palette.mode === "dark" ? 0.18 : 0.1),
            color: "#DC2626",
          }}
        >
          <Iconify icon="solar:danger-triangle-bold" width={28} />
        </Box>
        <Typography sx={{ typography: "m2", fontWeight: 700 }}>
          We couldn&rsquo;t read your agent
        </Typography>
        <Typography sx={{ typography: "s2", color: "text.secondary", mt: 1, maxWidth: 560 }}>
          {reason}
        </Typography>
        <Stack direction="row" spacing={1.5} sx={{ mt: 3 }}>
          <Button
            variant="contained" color="primary"
            onClick={onRetry}
            startIcon={<Iconify icon="solar:refresh-linear" width={15} />}
            sx={{ typography: "s2", fontWeight: 700 }}
          >
            Retry read
          </Button>
          <Button
            variant="outlined"
            onClick={onChangeSource}
            sx={{ typography: "s2", fontWeight: 700, color: "text.primary", borderColor: "divider" }}
          >
            Change source
          </Button>
        </Stack>
        <Link
          component="button"
          onClick={onContinueWithDefaults}
          underline="hover"
          sx={{ typography: "s3", color: "text.subtitle", mt: 2, fontWeight: 600 }}
        >
          Continue without reading — build with template defaults
        </Link>
      </Stack>
    </Box>
  );
}
HardFailPage.propTypes = {
  agentRef: PropTypes.string, reason: PropTypes.string,
  onRetry: PropTypes.func, onChangeSource: PropTypes.func, onContinueWithDefaults: PropTypes.func,
};

/* ── Claude-style AskUserQuestion card (matches IntakeStage in BuildFromAgent) ── */

function AskUserQuestionCard({
  step, total, question, answer, onPick, onOtherChange, onBack, onSkip, onNext, onBuild,
  canSubmit, isLast, skipped, openCount, resolvedCount, skippedCount,
}) {
  /* On the last question, the primary action becomes "Build the environment"
     — the questionnaire and the build are one continuous act. The
     button is disabled while any question is still open (not answered
     and not skipped); a tooltip explains what's still blocking. */
  const buildBlocked = openCount > 0 && !(canSubmit || skipped);
  const buildLabel = "Build the environment";
  const buildHint = buildBlocked
    ? `${openCount} open ${openCount === 1 ? "question" : "questions"} still block the build`
    : skippedCount > 0
      ? `${skippedCount} skipped — the affected facts will carry an INFERRED marker in the built world`
      : "";
  const options = question?.options || (question?.kind === "boolean"
    ? [{ id: "yes", label: "Yes" }, { id: "no", label: "No" }]
    : []);
  const otherIndex = options.length;
  const otherText = answer?.other?.trim() || "";
  const otherActive = otherText.length > 0;
  const rowIsSelected = (i) => answer?.pick === i;

  return (
    <Box
      sx={{
        borderRadius: 2,
        border: "1px solid",
        borderColor: (t) => alpha(t.palette.text.primary, t.palette.mode === "dark" ? 0.1 : 0.08),
        bgcolor: "background.paper",
        overflow: "hidden",
        mt: 1,
      }}
    >
      {/* ── header ── */}
      <Stack direction="row" alignItems="center" spacing={1} sx={{ px: 1.75, py: 1.25 }}>
        <Box
          sx={{
            px: 0.75, py: 0.125, borderRadius: 999, flexShrink: 0,
            bgcolor: (t) => alpha("#B45309", t.palette.mode === "dark" ? 0.28 : 0.16),
            color: "#B45309",
            typography: "s3", fontWeight: 700,
            fontVariantNumeric: "tabular-nums",
          }}
        >
          {step + 1}/{total}
        </Box>
        <Box flex={1} minWidth={0}>
          <Typography sx={{ typography: "s2", fontWeight: 700, minWidth: 0 }}>
            {question.title}
          </Typography>
          {question.why && (
            <Typography sx={{ typography: "s3", color: "text.subtitle", mt: 0.125 }}>
              {question.why}
            </Typography>
          )}
        </Box>
      </Stack>

      {/* ── options ── */}
      <Stack spacing={0.5} sx={{ px: 1.25, pb: 0.75 }}>
        {options.map((opt, i) => (
          <AskOptionRow
            key={opt.id || opt.label}
            label={opt.label}
            description={opt.description}
            selected={rowIsSelected(i)}
            index={i + 1}
            onSelect={() => onPick(i)}
          />
        ))}
        <AskOptionRow
          label="Other"
          selected={otherActive}
          index={otherIndex + 1}
          onSelect={() => { /* focus via input */ }}
          bottomChildren={(
            <TextField
              size="small" fullWidth variant="outlined"
              value={otherText}
              onChange={(e) => onOtherChange(e.target.value)}
              placeholder="Type your own answer here"
              InputProps={{
                sx: {
                  typography: "s2",
                  bgcolor: (t) => alpha(t.palette.text.primary, t.palette.mode === "dark" ? 0.06 : 0.03),
                  "& fieldset": { border: "none" },
                },
              }}
              sx={{ mt: 0.75 }}
            />
          )}
        />
      </Stack>

      {skipped && (
        <Stack
          direction="row" alignItems="center" spacing={0.75}
          sx={{
            mx: 1.5, mb: 1, px: 1.25, py: 0.75, borderRadius: 1,
            bgcolor: (t) => alpha("#CA8A04", t.palette.mode === "dark" ? 0.12 : 0.08),
            color: "#CA8A04",
            width: "fit-content",
          }}
        >
          <Iconify icon="solar:info-circle-linear" width={14} />
          <Typography sx={{ typography: "s3", fontWeight: 600 }}>
            Skipped — the affected fact will carry an INFERRED marker.
          </Typography>
        </Stack>
      )}

      {/* ── bottom bar ── */}
      <Stack direction="row" alignItems="center" sx={{ px: 1.5, py: 1.25 }}>
        {onBack ? (
          <Button
            size="small" onClick={onBack}
            sx={{
              typography: "s2", fontWeight: 600, color: "text.primary",
              bgcolor: (t) => alpha(t.palette.text.primary, t.palette.mode === "dark" ? 0.08 : 0.05),
              px: 1.5, borderRadius: 1,
              "&:hover": { bgcolor: (t) => alpha(t.palette.text.primary, t.palette.mode === "dark" ? 0.12 : 0.08) },
            }}
          >
            Back
          </Button>
        ) : <Box />}
        <Box flex={1} />
        <Stack direction="row" spacing={1}>
          <Button
            size="small" onClick={onSkip}
            sx={{
              typography: "s2", fontWeight: 600, color: "text.primary",
              bgcolor: (t) => alpha(t.palette.text.primary, t.palette.mode === "dark" ? 0.08 : 0.05),
              px: 1.5, borderRadius: 1,
              "&:hover": { bgcolor: (t) => alpha(t.palette.text.primary, t.palette.mode === "dark" ? 0.12 : 0.08) },
            }}
          >
            Skip
          </Button>
          {isLast ? (
            <Tooltip arrow title={buildHint}>
              <span>
                <Button
                  size="small"
                  variant="contained" color="primary"
                  onClick={() => onBuild?.()}
                  disabled={buildBlocked}
                  endIcon={<Iconify icon="solar:alt-arrow-right-linear" width={14} />}
                  sx={{ typography: "s2", fontWeight: 700, px: 1.5 }}
                >
                  {buildLabel}
                </Button>
              </span>
            </Tooltip>
          ) : (
            <Button
              size="small" onClick={onNext}
              disabled={!canSubmit}
              sx={{
                typography: "s2", fontWeight: 600, color: "text.primary",
                bgcolor: (t) => alpha(t.palette.text.primary, t.palette.mode === "dark" ? 0.16 : 0.09),
                px: 1.5, borderRadius: 1,
                "&:hover": { bgcolor: (t) => alpha(t.palette.text.primary, t.palette.mode === "dark" ? 0.22 : 0.14) },
                "&.Mui-disabled": {
                  color: "text.disabled",
                  bgcolor: (t) => alpha(t.palette.text.primary, t.palette.mode === "dark" ? 0.06 : 0.04),
                },
              }}
            >
              Next
            </Button>
          )}
        </Stack>
      </Stack>
    </Box>
  );
}
AskUserQuestionCard.propTypes = {
  step: PropTypes.number, total: PropTypes.number,
  question: PropTypes.object, answer: PropTypes.object,
  onPick: PropTypes.func, onOtherChange: PropTypes.func,
  onBack: PropTypes.func, onSkip: PropTypes.func, onNext: PropTypes.func,
  onBuild: PropTypes.func,
  canSubmit: PropTypes.bool, isLast: PropTypes.bool, skipped: PropTypes.bool,
  openCount: PropTypes.number, resolvedCount: PropTypes.number, skippedCount: PropTypes.number,
};

function AskOptionRow({ label, description, selected, index, onSelect, bottomChildren }) {
  return (
    <Box
      onClick={onSelect}
      sx={{
        p: 1.25, borderRadius: 1.25, cursor: "pointer",
        bgcolor: (t) => alpha(t.palette.text.primary, t.palette.mode === "dark" ? 0.05 : 0.03),
        "&:hover": {
          bgcolor: (t) => alpha(t.palette.text.primary, t.palette.mode === "dark" ? 0.08 : 0.05),
        },
      }}
    >
      <Stack direction="row" alignItems="flex-start" spacing={1.25}>
        <Box flex={1} minWidth={0}>
          <Typography sx={{ typography: "s2", fontWeight: 600, color: "text.primary" }}>
            {label}
          </Typography>
          {description && (
            <Typography sx={{ typography: "s3", color: "text.subtitle", mt: 0.125 }}>
              {description}
            </Typography>
          )}
        </Box>
        <Box
          sx={{
            minWidth: 22, height: 20, px: 0.75, borderRadius: 0.75, flexShrink: 0, mt: "1px",
            display: "grid", placeItems: "center",
            border: "1px solid",
            borderColor: selected ? "#7857FC" : (t) => alpha(t.palette.text.primary, t.palette.mode === "dark" ? 0.16 : 0.12),
            bgcolor: selected ? "#7857FC" : "transparent",
            color: selected ? "#fff" : "text.subtitle",
            typography: "s3", fontWeight: 700, fontVariantNumeric: "tabular-nums",
          }}
        >
          {index}
        </Box>
      </Stack>
      {bottomChildren}
    </Box>
  );
}
AskOptionRow.propTypes = {
  label: PropTypes.string, description: PropTypes.string,
  selected: PropTypes.bool, index: PropTypes.number,
  onSelect: PropTypes.func, bottomChildren: PropTypes.node,
};

/* ── LEGACY: keeping around during transition; removable once no callers ── */

function QuestionStepper({ questions, answers, activeIdx, setActiveIdx, onAnswer, onSkip }) {
  const total = questions.length;
  const q = questions[activeIdx];
  const answer = answers[q?.id];
  const isAnswered = answer?.value != null;
  const isSkipped = answer?.skipped;

  const goNext = () => setActiveIdx((i) => Math.min(total - 1, i + 1));
  const goBack = () => setActiveIdx((i) => Math.max(0, i - 1));

  return (
    <Box
      sx={{
        mt: 1,
        border: "1px solid", borderColor: "divider", borderRadius: 1.5,
        bgcolor: "background.paper", overflow: "hidden",
      }}
    >
      {/* progress row */}
      <Stack
        direction="row" alignItems="center" spacing={1}
        sx={{ px: 2, py: 1, borderBottom: "1px solid", borderColor: "divider" }}
      >
        {questions.map((qq, i) => {
          const a = answers[qq.id];
          const done = a?.value != null;
          const skipped = a?.skipped;
          const active = i === activeIdx;
          const bg = active
            ? "primary.main"
            : done
              ? "#16A34A"
              : skipped
                ? "#CA8A04"
                : "transparent";
          const border = active
            ? "primary.main"
            : done
              ? "#16A34A"
              : skipped
                ? "#CA8A04"
                : "divider";
          return (
            <Stack
              key={qq.id}
              direction="row" alignItems="center" spacing={0.5}
              onClick={() => setActiveIdx(i)}
              sx={{ cursor: "pointer" }}
            >
              <Box
                sx={{
                  width: 20, height: 20, borderRadius: "50%",
                  display: "grid", placeItems: "center",
                  border: "1.5px solid", borderColor: border,
                  bgcolor: active ? bg : "transparent",
                  color: active ? "primary.contrastText" : done ? "#16A34A" : skipped ? "#CA8A04" : "text.subtitle",
                }}
              >
                {done ? <Iconify icon="solar:check-circle-bold" width={12} />
                  : skipped ? <Iconify icon="solar:minus-circle-linear" width={12} />
                    : <Typography sx={{ typography: "s3", fontWeight: 700 }}>{i + 1}</Typography>}
              </Box>
              {i < total - 1 && (
                <Box sx={{ width: 22, height: 1, bgcolor: "divider" }} />
              )}
            </Stack>
          );
        })}
        <Box flex={1} />
        <Typography sx={{ typography: "s3", color: "text.subtitle", fontVariantNumeric: "tabular-nums" }}>
          {activeIdx + 1} / {total}
        </Typography>
      </Stack>

      {/* question body */}
      <Box sx={{ px: 2.5, py: 2 }}>
        <Typography sx={{ typography: "s1", fontWeight: 700 }}>{q.title}</Typography>
        <Typography sx={{ typography: "s2", color: "text.secondary", mt: 0.5, maxWidth: 780 }}>{q.why}</Typography>

        <Box sx={{ mt: 1.5 }}>
          {q.kind === "boolean" && (
            <Stack direction="row" spacing={1} flexWrap="wrap" rowGap={1}>
              {[{ id: "yes", label: "Yes, enforced" }, { id: "no", label: "No, prompt only" }].map((opt) => (
                <ChoiceButton
                  key={opt.id}
                  label={opt.label}
                  active={answer?.value === opt.id}
                  onClick={() => onAnswer(q.id, opt.id)}
                />
              ))}
            </Stack>
          )}
          {q.kind === "choice" && (
            <Stack direction="row" spacing={1} flexWrap="wrap" rowGap={1}>
              {(q.options || []).map((opt) => (
                <ChoiceButton
                  key={opt.id}
                  label={opt.label}
                  active={answer?.value === opt.id}
                  onClick={() => onAnswer(q.id, opt.id)}
                />
              ))}
            </Stack>
          )}
          {q.kind === "text" && (
            <TextField
              size="small" fullWidth multiline minRows={2} maxRows={4}
              placeholder="Your answer…"
              value={answer && !answer.skipped ? (answer.value || "") : ""}
              onChange={(e) => onAnswer(q.id, e.target.value)}
            />
          )}
        </Box>

        {isSkipped && (
          <Stack
            direction="row" alignItems="center" spacing={0.75}
            sx={{
              mt: 2, px: 1.25, py: 0.75, borderRadius: 1,
              bgcolor: (t) => alpha("#CA8A04", t.palette.mode === "dark" ? 0.12 : 0.08),
              color: "#CA8A04",
              width: "fit-content",
            }}
          >
            <Iconify icon="solar:info-circle-linear" width={14} />
            <Typography sx={{ typography: "s3", fontWeight: 600 }}>
              Skipped — the affected fact will carry an INFERRED marker in the built world.
            </Typography>
          </Stack>
        )}
      </Box>

      <Divider />

      {/* nav footer */}
      <Stack direction="row" alignItems="center" spacing={1} sx={{ px: 2.5, py: 1.25 }}>
        <Button
          size="small" variant="text"
          disabled={activeIdx === 0}
          onClick={goBack}
          startIcon={<Iconify icon="solar:alt-arrow-left-linear" width={14} />}
          sx={{ typography: "s2", fontWeight: 600, color: "text.secondary" }}
        >
          Previous
        </Button>
        <Box flex={1} />
        {!isAnswered && !isSkipped && (
          <Button
            size="small" variant="outlined"
            onClick={() => { onSkip(q.id); if (activeIdx < total - 1) goNext(); }}
            sx={{ typography: "s2", fontWeight: 600, color: "text.secondary", borderColor: "divider" }}
          >
            Skip for now
          </Button>
        )}
        <Button
          size="small" variant={isAnswered ? "contained" : "outlined"}
          onClick={goNext}
          disabled={activeIdx === total - 1}
          endIcon={<Iconify icon="solar:alt-arrow-right-linear" width={14} />}
          sx={{
            typography: "s2", fontWeight: 700,
            ...(isAnswered ? {} : { color: "text.primary", borderColor: "divider" }),
          }}
        >
          Next
        </Button>
      </Stack>
    </Box>
  );
}
QuestionStepper.propTypes = {
  questions: PropTypes.array, answers: PropTypes.object,
  activeIdx: PropTypes.number, setActiveIdx: PropTypes.func,
  onAnswer: PropTypes.func, onSkip: PropTypes.func,
};

function ChoiceButton({ label, active, onClick }) {
  return (
    <Button
      size="small" variant={active ? "contained" : "outlined"}
      onClick={onClick}
      sx={{
        typography: "s2", fontWeight: 700, px: 1.5,
        ...(active ? {} : { color: "text.primary", borderColor: "divider" }),
      }}
    >
      {label}
    </Button>
  );
}
ChoiceButton.propTypes = { label: PropTypes.string, active: PropTypes.bool, onClick: PropTypes.func };
