import PropTypes from "prop-types";
import { useEffect, useMemo, useRef, useState } from "react";
import { alpha } from "@mui/material/styles";
import { Box, Stack, Typography, Button, TextField } from "@mui/material";
import Iconify from "src/components/iconify";
import { OriginChip } from "../components/primitives";
import { buildTemplateReading, buildTemplateQuestions } from "./TemplateReviewLayout";

/**
 * The read-audit, inline in the Connect-your-agent right column.
 *
 * Replaces the old two-step detour (fit-check popup → a separate
 * "What we read from your agent" screen). Clicking "Check it fits"
 * mounts this in the right column: it streams the fit probe, then
 * shows what the reader extracted from the template plus the open
 * questions the user has to resolve — all without leaving the connect
 * page. When every question is answered, "Build the environment"
 * hands the answers back and the review layout takes over.
 */

const SECTIONS = [
  { key: "tools", title: "Tools it can call", icon: "solar:code-square-linear" },
  { key: "rules", title: "Rules it must hold", icon: "solar:shield-check-linear" },
  { key: "data", title: "Data it starts with", icon: "solar:database-linear" },
  { key: "behavior", title: "How it behaves", icon: "solar:playlist-linear" },
];

const isResolved = (a) => !!a && (a.pick != null || (a.other || "").trim().length > 0 || a.skipped);

/* Demo failed-reader states — sections the reader couldn't complete. Mirrors
   the full-screen receipt: no policy.yaml (rules), call-graph timeout (data).
   "Retry read" clears them so the demo can show the recovery to a clean read. */
const DEFAULT_DEMO_ISSUES = {
  rules: {
    icon: "solar:file-remove-linear",
    message: "No policy.yaml found in the repo",
    hint: "Rules can still be captured — add them manually or point us at a different branch.",
  },
  data: {
    icon: "solar:clock-circle-linear",
    message: "Call-graph timed out at 30s — 2 fixtures not resolved",
    hint: "The scan is deterministic; retrying often succeeds once other jobs free the workers.",
  },
};

export default function ConnectReadPanel({ env, probeSteps, onBuild }) {
  const [phase, setPhase] = useState("checking"); // checking | read
  const reading = useMemo(() => buildTemplateReading(env), [env]);
  const questions = useMemo(() => buildTemplateQuestions(env), [env]);
  const [answers, setAnswers] = useState({});
  const [activeIdx, setActiveIdx] = useState(0);
  /* Seed the demo gaps so the panel opens on a warning read, then clears
     to a clean read on retry. */
  const [sectionIssues, setSectionIssues] = useState(DEFAULT_DEMO_ISSUES);
  const issueKeys = Object.keys(sectionIssues);
  const retryAll = () => setSectionIssues({});
  const retrySection = (key) => setSectionIssues((prev) => {
    const next = { ...prev }; delete next[key]; return next;
  });

  if (phase === "checking") {
    return <CheckingCard steps={probeSteps || []} onDone={() => setPhase("read")} />;
  }

  const resolvedCount = questions.filter((q) => isResolved(answers[q.id])).length;
  const allDone = resolvedCount === questions.length;
  const q = questions[activeIdx];
  const isLast = activeIdx === questions.length - 1;
  const options = q?.options
    || (q?.kind === "boolean" ? [{ id: "yes", label: "Yes" }, { id: "no", label: "No" }] : []);

  const setAnswer = (id, patch) =>
    setAnswers((prev) => ({ ...prev, [id]: { ...prev[id], skipped: false, ...patch } }));

  const pick = (idx) => {
    setAnswer(q.id, { pick: idx });
    const pickedOther = idx === options.length;
    if (!pickedOther && !isLast) setActiveIdx((i) => i + 1);
  };
  const skip = () => {
    setAnswer(q.id, { skipped: true, pick: null });
    if (!isLast) setActiveIdx((i) => i + 1);
  };

  return (
    <Stack spacing={1.5}>
      {/* ── header ── */}
      <Box>
        <Stack direction="row" alignItems="center" spacing={0.875}>
          <Iconify icon="solar:check-circle-bold" width={15} sx={{ color: "#16A34A", flexShrink: 0 }} />
          <Typography sx={{ typography: "s2", fontWeight: 700 }}>What we read from your agent</Typography>
        </Stack>
        <Typography sx={{ typography: "s3", color: "text.subtitle", mt: 0.125 }}>
          Every fact is tagged by how we know it. Resolve the open questions before building.
        </Typography>
      </Box>

      {/* ── reader-status band — surfaces the sections the read couldn't complete ── */}
      {issueKeys.length > 0 && (
        <Stack
          direction="row" alignItems="center" spacing={1}
          sx={{
            px: 1.5, py: 1, borderRadius: 1,
            border: "1px solid", borderColor: (t) => alpha("#CA8A04", 0.35),
            bgcolor: (t) => alpha("#CA8A04", t.palette.mode === "dark" ? 0.1 : 0.05),
          }}
        >
          <Iconify icon="solar:danger-triangle-bold" width={14} sx={{ color: "#CA8A04", flexShrink: 0 }} />
          <Typography sx={{ typography: "s3", fontWeight: 700, flex: 1, minWidth: 0 }}>
            Reader completed with {issueKeys.length} {issueKeys.length === 1 ? "gap" : "gaps"}
            <Box component="span" sx={{ color: "text.subtitle", fontWeight: 500 }}> · {issueKeys.join(", ")} incomplete</Box>
          </Typography>
          <Button
            size="small" variant="text" onClick={retryAll}
            startIcon={<Iconify icon="solar:refresh-linear" width={12} />}
            sx={{ typography: "s3", fontWeight: 700, color: "text.primary", flexShrink: 0, minWidth: 0 }}
          >
            Retry read
          </Button>
        </Stack>
      )}

      {/* ── fact sections — two per row to keep the panel short ── */}
      <Box sx={{ display: "grid", gridTemplateColumns: { xs: "1fr", sm: "1fr 1fr" }, gap: 1 }}>
        {SECTIONS.map((s) => (
          <Box
            key={s.key}
            sx={{ border: "1px solid", borderColor: "divider", borderRadius: 1.25, overflow: "hidden", minWidth: 0 }}
          >
            <FactSection
              icon={s.icon} title={s.title}
              facts={reading[s.key] || []}
              issue={sectionIssues[s.key]}
              onRetry={() => retrySection(s.key)}
            />
          </Box>
        ))}
      </Box>

      {/* ── open questions ── */}
      {questions.length > 0 && q && (
        <Box sx={{ borderRadius: 1.25, border: "1px solid", borderColor: "divider", overflow: "hidden" }}>
          <Stack
            direction="row" alignItems="center" spacing={1}
            sx={{ px: 1.75, py: 1.25, borderBottom: "1px solid", borderColor: "divider", bgcolor: (t) => alpha(t.palette.text.primary, t.palette.mode === "dark" ? 0.03 : 0.02) }}
          >
            <Iconify icon="solar:question-circle-linear" width={15} sx={{ color: allDone ? "#16A34A" : "#DC2626", flexShrink: 0 }} />
            <Typography sx={{ typography: "s2", fontWeight: 700, flex: 1 }}>Open questions</Typography>
            <Typography sx={{ typography: "s3", color: "text.subtitle", fontVariantNumeric: "tabular-nums" }}>
              {resolvedCount} of {questions.length} resolved
            </Typography>
          </Stack>

          <Box sx={{ px: 1.75, py: 1.75 }}>
            <Stack direction="row" alignItems="center" spacing={0.875} sx={{ mb: 0.5 }}>
              <Box sx={{
                px: 0.625, py: 0.125, borderRadius: 0.5, flexShrink: 0,
                typography: "s3", fontWeight: 700, color: "primary.main",
                bgcolor: (t) => alpha(t.palette.primary.main, t.palette.mode === "dark" ? 0.16 : 0.1),
              }}>
                {activeIdx + 1}/{questions.length}
              </Box>
              <Typography sx={{ typography: "s2", fontWeight: 700 }}>{q.title}</Typography>
            </Stack>
            <Typography sx={{ typography: "s3", color: "text.subtitle", mb: 1.25 }}>{q.why}</Typography>

            <Stack spacing={0.75}>
              {options.map((opt, i) => {
                const selected = answers[q.id]?.pick === i;
                return (
                  <OptionRow
                    key={opt.id} label={opt.label} index={i + 1}
                    selected={selected} onSelect={() => pick(i)}
                  />
                );
              })}
              {/* Other */}
              <OptionRow
                label="Other" index={options.length + 1}
                selected={answers[q.id]?.pick === options.length}
                onSelect={() => setAnswer(q.id, { pick: options.length })}
              />
              {answers[q.id]?.pick === options.length && (
                <TextField
                  size="small" fullWidth autoFocus
                  placeholder="Type your own answer here"
                  value={answers[q.id]?.other || ""}
                  onChange={(e) => setAnswer(q.id, { other: e.target.value })}
                  sx={{ "& .MuiInputBase-input": { typography: "s2" } }}
                />
              )}
            </Stack>

            <Stack direction="row" alignItems="center" spacing={1} sx={{ mt: 1.5 }}>
              {activeIdx > 0 && (
                <Button
                  size="small" onClick={() => setActiveIdx((i) => Math.max(0, i - 1))}
                  sx={{ typography: "s2", fontWeight: 700, color: "text.subtitle" }}
                >
                  Back
                </Button>
              )}
              <Box flex={1} />
              {/* The last question is the final ambiguity — it must be
                  answered, so no Skip there; earlier ones can be deferred. */}
              {!isLast && (
                <Button
                  size="small" onClick={skip}
                  sx={{ typography: "s2", fontWeight: 700, color: "text.subtitle" }}
                >
                  Skip
                </Button>
              )}
              {!isLast && (
                <Button
                  size="small" variant="outlined"
                  disabled={!isResolved(answers[q.id])}
                  onClick={() => setActiveIdx((i) => Math.min(questions.length - 1, i + 1))}
                  sx={{ typography: "s2", fontWeight: 700, color: "text.primary", borderColor: "divider" }}
                >
                  Next
                </Button>
              )}
            </Stack>
          </Box>
        </Box>
      )}

      {/* ── build ── */}
      <Button
        fullWidth variant="contained" color="primary"
        disabled={!allDone}
        onClick={() => onBuild?.(answers)}
        endIcon={<Iconify icon="solar:arrow-right-linear" width={15} />}
        sx={{ typography: "s2", fontWeight: 700 }}
      >
        Build the environment
      </Button>
      {!allDone && (
        <Typography sx={{ typography: "s3", color: "text.subtitle", textAlign: "center", mt: -1 }}>
          {questions.length - resolvedCount} open {questions.length - resolvedCount === 1 ? "question" : "questions"} still to resolve
        </Typography>
      )}
    </Stack>
  );
}
ConnectReadPanel.propTypes = {
  env: PropTypes.object, probeSteps: PropTypes.array, onBuild: PropTypes.func,
};

/* ── the fit-check probe, as a live handshake ──────────────────────────────
 *
 * An animated probe between the agent and the template world: a pulse travels
 * the link while each check resolves in turn (spinner → green tick). Reads as
 * a live diagnostic rather than a plain streaming log. */
const ACCENT = "#7857FC";
const OK = "#16A34A";

function CheckingCard({ steps, onDone }) {
  const checks = useMemo(() => {
    /* Whatever the probe declared, followed by the rest of the fit sequence,
       so the check reads as a thorough diagnostic rather than three lines. */
    const declared = (steps || []).map((p) => ({ label: p.label, result: p.result }));
    const base = declared.length ? declared : [
      { label: "connect()", result: "handshake ok" },
      { label: "list_tools()", result: "8 declared" },
      { label: "compare()", result: "6 of 8 this world uses" },
    ];
    const rest = [
      { label: "match_arguments()", result: "signatures line up" },
      { label: "probe_world()", result: "every tool answers from state" },
      { label: "check_isolation()", result: "sandbox sealed · no egress" },
      { label: "seed_world()", result: "fixtures loaded" },
      { label: "verify_ready()", result: "world stands up" },
    ];
    return [...base, ...rest];
  }, [steps]);

  const total = checks.length;
  const [done, setDone] = useState(0);
  const onDoneRef = useRef(onDone);
  onDoneRef.current = onDone;

  useEffect(() => {
    const timers = [];
    for (let i = 1; i <= total; i += 1) {
      timers.push(setTimeout(() => setDone(i), i * 460));
    }
    timers.push(setTimeout(() => onDoneRef.current?.(), total * 460 + 400));
    return () => timers.forEach(clearTimeout);
  }, [total]);

  const pct = Math.round((done / total) * 100);

  return (
    <Box sx={{ borderRadius: 1.5, border: "1px solid", borderColor: "divider", overflow: "hidden", bgcolor: "background.paper" }}>
      {/* ── handshake visual ── */}
      <Box
        sx={{
          position: "relative", px: 2.5, pt: 3, pb: 2.5,
          bgcolor: (t) => alpha(t.palette.text.primary, t.palette.mode === "dark" ? 0.025 : 0.015),
          borderBottom: "1px solid", borderColor: "divider",
          "@keyframes crp-travel": { "0%": { left: "0%", opacity: 0 }, "12%": { opacity: 1 }, "88%": { opacity: 1 }, "100%": { left: "100%", opacity: 0 } },
          "@keyframes crp-spin": { to: { transform: "rotate(360deg)" } },
        }}
      >
        <Stack direction="row" alignItems="center" justifyContent="space-between" spacing={1.5}>
          <Node icon="solar:programming-linear" label="Your agent" />
          {/* link */}
          <Box sx={{ flex: 1, position: "relative", height: 2, mx: 0.5, borderRadius: 1, bgcolor: (t) => alpha(t.palette.text.primary, 0.1) }}>
            <Box sx={{
              position: "absolute", top: 0, left: 0, height: "100%", borderRadius: 1,
              width: `${pct}%`, bgcolor: (t) => alpha(ACCENT, 0.6), transition: "width 500ms ease",
            }} />
            <Box sx={{
              position: "absolute", top: "50%", width: 6, height: 6, borderRadius: "50%",
              bgcolor: ACCENT, transform: "translate(-50%, -50%)",
              animation: "crp-travel 1.6s linear infinite",
            }} />
          </Box>
          <Node icon="solar:database-linear" label="This world" />
        </Stack>

        <Typography sx={{ typography: "s2", fontWeight: 700, textAlign: "center", mt: 2 }}>
          {done < total ? "Checking your agent fits" : "Your agent fits this world"}
        </Typography>
        <Typography sx={{ typography: "s3", color: "text.subtitle", textAlign: "center", mt: 0.25 }}>
          {done < total ? "Probing declared tools against what this template calls" : "Reading what it declares…"}
        </Typography>
      </Box>

      {/* ── checklist ── */}
      <Stack sx={{ px: 2, py: 1.5 }} spacing={0}>
        {checks.map((c, i) => {
          const state = i < done ? "done" : i === done ? "running" : "pending";
          return (
            <Stack
              key={c.label} direction="row" alignItems="center" spacing={1.25}
              sx={{ py: 0.75, opacity: state === "pending" ? 0.4 : 1, transition: "opacity 300ms" }}
            >
              <Box sx={{ width: 16, height: 16, flexShrink: 0, display: "grid", placeItems: "center" }}>
                {state === "done" && <Iconify icon="solar:check-circle-bold" width={16} sx={{ color: OK }} />}
                {state === "running" && (
                  <Iconify icon="solar:refresh-linear" width={14} sx={{ color: "text.subtitle", animation: "crp-spin 0.8s linear infinite" }} />
                )}
                {state === "pending" && <Box sx={{ width: 6, height: 6, borderRadius: "50%", bgcolor: "text.disabled" }} />}
              </Box>
              <Typography sx={{ typography: "s2", fontWeight: 600, flex: 1, minWidth: 0 }} noWrap>
                {c.label}
              </Typography>
              {state === "done" && (
                <Typography sx={{ typography: "s3", color: "text.subtitle", flexShrink: 0 }} noWrap>
                  {c.result}
                </Typography>
              )}
            </Stack>
          );
        })}
      </Stack>

      <Box sx={{ px: 2, pb: 1.5 }}>
        <Typography sx={{ typography: "s3", color: "text.subtitle", textAlign: "center" }}>
          Prototype resolves in a few seconds · in production this is a live probe
        </Typography>
      </Box>
    </Box>
  );
}
CheckingCard.propTypes = { steps: PropTypes.array, onDone: PropTypes.func };

function Node({ icon, label }) {
  return (
    <Stack alignItems="center" spacing={0.625} sx={{ flexShrink: 0, width: 72 }}>
      <Box sx={{
        width: 38, height: 38, borderRadius: 1.5, display: "grid", placeItems: "center",
        border: "1px solid", borderColor: "divider",
        bgcolor: (t) => alpha(t.palette.text.primary, t.palette.mode === "dark" ? 0.05 : 0.03),
        color: "text.secondary",
      }}>
        <Iconify icon={icon} width={18} />
      </Box>
      <Typography sx={{ typography: "s3", fontWeight: 700, color: "text.secondary", textAlign: "center" }}>{label}</Typography>
    </Stack>
  );
}
Node.propTypes = { icon: PropTypes.string, label: PropTypes.string };

function FactSection({ icon, title, facts, issue, onRetry }) {
  return (
    <Box sx={{ px: 1.5, py: 1, ...(issue && { bgcolor: (t) => alpha("#CA8A04", t.palette.mode === "dark" ? 0.06 : 0.03) }) }}>
      <Stack direction="row" alignItems="center" spacing={0.75} sx={{ mb: (facts.length || issue) ? 0.5 : 0 }}>
        <Iconify icon={icon} width={13} sx={{ color: issue ? "#CA8A04" : "text.subtitle", flexShrink: 0 }} />
        <Typography sx={{ typography: "s3", fontWeight: 700, flex: 1 }}>{title}</Typography>
        <Typography sx={{ typography: "s3", color: issue ? "#CA8A04" : "text.subtitle", fontVariantNumeric: "tabular-nums" }}>
          {issue ? "—" : facts.length}
        </Typography>
      </Stack>

      {issue ? (
        <Stack direction="row" alignItems="flex-start" spacing={0.875}>
          <Iconify icon={issue.icon || "solar:danger-triangle-linear"} width={13} sx={{ color: "#CA8A04", flexShrink: 0, mt: "2px" }} />
          <Box flex={1} minWidth={0}>
            <Typography sx={{ fontSize: 11.5, lineHeight: 1.4, fontWeight: 700 }}>{issue.message}</Typography>
            {issue.hint && (
              <Typography sx={{ fontSize: 11, lineHeight: 1.4, color: "text.subtitle", mt: 0.125 }}>{issue.hint}</Typography>
            )}
            <Button
              size="small" variant="text" onClick={onRetry}
              startIcon={<Iconify icon="solar:refresh-linear" width={11} />}
              sx={{ typography: "s3", fontWeight: 700, color: "text.primary", mt: 0.25, px: 0, minWidth: 0 }}
            >
              Retry read
            </Button>
          </Box>
        </Stack>
      ) : (
        <Stack>
          {facts.map((f) => (
            <Stack key={f.name} direction="row" alignItems="center" spacing={1} sx={{ py: 0.25 }}>
              <Typography noWrap sx={{ fontSize: 11, lineHeight: 1.4, fontWeight: 600, fontFamily: "ui-monospace, Menlo, monospace", flex: 1, minWidth: 0 }}>
                {f.name}
              </Typography>
              {f.note && (
                <Typography noWrap sx={{ fontSize: 11, lineHeight: 1.4, color: "text.subtitle", flexShrink: 0 }}>{f.note}</Typography>
              )}
              {f.origin && <OriginChip origin={f.origin} showPath={false} />}
            </Stack>
          ))}
        </Stack>
      )}
    </Box>
  );
}
FactSection.propTypes = {
  icon: PropTypes.string, title: PropTypes.string, facts: PropTypes.array,
  issue: PropTypes.object, onRetry: PropTypes.func,
};

function OptionRow({ label, index, selected, onSelect }) {
  return (
    <Box
      role="button"
      tabIndex={0}
      onClick={onSelect}
      onKeyDown={(e) => { if (e.key === "Enter" || e.key === " ") onSelect(); }}
      sx={{
        display: "flex", alignItems: "center", gap: 1,
        px: 1.25, py: 0.875, borderRadius: 1, cursor: "pointer",
        border: "1px solid",
        borderColor: selected ? "primary.main" : "divider",
        bgcolor: (t) => selected ? alpha(t.palette.primary.main, t.palette.mode === "dark" ? 0.14 : 0.06) : "transparent",
        transition: "border-color 120ms, background-color 120ms",
        "&:hover": { borderColor: selected ? "primary.main" : "text.disabled" },
      }}
    >
      <Typography sx={{ typography: "s2", fontWeight: 600, flex: 1 }}>{label}</Typography>
      <Box sx={{
        minWidth: 20, height: 20, px: 0.5, borderRadius: 0.75, flexShrink: 0,
        display: "grid", placeItems: "center",
        typography: "s3", fontWeight: 700,
        color: selected ? "primary.main" : "text.disabled",
        bgcolor: (t) => selected ? alpha(t.palette.primary.main, t.palette.mode === "dark" ? 0.2 : 0.12) : alpha(t.palette.text.primary, 0.05),
      }}>
        {index}
      </Box>
    </Box>
  );
}
OptionRow.propTypes = {
  label: PropTypes.string, index: PropTypes.number, selected: PropTypes.bool, onSelect: PropTypes.func,
};
