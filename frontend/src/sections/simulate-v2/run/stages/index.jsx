/**
 * Live stages — the "watch the agent work" panels.
 *
 * One per surface, because watching a voice call and watching a browser agent
 * have nothing in common visually. Each stage takes the focused task plus its
 * current step index and renders what is happening *right now*, so a viewer
 * can tell at a glance whether the agent is doing something sensible.
 */
import PropTypes from "prop-types";
import { useEffect, useRef } from "react";
import { alpha } from "@mui/material/styles";
import { Box, Stack, Typography, keyframes } from "@mui/material";
import Iconify from "src/components/iconify";
import { PersonaBadge } from "../../components/primitives";

const bar = keyframes`
  0%, 100% { transform: scaleY(0.28); }
  50%      { transform: scaleY(1); }
`;

const blink = keyframes`
  0%, 49%  { opacity: 1; }
  50%,100% { opacity: 0; }
`;

const clickRing = keyframes`
  0%   { transform: scale(0.4); opacity: .8; }
  100% { transform: scale(2.4); opacity: 0; }
`;

/* ── shell ───────────────────────────────────────────────────────────────── */

function StageShell({ children, sx }) {
  return (
    <Box
      sx={{
        flex: 1, minHeight: 0, display: "flex", flexDirection: "column",
        bgcolor: "background.neutral", ...sx,
      }}
    >
      {children}
    </Box>
  );
}
StageShell.propTypes = { children: PropTypes.node, sx: PropTypes.object };

function useAutoScroll(dep) {
  const ref = useRef(null);
  useEffect(() => {
    ref.current?.scrollTo({ top: 999999, behavior: "smooth" });
  }, [dep]);
  return ref;
}

/* ── voice ───────────────────────────────────────────────────────────────── */

export function VoiceStage({ task, stepIndex, live }) {
  const scrollRef = useAutoScroll(stepIndex);
  const visible = task.steps.slice(0, stepIndex + 1);
  const current = task.steps[stepIndex];
  const agentSpeaking = current?.role === "agent";

  return (
    <StageShell>
      {/* call header */}
      <Stack
        direction="row"
        alignItems="center"
        spacing={2}
        sx={{ px: 2.5, py: 2, borderBottom: "1px solid", borderColor: "divider", bgcolor: "background.paper" }}
      >
        <Party
          label={task.persona?.name || "Caller"}
          sub={task.persona?.voice}
          icon="solar:user-linear"
          color="#0891B2"
          speaking={live && current?.role === "customer"}
        />
        <Box sx={{ flex: 1, display: "grid", placeItems: "center" }}>
          <Waveform active={live} color={agentSpeaking ? "#7857FC" : "#0891B2"} />
        </Box>
        <Party
          label="Your agent"
          sub="under test"
          icon="solar:cpu-bolt-linear"
          color="#7857FC"
          speaking={live && agentSpeaking}
          right
        />
      </Stack>

      {/* transcript */}
      <Box ref={scrollRef} sx={{ flex: 1, overflow: "auto", p: 2.5 }}>
        <Stack spacing={1.5}>
          {visible.map((s, i) => (
            <Turn key={s.id} turn={s} latest={live && i === visible.length - 1} />
          ))}
          {live && <TypingIndicator role={agentSpeaking ? "agent" : "customer"} />}
        </Stack>
      </Box>
    </StageShell>
  );
}
VoiceStage.propTypes = { task: PropTypes.object, stepIndex: PropTypes.number, live: PropTypes.bool };

function Party({ label, sub, icon, color, speaking, right }) {
  return (
    <Stack direction={right ? "row-reverse" : "row"} alignItems="center" spacing={1.25} sx={{ width: 160 }}>
      <Box sx={{ position: "relative", display: "grid", placeItems: "center", flexShrink: 0 }}>
        {speaking && (
          <Box
            sx={{
              position: "absolute", width: 44, height: 44, borderRadius: "50%",
              border: "2px solid", borderColor: alpha(color, 0.45),
              animation: `${clickRing} 1.4s ease-out infinite`,
            }}
          />
        )}
        <Box
          sx={{
            width: 36, height: 36, borderRadius: "50%", display: "grid", placeItems: "center",
            bgcolor: (t) => alpha(color, t.palette.mode === "dark" ? 0.18 : 0.1),
            color, border: `1px solid ${alpha(color, 0.3)}`,
          }}
        >
          <Iconify icon={icon} width={18} />
        </Box>
      </Box>
      <Box minWidth={0} sx={{ textAlign: right ? "right" : "left" }}>
        <Typography noWrap sx={{ typography: "s2", fontWeight: 700 }}>{label}</Typography>
        <Typography noWrap sx={{ typography: "s3", color: "text.subtitle" }}>{sub}</Typography>
      </Box>
    </Stack>
  );
}
Party.propTypes = {
  label: PropTypes.string, sub: PropTypes.string, icon: PropTypes.string,
  color: PropTypes.string, speaking: PropTypes.bool, right: PropTypes.bool,
};

function Waveform({ active, color = "#7857FC" }) {
  const bars = [0.5, 0.8, 0.35, 1, 0.6, 0.9, 0.45, 0.75, 0.55, 0.95, 0.4, 0.7];
  return (
    <Stack direction="row" alignItems="center" spacing={0.375} sx={{ height: 28 }}>
      {bars.map((h, i) => (
        <Box
          key={i}
          sx={{
            width: 3, height: 24, borderRadius: 2, bgcolor: color,
            opacity: active ? 0.9 : 0.25,
            transformOrigin: "center",
            transform: active ? undefined : "scaleY(0.2)",
            animation: active ? `${bar} ${0.7 + (i % 4) * 0.16}s ease-in-out infinite` : "none",
            animationDelay: `${i * 0.06}s`,
          }}
        />
      ))}
    </Stack>
  );
}
Waveform.propTypes = { active: PropTypes.bool, color: PropTypes.string };

function Turn({ turn, latest }) {
  const isAgent = turn.role === "agent";
  return (
    <Stack
      direction="row"
      spacing={1.25}
      sx={{ justifyContent: isAgent ? "flex-end" : "flex-start" }}
    >
      <Box
        sx={{
          maxWidth: "76%", px: 1.75, py: 1.125, borderRadius: 1.5,
          border: "1px solid",
          borderColor: isAgent ? alpha("#7857FC", 0.3) : "divider",
          bgcolor: (t) => isAgent
            ? alpha("#7857FC", t.palette.mode === "dark" ? 0.14 : 0.06)
            : "background.paper",
          ...(latest && { boxShadow: () => `0 0 0 3px ${alpha(isAgent ? "#7857FC" : "#0891B2", 0.1)}` }),
        }}
      >
        <Typography sx={{ typography: "s3", fontWeight: 700, color: isAgent ? "#7857FC" : "text.subtitle", mb: 0.25 }}>
          {isAgent ? "Agent" : "Customer"}
        </Typography>
        <Typography sx={{ typography: "s2" }}>{turn.text}</Typography>
      </Box>
    </Stack>
  );
}
Turn.propTypes = { turn: PropTypes.object, latest: PropTypes.bool };

function TypingIndicator({ role }) {
  const isAgent = role === "agent";
  return (
    <Stack direction="row" sx={{ justifyContent: isAgent ? "flex-end" : "flex-start" }}>
      <Stack
        direction="row" spacing={0.5} alignItems="center"
        sx={{
          px: 1.5, py: 1, borderRadius: 1.5,
          border: "1px solid", borderColor: "divider", bgcolor: "background.paper",
        }}
      >
        {[0, 1, 2].map((i) => (
          <Box
            key={i}
            sx={{
              width: 5, height: 5, borderRadius: "50%",
              bgcolor: isAgent ? "#7857FC" : "text.subtitle",
              animation: `${bar} 1s ease-in-out infinite`,
              animationDelay: `${i * 0.16}s`,
            }}
          />
        ))}
      </Stack>
    </Stack>
  );
}
TypingIndicator.propTypes = { role: PropTypes.string };

/* ── chat ────────────────────────────────────────────────────────────────── */

export function ChatStage({ task, stepIndex, live }) {
  const scrollRef = useAutoScroll(stepIndex);
  const visible = task.steps.slice(0, stepIndex + 1);
  const current = task.steps[stepIndex];

  return (
    <StageShell>
      <Stack
        direction="row" alignItems="center" spacing={1.5}
        sx={{ px: 2.5, py: 1.75, borderBottom: "1px solid", borderColor: "divider", bgcolor: "background.paper" }}
      >
        <PersonaBadge persona={task.persona} />
        <Box flex={1} />
        <Stack direction="row" alignItems="center" spacing={0.625}>
          <Box sx={{ width: 6, height: 6, borderRadius: "50%", bgcolor: live ? "#16A34A" : "text.subtitle" }} />
          <Typography sx={{ typography: "s3", color: "text.subtitle" }}>
            {live ? "session active" : "session closed"}
          </Typography>
        </Stack>
      </Stack>

      <Box ref={scrollRef} sx={{ flex: 1, overflow: "auto", p: 2.5 }}>
        <Stack spacing={1.5}>
          {visible.map((s, i) => (
            <Turn key={s.id} turn={s} latest={live && i === visible.length - 1} />
          ))}
          {live && <TypingIndicator role={current?.role === "agent" ? "agent" : "customer"} />}
        </Stack>
      </Box>
    </StageShell>
  );
}
ChatStage.propTypes = { task: PropTypes.object, stepIndex: PropTypes.number, live: PropTypes.bool };

/* ── browser ─────────────────────────────────────────────────────────────── */

export function BrowserStage({ task, stepIndex, live }) {
  const current = task.steps[stepIndex];
  const scrollRef = useAutoScroll(stepIndex);
  const visible = task.steps.slice(0, stepIndex + 1);

  // Deterministic cursor placement per step so the pointer moves purposefully
  // rather than jittering — it should read as intent, not noise.
  const pos = cursorFor(current, stepIndex);

  return (
    <StageShell>
      <Box sx={{ flex: 1, minHeight: 0, display: "flex", flexDirection: "column", p: 2 }}>
        {/* browser chrome */}
        <Box
          sx={{
            flex: 1, minHeight: 0, display: "flex", flexDirection: "column",
            borderRadius: 1.5, overflow: "hidden",
            border: "1px solid", borderColor: "divider",
            bgcolor: "background.paper",
            boxShadow: (t) => `0 8px 30px ${alpha("#000", t.palette.mode === "dark" ? 0.4 : 0.08)}`,
          }}
        >
          <Stack
            direction="row" alignItems="center" spacing={1.25}
            sx={{ px: 1.5, py: 1, borderBottom: "1px solid", borderColor: "divider", bgcolor: "background.neutral" }}
          >
            <Stack direction="row" spacing={0.625}>
              {["#FF5F57", "#FEBC2E", "#28C840"].map((c) => (
                <Box key={c} sx={{ width: 9, height: 9, borderRadius: "50%", bgcolor: c, opacity: 0.85 }} />
              ))}
            </Stack>
            <Box
              sx={{
                flex: 1, px: 1.25, py: 0.5, borderRadius: 0.75,
                border: "1px solid", borderColor: "divider", bgcolor: "background.paper",
              }}
            >
              <Typography noWrap sx={{ typography: "s3", color: "text.subtitle", fontFamily: "ui-monospace, Menlo, monospace" }}>
                {current?.action === "navigate" ? current.target : "app.acme-admin.com/billing"}
              </Typography>
            </Box>
            {live && (
              <Stack direction="row" alignItems="center" spacing={0.5}>
                <Box sx={{ width: 6, height: 6, borderRadius: "50%", bgcolor: "#DC2626", animation: `${blink} 1.4s infinite` }} />
                <Typography sx={{ typography: "s3", color: "#DC2626", fontWeight: 700 }}>REC</Typography>
              </Stack>
            )}
          </Stack>

          {/* wireframe viewport with the agent's cursor */}
          <Box sx={{ flex: 1, minHeight: 0, position: "relative", overflow: "hidden" }}>
            <Wireframe highlight={pos.region} />

            {live && (
              <Box
                sx={{
                  position: "absolute",
                  left: `${pos.x}%`, top: `${pos.y}%`,
                  transition: "left .55s cubic-bezier(.4,0,.2,1), top .55s cubic-bezier(.4,0,.2,1)",
                  pointerEvents: "none", zIndex: 3,
                }}
              >
                {current?.action === "click" && (
                  <Box
                    sx={{
                      position: "absolute", left: -12, top: -12, width: 26, height: 26,
                      borderRadius: "50%", border: "2px solid #7857FC",
                      animation: `${clickRing} .9s ease-out infinite`,
                    }}
                  />
                )}
                <Iconify
                  icon="solar:cursor-bold"
                  width={20}
                  sx={{ color: "#7857FC", filter: "drop-shadow(0 2px 4px rgba(0,0,0,.35))" }}
                />
              </Box>
            )}
          </Box>
        </Box>

        {/* action log — what the agent believes it is doing */}
        <Box
          sx={{
            mt: 1.5, maxHeight: 132, overflow: "auto",
            borderRadius: 1.25, border: "1px solid", borderColor: "divider", bgcolor: "background.paper",
          }}
          ref={scrollRef}
        >
          <Stack divider={<Box sx={{ borderBottom: "1px solid", borderColor: "divider" }} />}>
            {visible.slice(-6).map((s) => (
              <Stack key={s.id} direction="row" alignItems="center" spacing={1.25} sx={{ px: 1.5, py: 0.875 }}>
                <Box
                  sx={{
                    px: 0.75, height: 18, borderRadius: 0.5, display: "grid", placeItems: "center", flexShrink: 0,
                    bgcolor: (t) => alpha("#EA580C", t.palette.mode === "dark" ? 0.18 : 0.1),
                    color: "#EA580C",
                  }}
                >
                  <Typography sx={{ typography: "s3", fontWeight: 700, fontFamily: "ui-monospace, Menlo, monospace" }}>
                    {s.action}
                  </Typography>
                </Box>
                <Typography noWrap sx={{ typography: "s3", fontFamily: "ui-monospace, Menlo, monospace", color: "text.secondary", flex: 1 }}>
                  {s.target}{s.value ? ` = "${s.value}"` : ""}
                </Typography>
                <Typography noWrap sx={{ typography: "s3", color: "text.subtitle", maxWidth: 220, display: { xs: "none", lg: "block" } }}>
                  {s.thought}
                </Typography>
              </Stack>
            ))}
          </Stack>
        </Box>
      </Box>
    </StageShell>
  );
}
BrowserStage.propTypes = { task: PropTypes.object, stepIndex: PropTypes.number, live: PropTypes.bool };

function cursorFor(step, i) {
  if (!step) return { x: 50, y: 50, region: null };
  const map = {
    navigate: { x: 30, y: 8, region: "url" },
    type: { x: 42, y: 38, region: "form" },
    click: { x: 62, y: 52, region: "table" },
    scroll: { x: 78, y: 62, region: "table" },
    wait: { x: 50, y: 30, region: null },
  };
  const base = map[step.action] || { x: 50, y: 50, region: null };
  // Nudge by index so repeated actions don't land on the identical pixel.
  return { ...base, x: base.x + ((i * 7) % 11) - 5, y: base.y + ((i * 5) % 9) - 4 };
}

function Wireframe({ highlight }) {
  const hl = (region) => ({
    outline: highlight === region ? "2px solid #7857FC" : "none",
    outlineOffset: 2,
    transition: "outline-color .3s ease",
  });
  return (
    <Box sx={{ position: "absolute", inset: 0, p: 1.5, display: "flex", gap: 1.5 }}>
      {/* sidebar */}
      <Stack spacing={0.75} sx={{ width: "18%", flexShrink: 0 }}>
        <Box sx={{ height: 18, borderRadius: 0.5, bgcolor: (t) => alpha(t.palette.primary.main, 0.18) }} />
        {Array.from({ length: 6 }).map((_, i) => (
          <Box key={i} sx={{ height: 10, borderRadius: 0.5, bgcolor: "background.neutral", opacity: 1 - i * 0.1 }} />
        ))}
      </Stack>
      {/* main */}
      <Stack spacing={1} sx={{ flex: 1, minWidth: 0 }}>
        <Stack direction="row" spacing={1} sx={{ ...hl("form") }}>
          <Box sx={{ flex: 1, height: 22, borderRadius: 0.5, bgcolor: "background.neutral" }} />
          <Box sx={{ width: 64, height: 22, borderRadius: 0.5, bgcolor: (t) => alpha(t.palette.primary.main, 0.25) }} />
        </Stack>
        <Stack direction="row" spacing={1}>
          {[0, 1, 2].map((i) => (
            <Box key={i} sx={{ flex: 1, height: 42, borderRadius: 0.75, bgcolor: "background.neutral" }} />
          ))}
        </Stack>
        <Box sx={{ flex: 1, borderRadius: 0.75, bgcolor: "background.neutral", p: 1, ...hl("table") }}>
          <Stack spacing={0.625}>
            {Array.from({ length: 7 }).map((_, i) => (
              <Stack key={i} direction="row" spacing={1}>
                <Box sx={{ flex: 2, height: 9, borderRadius: 0.375, bgcolor: "divider" }} />
                <Box sx={{ flex: 1, height: 9, borderRadius: 0.375, bgcolor: "divider", opacity: 0.7 }} />
                <Box sx={{ flex: 1, height: 9, borderRadius: 0.375, bgcolor: "divider", opacity: 0.5 }} />
              </Stack>
            ))}
          </Stack>
        </Box>
      </Stack>
    </Box>
  );
}
Wireframe.propTypes = { highlight: PropTypes.string };

/* ── tools ───────────────────────────────────────────────────────────────── */

export function ToolStage({ task, stepIndex, live }) {
  const scrollRef = useAutoScroll(stepIndex);
  const visible = task.steps.slice(0, stepIndex + 1);

  return (
    <StageShell>
      <Box ref={scrollRef} sx={{ flex: 1, overflow: "auto", p: 2.5 }}>
        <Stack spacing={1.25}>
          {visible.map((s, i) => {
            const pending = live && i === visible.length - 1;
            return (
              <Box
                key={s.id}
                sx={{
                  borderRadius: 1.25, overflow: "hidden",
                  border: "1px solid",
                  borderColor: pending ? alpha("#DB2777", 0.4) : "divider",
                  bgcolor: "background.paper",
                }}
              >
                <Stack
                  direction="row" alignItems="center" spacing={1.25}
                  sx={{ px: 1.75, py: 1.125, borderBottom: "1px solid", borderColor: "divider" }}
                >
                  <Iconify icon="solar:settings-minimalistic-linear" width={15} sx={{ color: "#DB2777", flexShrink: 0 }} />
                  <Typography sx={{ typography: "s2", fontWeight: 700, fontFamily: "ui-monospace, Menlo, monospace" }}>
                    {s.tool}
                  </Typography>
                  <Box flex={1} />
                  <Typography sx={{ typography: "s3", color: "text.subtitle", fontVariantNumeric: "tabular-nums" }}>
                    {pending ? "running…" : `${s.ms}ms`}
                  </Typography>
                </Stack>
                <Box sx={{ px: 1.75, py: 1.25 }}>
                  <Typography sx={{ typography: "s3", color: "text.subtitle", mb: 0.375 }}>arguments</Typography>
                  <Typography
                    sx={{
                      typography: "s3", fontFamily: "ui-monospace, Menlo, monospace",
                      color: "text.secondary", mb: 1, wordBreak: "break-all",
                    }}
                  >
                    {JSON.stringify(s.args)}
                  </Typography>
                  <Typography sx={{ typography: "s3", color: "text.subtitle", mb: 0.375 }}>result</Typography>
                  <Typography
                    sx={{
                      typography: "s3", fontFamily: "ui-monospace, Menlo, monospace",
                      color: pending ? "text.subtitle" : "#16A34A",
                    }}
                  >
                    {pending ? "…" : s.result}
                  </Typography>
                </Box>
              </Box>
            );
          })}
        </Stack>
      </Box>
    </StageShell>
  );
}
ToolStage.propTypes = { task: PropTypes.object, stepIndex: PropTypes.number, live: PropTypes.bool };

/* ── terminal ────────────────────────────────────────────────────────────── */

export function TerminalStage({ task, stepIndex, live }) {
  const scrollRef = useAutoScroll(stepIndex);
  const visible = task.steps.slice(0, stepIndex + 1);

  return (
    <StageShell sx={{ p: 2 }}>
      <Box
        ref={scrollRef}
        sx={{
          flex: 1, minHeight: 0, overflow: "auto", p: 2, borderRadius: 1.5,
          bgcolor: (t) => t.palette.mode === "dark" ? "#0a0a0a" : "#18181b",
          border: "1px solid", borderColor: "divider",
          fontFamily: "ui-monospace, SFMono-Regular, Menlo, monospace",
        }}
      >
        {visible.map((s, i) => {
          const pending = live && i === visible.length - 1;
          return (
            <Box key={s.id} sx={{ mb: 1.5 }}>
              <Stack direction="row" spacing={1}>
                <Typography sx={{ typography: "s2", color: "#16A34A", fontFamily: "inherit", flexShrink: 0 }}>
                  $
                </Typography>
                <Typography sx={{ typography: "s2", color: "#fafafa", fontFamily: "inherit", wordBreak: "break-all" }}>
                  {s.cmd}
                  {pending && (
                    <Box component="span" sx={{ ml: 0.5, color: "#fafafa", animation: `${blink} 1s step-end infinite` }}>▊</Box>
                  )}
                </Typography>
              </Stack>
              {!pending && (
                <Typography sx={{ typography: "s2", color: "#a1a1aa", fontFamily: "inherit", pl: 2, whiteSpace: "pre-wrap" }}>
                  {s.out}
                </Typography>
              )}
            </Box>
          );
        })}
      </Box>
    </StageShell>
  );
}
TerminalStage.propTypes = { task: PropTypes.object, stepIndex: PropTypes.number, live: PropTypes.bool };

/* ── email ───────────────────────────────────────────────────────────────── */

export function EmailStage({ task, stepIndex, live }) {
  const scrollRef = useAutoScroll(stepIndex);
  const visible = task.steps.slice(0, stepIndex + 1);
  const kindMeta = {
    read: { icon: "solar:letter-opened-linear", color: "#0891B2", label: "Read" },
    parse: { icon: "solar:file-text-linear", color: "#CA8A04", label: "Parse" },
    check: { icon: "solar:shield-check-linear", color: "#7857FC", label: "Check" },
    compose: { icon: "solar:pen-new-square-linear", color: "#EA580C", label: "Compose" },
    send: { icon: "solar:plain-linear", color: "#16A34A", label: "Send" },
  };

  return (
    <StageShell>
      <Box ref={scrollRef} sx={{ flex: 1, overflow: "auto", p: 2.5 }}>
        <Stack spacing={0}>
          {visible.map((s, i) => {
            const meta = kindMeta[s.kind] || kindMeta.read;
            const pending = live && i === visible.length - 1;
            const last = i === visible.length - 1;
            return (
              <Stack key={s.id} direction="row" spacing={1.75}>
                {/* rail */}
                <Stack alignItems="center" sx={{ flexShrink: 0 }}>
                  <Box
                    sx={{
                      width: 30, height: 30, borderRadius: "50%", display: "grid", placeItems: "center",
                      bgcolor: (t) => alpha(meta.color, t.palette.mode === "dark" ? 0.18 : 0.1),
                      color: meta.color,
                      border: `1px solid ${alpha(meta.color, 0.3)}`,
                    }}
                  >
                    <Iconify icon={meta.icon} width={15} />
                  </Box>
                  {!last && <Box sx={{ flex: 1, width: "2px", bgcolor: "divider", my: 0.5, minHeight: 18 }} />}
                </Stack>
                <Box sx={{ pb: 2.25, flex: 1, minWidth: 0 }}>
                  <Stack direction="row" alignItems="center" spacing={0.75}>
                    <Typography sx={{ typography: "s3", fontWeight: 700, color: meta.color }}>
                      {meta.label}
                    </Typography>
                    {pending && (
                      <Typography sx={{ typography: "s3", color: "text.subtitle" }}>in progress…</Typography>
                    )}
                  </Stack>
                  <Typography sx={{ typography: "s2", fontWeight: 600 }}>{s.subject}</Typography>
                  <Typography sx={{ typography: "s3", color: "text.subtitle" }}>{s.note}</Typography>
                </Box>
              </Stack>
            );
          })}
        </Stack>
      </Box>
    </StageShell>
  );
}
EmailStage.propTypes = { task: PropTypes.object, stepIndex: PropTypes.number, live: PropTypes.bool };

/* ── simulation ──────────────────────────────────────────────────────────── */

/**
 * Robotics and game environments.
 *
 * The two things that matter while a policy runs are where it is in the scene
 * and whether reward is climbing, so the viewport and the reward trace get the
 * space. The action log underneath is the trajectory in text.
 */
export function SimStage({ task, stepIndex, live }) {
  const scrollRef = useAutoScroll(stepIndex);
  const visible = task.steps.slice(0, stepIndex + 1);
  const current = task.steps[stepIndex];
  const reward = current?.reward ?? 0;

  // Deterministic path across the scene so the marker reads as intent.
  const pos = {
    x: 18 + Math.min(stepIndex, 7) * 9,
    y: 62 - Math.sin(Math.min(stepIndex, 7) / 2) * 26,
  };

  return (
    <StageShell>
      <Box sx={{ flex: 1, minHeight: 0, display: "flex", flexDirection: "column", p: 2 }}>
        {/* viewport */}
        <Box
          sx={{
            flex: 1, minHeight: 0, position: "relative", overflow: "hidden",
            borderRadius: 1.5, border: "1px solid", borderColor: "divider",
            bgcolor: (t) => (t.palette.mode === "dark" ? "#0d0d12" : "#f4f4f7"),
          }}
        >
          {/* ground grid */}
          <Box
            sx={{
              position: "absolute", inset: 0,
              backgroundImage: (t) => {
                const line = alpha(t.palette.text.primary, 0.07);
                return `linear-gradient(${line} 1px, transparent 1px), linear-gradient(90deg, ${line} 1px, transparent 1px)`;
              },
              backgroundSize: "32px 32px",
            }}
          />

          {/* goal */}
          <Box
            sx={{
              position: "absolute", left: "78%", top: "46%",
              width: 26, height: 26, borderRadius: "50%",
              border: "2px dashed", borderColor: alpha("#16A34A", 0.7),
            }}
          />

          {/* the agent in the scene */}
          <Box
            sx={{
              position: "absolute",
              left: `${pos.x}%`, top: `${pos.y}%`,
              transition: "left .6s cubic-bezier(.4,0,.2,1), top .6s cubic-bezier(.4,0,.2,1)",
            }}
          >
            {live && (
              <Box
                sx={{
                  position: "absolute", left: -10, top: -10, width: 34, height: 34,
                  borderRadius: "50%", border: "2px solid", borderColor: alpha("#8B5CF6", 0.5),
                  animation: `${clickRing} 1.5s ease-out infinite`,
                }}
              />
            )}
            <Box
              sx={{
                width: 14, height: 14, borderRadius: 0.75, bgcolor: "#8B5CF6",
                boxShadow: `0 0 14px ${alpha("#8B5CF6", 0.8)}`,
              }}
            />
          </Box>

          {/* episode + reward readout */}
          <Stack
            direction="row" alignItems="center" spacing={1.5}
            sx={{
              position: "absolute", left: 12, right: 12, bottom: 12,
              px: 1.5, py: 1, borderRadius: 1,
              bgcolor: (t) => alpha(t.palette.background.paper, 0.85),
              border: "1px solid", borderColor: "divider",
              backdropFilter: "blur(6px)",
            }}
          >
            <Typography sx={{ typography: "s3", color: "text.subtitle", flexShrink: 0 }}>
              step {Math.max(0, stepIndex) + 1}/{task.steps.length}
            </Typography>
            <Box sx={{ flex: 1, height: 5, borderRadius: 3, bgcolor: "background.neutral", overflow: "hidden" }}>
              <Box
                sx={{
                  height: "100%", width: `${reward * 100}%`, bgcolor: "#8B5CF6",
                  transition: "width .5s ease",
                }}
              />
            </Box>
            <Typography
              sx={{ typography: "s2", fontWeight: 700, color: "#8B5CF6", flexShrink: 0, fontVariantNumeric: "tabular-nums" }}
            >
              {reward.toFixed(2)}
            </Typography>
          </Stack>
        </Box>

        {/* trajectory */}
        <Box
          ref={scrollRef}
          sx={{
            mt: 1.5, maxHeight: 132, overflow: "auto",
            borderRadius: 1.25, border: "1px solid", borderColor: "divider", bgcolor: "background.paper",
          }}
        >
          <Stack divider={<Box sx={{ borderBottom: "1px solid", borderColor: "divider" }} />}>
            {visible.slice(-6).map((s) => (
              <Stack key={s.id} direction="row" alignItems="center" spacing={1.25} sx={{ px: 1.5, py: 0.875 }}>
                <Box
                  sx={{
                    px: 0.75, height: 18, borderRadius: 0.5, display: "grid", placeItems: "center", flexShrink: 0,
                    bgcolor: (t) => alpha("#8B5CF6", t.palette.mode === "dark" ? 0.18 : 0.1),
                    color: "#8B5CF6",
                  }}
                >
                  <Typography sx={{ typography: "s3", fontWeight: 700, fontFamily: "ui-monospace, Menlo, monospace" }}>
                    {s.action}
                  </Typography>
                </Box>
                <Typography noWrap sx={{ flex: 1, typography: "s3", fontFamily: "ui-monospace, Menlo, monospace", color: "text.secondary" }}>
                  {s.obs}
                </Typography>
                <Typography noWrap sx={{ typography: "s3", color: "text.subtitle", maxWidth: 220, display: { xs: "none", lg: "block" } }}>
                  {s.note}
                </Typography>
              </Stack>
            ))}
          </Stack>
        </Box>
      </Box>
    </StageShell>
  );
}
SimStage.propTypes = { task: PropTypes.object, stepIndex: PropTypes.number, live: PropTypes.bool };

/* ── router ──────────────────────────────────────────────────────────────── */

const STAGES = {
  voice: VoiceStage,
  chat: ChatStage,
  browser: BrowserStage,
  tools: ToolStage,
  terminal: TerminalStage,
  email: EmailStage,
  sim: SimStage,
  multi: ChatStage,
};

export default function Stage({ stage, task, stepIndex, live }) {
  const Cmp = STAGES[stage] || VoiceStage;
  if (!task) return null;
  return <Cmp task={task} stepIndex={stepIndex} live={live} />;
}
Stage.propTypes = {
  stage: PropTypes.string, task: PropTypes.object,
  stepIndex: PropTypes.number, live: PropTypes.bool,
};
