import PropTypes from "prop-types";
import { useEffect, useMemo, useRef, useState } from "react";
import { alpha, useTheme } from "@mui/material/styles";
import { Box, IconButton, Stack, Tooltip, Typography } from "@mui/material";
import Iconify from "src/components/iconify";
import { StatusDot, ScorePill } from "../components/primitives";
import { traceOf, spanTree, fmtMs } from "../_mock/runTrace";
import { clickable, focusRing } from "../components/clickable";
import { scenarioTitle } from "../_mock/scenarios";
import CostStrip from "./CostStrip";
import { resolveEval } from "../_mock/evals";
import { judgeCreditsOf, judgesOf, usageOfTask } from "../_mock/pricing";

/* The agent's own model spend, billed to the customer's key — dollars, never
   Future AGI credits. */
const keyUsd = (usd) => (usd == null ? "—" : `≈ $${Number(usd).toFixed(2)}`);
const fmtN = (n) => Math.round(n).toLocaleString();

/**
 * The trace of one scenario, as a waterfall (Story 11 — AC-11.3, 11.7, 11.9,
 * 11.10). Parent → child on the left, one shared time axis on the right; a
 * failed scenario opens pinned to the span where it went wrong.
 */
const KIND = {
  session: { icon: "solar:routing-2-linear", label: "Session" },
  turn: { icon: "solar:user-speak-rounded-linear", label: "Agent turn" },
  caller: { icon: "solar:user-rounded-linear", label: "Caller turn" },
  actor: { icon: "solar:users-group-rounded-linear", label: "Third party" },
  llm: { icon: "solar:cpu-bolt-linear", label: "LLM call" },
  tool: { icon: "solar:code-square-linear", label: "Tool call" },
  guard: { icon: "solar:shield-check-linear", label: "Guard" },
  missing: { icon: "solar:forbidden-circle-linear", label: "Never called" },
};
const NAME_W = 260;
const DUR_W = 64;

const barColor = (s, theme) => {
  if (s.status === "error" || s.kind === "missing")
    return theme.palette.status.danger.solid;
  if (s.kind === "llm") return theme.palette.primary.main;
  if (s.kind === "tool") return theme.palette.text.secondary;
  if (s.kind === "guard") return alpha(theme.palette.primary.main, 0.55);
  if (s.kind === "turn")
    return alpha(
      theme.palette.primary.main,
      theme.palette.mode === "dark" ? 0.4 : 0.28,
    );
  return alpha(
    theme.palette.text.primary,
    theme.palette.mode === "dark" ? 0.22 : 0.14,
  );
};

export default function TraceWaterfall({
  task,
  env,
  envState,
  focusSpanId,
  showCost,
}) {
  const theme = useTheme();
  /* What this scenario's calls used — the same rules as the run's Cost tab. */
  const usage = useMemo(() => {
    const evals = (envState?.evals || []).map(resolveEval).filter(Boolean);
    return usageOfTask(task, {
      voice: ["voice", "multi"].includes(env?.surface),
      judgeCredits: judgeCreditsOf(evals),
      judges: judgesOf(evals),
      repeats: task?.samples?.length || 1,
    });
  }, [task, env, envState]);
  const perCall = usage.samples ? usage.credits / usage.samples : 0;
  const trace = useMemo(
    () => traceOf(task, env, envState),
    [task, env, envState],
  );
  const rows = useMemo(() => spanTree(trace.spans), [trace]);
  const [selected, setSelected] = useState(
    () => focusSpanId || trace.offendingId || trace.sessionId,
  );
  const [closed, setClosed] = useState(() => new Set());
  const listRef = useRef(null);

  /* Open on the offending span, scrolled into view. */
  useEffect(() => {
    const id = focusSpanId || trace.offendingId;
    if (!id) return;
    setSelected(id);
    const el = listRef.current?.querySelector(
      `[data-span="${CSS.escape(id)}"]`,
    );
    el?.scrollIntoView?.({ block: "nearest" });
  }, [focusSpanId, trace.offendingId]);

  const byId = useMemo(
    () => new Map(trace.spans.map((s) => [s.id, s])),
    [trace],
  );
  const hidden = (s) => {
    let p = s.parentId;
    while (p) {
      if (closed.has(p)) return true;
      p = byId.get(p)?.parentId;
    }
    return false;
  };
  const visible = rows.filter((s) => !hidden(s));
  const total = trace.total || 1;
  const pos = (ms) => `${Math.min(100, (ms / total) * 100)}%`;
  const llm = trace.spans.filter((s) => s.kind === "llm");
  const tools = trace.spans.filter((s) => s.kind === "tool");
  const tokens = llm.reduce((a, s) => a + (s.tokens || 0), 0);
  const cost = llm.reduce((a, s) => a + (s.cost || 0), 0);
  const offending = trace.offendingId ? byId.get(trace.offendingId) : null;
  const offendingPath = offending
    ? [byId.get(offending.parentId)?.name, offending.name]
        .filter(Boolean)
        .join(" › ")
    : null;
  const sel = byId.get(selected) || trace.spans[0];

  const toggle = (id) =>
    setClosed((prev) => {
      const next = new Set(prev);
      if (next.has(id)) next.delete(id);
      else next.add(id);
      return next;
    });

  if (!trace.spans.length) {
    return (
      <Typography sx={{ p: 2, typography: "s2", color: "text.tertiary" }}>
        This scenario recorded no steps, so there is no trace.
      </Typography>
    );
  }

  return (
    <Stack sx={{ minHeight: 0 }}>
      {/* ── opened from the Cost tab: what this scenario's calls cost ── */}
      {showCost && <CostStrip task={task} env={env} envState={envState} />}
      {/* ── summary ── */}
      <Stack
        direction="row"
        alignItems="center"
        spacing={1.5}
        sx={{
          px: 2,
          py: 1.25,
          borderBottom: "1px solid",
          borderColor: "divider",
          flexWrap: "wrap",
          rowGap: 0.5,
        }}
      >
        <Typography sx={{ typography: "s2", fontWeight: 600 }}>
          {`${trace.spans.length} spans · ${fmtMs(trace.total)}${usage.charged ? ` · ${fmtN(perCall)} credits` : " · not charged"}`}
        </Typography>
        <Typography
          sx={{
            typography: "s3",
            color: "text.tertiary",
            fontVariantNumeric: "tabular-nums",
          }}
        >
          {`${llm.length} LLM · ${tools.length} tool · ${tokens.toLocaleString()} tokens · your model ${keyUsd(cost)} on your key · ${trace.model}`}
        </Typography>
        <Box flex={1} />
        {offending && (
          <Box
            role="button"
            tabIndex={0}
            onClick={() => setSelected(offending.id)}
            onKeyDown={(e) => {
              if (e.key === "Enter") setSelected(offending.id);
            }}
            sx={{
              display: "inline-flex",
              alignItems: "center",
              gap: 0.5,
              cursor: "pointer",
              typography: "s3",
              fontWeight: 600,
              color: "status.danger.fg",
            }}
          >
            <Iconify icon="solar:danger-triangle-linear" width={14} />
            {`Went wrong at ${offendingPath}`}
          </Box>
        )}
      </Stack>
      {trace.unmeasured && trace.spans[0]?.note && (
        <Typography
          sx={{
            px: 2,
            py: 1,
            typography: "s3",
            color: "text.tertiary",
            borderBottom: "1px solid",
            borderColor: "divider",
          }}
        >
          {`No verdict — ${trace.spans[0].note}`}
        </Typography>
      )}

      {/* ── axis ── */}
      <Stack
        direction="row"
        alignItems="center"
        sx={{ px: 2, py: 1, borderBottom: "1px solid", borderColor: "divider" }}
      >
        <Typography
          sx={{
            width: NAME_W,
            flexShrink: 0,
            fontSize: 13,
            fontWeight: 500,
            color: "text.secondary",
          }}
        >
          Span
        </Typography>
        <Box sx={{ flex: 1, position: "relative", height: 16, minWidth: 120 }}>
          {[0, 0.25, 0.5, 0.75, 1].map((f) => (
            <Typography
              key={f}
              sx={{
                position: "absolute",
                left: `${f * 100}%`,
                transform:
                  f === 1
                    ? "translateX(-100%)"
                    : f === 0
                      ? "none"
                      : "translateX(-50%)",
                typography: "s3",
                color: "text.tertiary",
                fontVariantNumeric: "tabular-nums",
                whiteSpace: "nowrap",
              }}
            >
              {fmtMs(total * f)}
            </Typography>
          ))}
        </Box>
        <Typography
          sx={{
            width: DUR_W,
            flexShrink: 0,
            textAlign: "right",
            typography: "s3",
            fontWeight: 600,
            color: "text.tertiary",
            textTransform: "none",
            letterSpacing: 0.4,
          }}
        >
          Time
        </Typography>
      </Stack>

      {/* ── rows ── */}
      <Box ref={listRef}>
        {visible.map((s) => {
          const k = KIND[s.kind] || KIND.tool;
          const isSel = s.id === sel?.id;
          const bad = s.offending;
          const err = s.status === "error" || s.kind === "missing";
          return (
            <Stack
              key={s.id}
              data-span={s.id}
              direction="row"
              alignItems="center"
              {...clickable(() => setSelected(s.id), { pressed: isSel })}
              sx={{
                px: 2,
                height: 28,
                cursor: "pointer",
                borderBottom: "1px solid",
                borderColor: "divider",
                bgcolor: (t) =>
                  bad
                    ? t.palette.status.danger.bg
                    : isSel
                      ? t.palette.action.selected
                      : "transparent",
                "&:hover": {
                  bgcolor: (t) =>
                    bad ? t.palette.status.danger.bg : t.palette.action.hover,
                },
                ...focusRing,
              }}
            >
              <Stack
                direction="row"
                alignItems="center"
                spacing={0.5}
                sx={{
                  width: NAME_W,
                  flexShrink: 0,
                  minWidth: 0,
                  pl: s.depth * 1.75,
                }}
              >
                {s.hasChildren ? (
                  <IconButton
                    size="small"
                    onClick={(e) => {
                      e.stopPropagation();
                      toggle(s.id);
                    }}
                  >
                    <Iconify
                      icon={
                        closed.has(s.id)
                          ? "solar:alt-arrow-right-linear"
                          : "solar:alt-arrow-down-linear"
                      }
                      width={12}
                      sx={{ color: "text.tertiary" }}
                    />
                  </IconButton>
                ) : (
                  <Box sx={{ width: 16, flexShrink: 0 }} />
                )}
                <Iconify
                  icon={k.icon}
                  width={14}
                  sx={{
                    color: err ? "status.danger.fg" : "text.tertiary",
                    flexShrink: 0,
                  }}
                />
                <Typography
                  noWrap
                  sx={{
                    typography: "s2",
                    minWidth: 0,
                    fontWeight: s.depth < 2 ? 600 : 400,
                    color: err ? "status.danger.fg" : "text.primary",
                    fontFamily:
                      s.kind === "tool" || s.kind === "missing"
                        ? "ui-monospace, Menlo, monospace"
                        : undefined,
                  }}
                >
                  {s.name}
                </Typography>
                {s.evals.length > 0 && (
                  <Tooltip
                    arrow
                    title={s.evals
                      .map(
                        (e) =>
                          `${e.name} ${Math.round((e.score ?? 0) * 100)}${e.passed ? "" : " · below bar"}`,
                      )
                      .join(" · ")}
                  >
                    <Typography
                      sx={{
                        typography: "s3",
                        color: s.evals.some((e) => !e.passed)
                          ? "status.danger.fg"
                          : "text.tertiary",
                        flexShrink: 0,
                        ml: 0.5,
                      }}
                    >
                      {`${s.evals.length} eval${s.evals.length === 1 ? "" : "s"}`}
                    </Typography>
                  </Tooltip>
                )}
              </Stack>
              <Box
                sx={{
                  flex: 1,
                  position: "relative",
                  height: "100%",
                  minWidth: 120,
                }}
              >
                {s.kind === "missing" ? (
                  <Box
                    sx={{
                      position: "absolute",
                      left: pos(s.start),
                      top: 6,
                      bottom: 6,
                      borderLeft: (t) =>
                        `2px dashed ${t.palette.status.danger.solid}`,
                    }}
                  />
                ) : (
                  <Box
                    sx={{
                      position: "absolute",
                      top: s.depth === 0 ? 11 : 9,
                      height: s.depth === 0 ? 6 : 10,
                      borderRadius: "4px",
                      left: pos(s.start),
                      width: `max(2px, ${((s.end - s.start) / total) * 100}%)`,
                      bgcolor: barColor(s, theme),
                    }}
                  />
                )}
              </Box>
              <Typography
                sx={{
                  width: DUR_W,
                  flexShrink: 0,
                  textAlign: "right",
                  typography: "s3",
                  color: "text.tertiary",
                  fontVariantNumeric: "tabular-nums",
                }}
              >
                {s.kind === "missing" ? "—" : fmtMs(s.end - s.start)}
              </Typography>
            </Stack>
          );
        })}
      </Box>

      {/* ── the selected span ── */}
      {sel && <SpanDetail span={sel} parent={byId.get(sel.parentId)} />}
    </Stack>
  );
}

TraceWaterfall.propTypes = {
  showCost: PropTypes.bool,
  task: PropTypes.object,
  env: PropTypes.object,
  envState: PropTypes.object,
  focusSpanId: PropTypes.string,
};

function SpanDetail({ span, parent }) {
  const k = KIND[span.kind] || KIND.tool;
  const rows = [
    ["Kind", k.label],
    parent ? ["Parent", parent.name] : null,
    ["Start", fmtMs(span.start)],
    span.kind !== "missing" ? ["Duration", fmtMs(span.end - span.start)] : null,
  ];
  if (span.kind === "llm") {
    rows.push(["Model", span.model]);
    if (span.tokens)
      rows.push([
        "Tokens",
        `${span.tokens.toLocaleString()} (${(span.inputTokens || 0).toLocaleString()} in · ${(span.tokens - (span.inputTokens || 0)).toLocaleString()} out)`,
      ]);
    if (span.cost != null)
      rows.push(["Model cost", `${keyUsd(span.cost)} · billed to your key`]);
    if (span.input) rows.push(["Input", span.input]);
    if (span.output) rows.push(["Output", span.output]);
  }
  if (span.tool) {
    const tl = span.tool;
    rows.push(["Tool", tl.name, true]);
    rows.push(["Arguments", tl.args, true]);
    rows.push(["Response", tl.response, true]);
    if (span.kind !== "missing") rows.push(["Latency", `${tl.latencyMs}ms`]);
    rows.push(["Status", tl.status]);
    if (tl.fidelity) rows.push(["Fidelity", tl.fidelity.label]);
    if (tl.fault)
      rows.push([
        "Injected fault",
        typeof tl.fault === "string"
          ? tl.fault
          : [tl.fault.kind, tl.fault.message || tl.fault.label]
              .filter(Boolean)
              .join(" — "),
      ]);
    else if (tl.fidelity?.faults)
      rows.push([
        "Fault profile",
        "Faults enabled on this tool — none fired on this call",
      ]);
  }
  if (span.guard) {
    rows.push(["Rule", span.guard.rule]);
    rows.push([
      "Verdict",
      span.guard.verdict === "breached"
        ? "Breached"
        : span.guard.verdict === "unsure"
          ? "Flaky — broke on some repeats"
          : "Held",
    ]);
  }
  if (
    (span.kind === "caller" || span.kind === "actor" || span.kind === "turn") &&
    span.output
  )
    rows.push(["Said", span.output]);
  if (span.note) rows.push(["Finding", span.note]);

  return (
    <Box sx={{ borderTop: "1px solid", borderColor: "divider" }}>
      <Stack
        direction="row"
        alignItems="center"
        spacing={1}
        sx={{ px: 2, pt: 1.5, pb: 0.75 }}
      >
        <Iconify
          icon={k.icon}
          width={14}
          sx={{
            color:
              span.status === "error" ? "status.danger.fg" : "text.tertiary",
          }}
        />
        <Typography sx={{ typography: "s1", fontWeight: 600 }}>
          {span.name}
        </Typography>
        {span.offending && (
          <Typography
            sx={{
              typography: "s3",
              fontWeight: 600,
              color: "status.danger.fg",
            }}
          >
            Where it went wrong
          </Typography>
        )}
      </Stack>
      <Box>
        {rows.filter(Boolean).map(([label, value, mono]) => (
          <Stack
            key={label}
            direction="row"
            spacing={2}
            sx={{
              px: 2,
              py: 0.75,
              borderBottom: "1px solid",
              borderColor: "divider",
            }}
          >
            <Typography
              sx={{
                width: 110,
                flexShrink: 0,
                typography: "s3",
                color: "text.tertiary",
              }}
            >
              {label}
            </Typography>
            <Typography
              sx={{
                typography: "s2",
                color:
                  label === "Finding" ? "status.danger.fg" : "text.secondary",
                minWidth: 0,
                wordBreak: "break-word",
                fontFamily: mono ? "ui-monospace, Menlo, monospace" : undefined,
              }}
            >
              {value || "—"}
            </Typography>
          </Stack>
        ))}
      </Box>
      {span.evals.length > 0 && (
        <Box sx={{ px: 2, py: 1.25 }}>
          <Typography
            sx={{
              typography: "s3",
              fontWeight: 600,
              color: "text.tertiary",
              textTransform: "none",
              letterSpacing: 0.4,
              mb: 0.75,
            }}
          >
            Evals on this span
          </Typography>
          <Stack spacing={1}>
            {span.evals.map((e) => (
              <Box key={e.id}>
                <Stack direction="row" alignItems="center" spacing={1}>
                  <Typography
                    sx={{ typography: "s2", fontWeight: 600, flex: 1 }}
                  >
                    {e.name}
                  </Typography>
                  <ScorePill score={e.score} passed={e.passed} />
                </Stack>
                {e.reason && (
                  <Typography sx={{ typography: "s3", color: "text.tertiary" }}>
                    {e.reason}
                  </Typography>
                )}
              </Box>
            ))}
          </Stack>
        </Box>
      )}
    </Box>
  );
}
SpanDetail.propTypes = { span: PropTypes.object, parent: PropTypes.object };

/**
 * The trace on its own, in a drawer — how a voice run (whose call drawer is
 * Observe's) opens a scenario at its offending span from the Insights tab.
 */
export function TraceSheet({
  task,
  env,
  envState,
  onClose,
  onPrev,
  onNext,
  showCost,
}) {
  if (!task) return null;
  return (
    <Stack sx={{ height: "100%" }}>
      <Stack
        direction="row"
        alignItems="center"
        spacing={1.25}
        sx={{
          px: 2,
          py: 1.25,
          borderBottom: "1px solid",
          borderColor: "divider",
          flexShrink: 0,
        }}
      >
        <StatusDot status={task.status} />
        <Box flex={1} minWidth={0}>
          <Typography noWrap sx={{ typography: "s1", fontWeight: 600 }}>
            {scenarioTitle(task.title) || task.name || task.id}
          </Typography>
          <Typography noWrap sx={{ typography: "s3", color: "text.tertiary" }}>
            {[`Trace · ${task.id}`, task.persona?.name]
              .filter(Boolean)
              .join(" · ")}
          </Typography>
        </Box>
        <IconButton size="small" onClick={onPrev} disabled={!onPrev}>
          <Iconify
            icon="solar:alt-arrow-up-linear"
            width={16}
            sx={{ color: "text.tertiary" }}
          />
        </IconButton>
        <IconButton size="small" onClick={onNext} disabled={!onNext}>
          <Iconify
            icon="solar:alt-arrow-down-linear"
            width={16}
            sx={{ color: "text.tertiary" }}
          />
        </IconButton>
        <IconButton size="small" onClick={onClose}>
          <Iconify
            icon="solar:close-linear"
            width={16}
            sx={{ color: "text.tertiary" }}
          />
        </IconButton>
      </Stack>
      <Box sx={{ flex: 1, minHeight: 0, overflow: "auto" }}>
        <TraceWaterfall
          key={task.id}
          task={task}
          env={env}
          envState={envState}
          showCost={showCost}
        />
      </Box>
    </Stack>
  );
}
TraceSheet.propTypes = {
  showCost: PropTypes.bool,
  task: PropTypes.object,
  env: PropTypes.object,
  envState: PropTypes.object,
  onClose: PropTypes.func,
  onPrev: PropTypes.func,
  onNext: PropTypes.func,
};
