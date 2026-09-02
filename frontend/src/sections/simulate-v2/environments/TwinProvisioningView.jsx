import PropTypes from "prop-types";
import { useEffect, useMemo, useRef, useState } from "react";
import { alpha, keyframes } from "@mui/material/styles";
import {
  Box, Stack, Typography, IconButton, Tooltip, Button,
} from "@mui/material";
import Iconify from "src/components/iconify";
import { SectionCard } from "../components/primitives";
import AssistantConsole from "../assistant/AssistantConsole";
import TwinLogo from "../components/TwinLogo";
import { twinById } from "../_mock/twins";

const TWIN_TINT = "#7857FC";
const SUCCESS = "#16A34A";

/*
  Phases the twin runtime steps through when standing up a fresh
  environment. Each phase's dwell keeps the prototype under ~6s while
  the real system would stream progress as each hits.
*/
const PHASES = [
  { key: "provision", title: "Provisioning clone services", subtitle: "Spinning up the sandbox instance for each service", dwell: 1500 },
  { key: "seed", title: "Seeding starting state", subtitle: "Resolving the seed prompt into concrete service state", dwell: 1200 },
  { key: "rotate", title: "Rotating per-run credentials", subtitle: "Minting fresh service tokens for this environment", dwell: 900 },
  { key: "probe", title: "Streaming first probe", subtitle: "Confirming each service responds", dwell: 1100 },
];

const glow = keyframes`
  0%,100% { box-shadow: 0 0 0 2px rgba(120,87,252,0.20); }
  50%     { box-shadow: 0 0 0 8px rgba(120,87,252,0.10); }
`;

const barShimmer = keyframes`
  0%   { background-position: 0% 0; }
  100% { background-position: 200% 0; }
`;

const logIn = keyframes`
  from { opacity: 0; transform: translateY(6px); }
  to   { opacity: 1; transform: translateY(0); }
`;

const particleFly = keyframes`
  0%   { transform: translate(0,0) scale(1); opacity: 0; }
  10%  { opacity: 1; }
  100% { transform: translate(var(--dx), var(--dy)) scale(0.4); opacity: 0; }
`;

const beamPulse = keyframes`
  0%,100% { opacity: 0.35; }
  50%     { opacity: 1; }
`;

const chipDrop = keyframes`
  from { opacity: 0; transform: translateY(-4px) scale(0.9); }
  to   { opacity: 1; transform: translateY(0) scale(1); }
`;

const counterFlick = keyframes`
  0%   { transform: translateY(-100%); opacity: 0; }
  40%  { transform: translateY(0); opacity: 1; }
  100% { transform: translateY(0); opacity: 1; }
`;

/*
  Ticker that counts up to `target` over `duration` ms — deterministic,
  starts fresh whenever phaseIdx changes so each stage feels alive.
*/
function useTicker(target, active, duration = 800) {
  const [n, setN] = useState(0);
  useEffect(() => {
    if (!active) { setN(target); return undefined; }
    setN(0);
    const start = performance.now();
    let raf;
    const step = (t) => {
      const p = Math.min(1, (t - start) / duration);
      setN(Math.round(target * (0.5 - Math.cos(Math.PI * p) / 2)));
      if (p < 1) raf = requestAnimationFrame(step);
    };
    raf = requestAnimationFrame(step);
    return () => cancelAnimationFrame(raf);
  }, [target, active, duration]);
  return n;
}

/*
  Log-line pool per phase per service. Each service gets a stream of
  short technical lines animated in as its phase runs, so the card
  reads as "something is happening in there" rather than a static
  progress bar.
*/
function logLinesFor(phase, twinName) {
  const t = twinName || "service";
  return ({
    provision: [
      `boot ${t.toLowerCase()}-sandbox-${randHex(6)}`,
      `mount /var/twin/${t.toLowerCase()}`,
      `attach network sandbox-net`,
      `health: 200 OK`,
    ],
    seed: [
      `parse seed prompt · 42 tokens`,
      `plan · 3 collections · 12 entities`,
      `insert users(4) channels(3) messages(11)`,
      `commit · fsync · verified`,
    ],
    rotate: [
      `mint access_token · ttl=60m`,
      `mint refresh_token · rotate on use`,
      `bind session ${randHex(8)}`,
      `sealed with kms-v3`,
    ],
    probe: [
      `→ GET /health`,
      `← 200 · 42ms`,
      `→ GET /whoami`,
      `← 200 · 18ms · verified`,
    ],
  })[phase] || [];
}

function randHex(n) {
  const s = "0123456789abcdef";
  let r = "";
  for (let i = 0; i < n; i += 1) r += s[Math.floor((i * 7 + n) % 16)];
  return r;
}

/**
 * Inline "spinning up" view for a freshly composed twin environment.
 *
 * Same split shape as the review layout: chat left, panels right. The
 * right side runs a live provisioning animation — one card per twin
 * service, each ticking through provision → seed → rotate → probe —
 * plus a proper step-by-step timeline underneath. The left side is
 * AssistantConsole narrating what's landing as it lands, so the wait
 * has both a picture and a running commentary.
 *
 * Fires `onDone` once every phase has landed; the parent flips
 * envState.twinBacking.status to "ready" which swaps this view out
 * for the real review layout.
 */
export default function TwinProvisioningView({ env, envState, onDone }) {
  const services = envState?.twinBacking?.services || [];
  const [phaseIdx, setPhaseIdx] = useState(0);
  const [turns, setTurns] = useState([]);
  const onDoneRef = useRef(onDone);
  onDoneRef.current = onDone;

  /*
    Deterministic per-service completion tick — services light up
    slightly out of phase within each stage so the right panel feels
    concurrent rather than lockstep. Read as a set: "at time T, which
    services have finished which phase."
  */
  const [serviceProgress, setServiceProgress] = useState(() =>
    Object.fromEntries(services.map((s, i) => [s, { offset: i * 180 }])),
  );

  /*
    Advance through phases one at a time. onDone fires 400ms after
    the last phase settles so the ✓ has a moment to be seen.
  */
  useEffect(() => {
    if (phaseIdx >= PHASES.length) {
      const t = setTimeout(() => onDoneRef.current?.(), 400);
      return () => clearTimeout(t);
    }
    const t = setTimeout(() => setPhaseIdx((i) => i + 1), PHASES[phaseIdx].dwell);
    return () => clearTimeout(t);
  }, [phaseIdx]);

  /*
    Push a chat turn as each phase begins. The turn shape mirrors the
    "builder" turns BuildFromAgent uses so AssistantConsole renders it
    with the same rhythm.
  */
  useEffect(() => {
    if (phaseIdx > PHASES.length) return;
    const phase = PHASES[phaseIdx];
    if (!phase) {
      setTurns((prev) => [...prev, {
        id: "done", role: "builder", title: "Sandbox ready",
        steps: [{ kind: "note", text: `All ${services.length} services are up and responding to probes. Handoff to the review layout in a moment.` }],
      }]);
      return;
    }
    setTurns((prev) => [...prev, {
      id: `phase-${phase.key}`, role: "builder", title: phase.title,
      steps: [
        { kind: "note", text: phase.subtitle },
        ...services.map((sId) => {
          const twin = twinById(sId);
          const noteByPhase = {
            provision: `Instance spun up for ${twin?.name || sId}`,
            seed: `Seeded ${twin?.name || sId} from the starting-state prompt`,
            rotate: `Fresh per-run token minted for ${twin?.name || sId}`,
            probe: `${twin?.name || sId} responded to first probe in ${40 + ((sId?.length || 4) * 7) % 90}ms`,
          };
          return { kind: "note", text: noteByPhase[phase.key] || `${twin?.name || sId} — ok` };
        }),
      ],
    }]);
  }, [phaseIdx, services]);

  const chips = useMemo(() => ["What is being provisioned?", "Skip animation", "Cancel and restart"], []);

  return (
    <Box sx={{ display: "flex", flexDirection: "column", height: "100%", minHeight: 0 }}>
      {/* header */}
      <Stack direction="row" alignItems="center" spacing={2} sx={{
        px: 3, py: 1.75, borderBottom: "1px solid", borderColor: "divider", flexShrink: 0,
      }}>
        <Tooltip arrow title="Back to environments">
          <IconButton size="small">
            <Iconify icon="solar:alt-arrow-left-linear" width={17} />
          </IconButton>
        </Tooltip>
        <Box flex={1} minWidth={0}>
          <Typography noWrap sx={{ typography: "s1_2", fontWeight: 700 }}>{env?.name}</Typography>
          <Typography noWrap sx={{ typography: "s2", color: "text.subtitle" }}>
            Spinning up {services.length} clone sandbox{services.length === 1 ? "" : "es"} — wire the agent as this lands
          </Typography>
        </Box>
        <Stack direction="row" alignItems="center" spacing={0.75} sx={{
          px: 1, py: 0.5, borderRadius: 999,
          border: "1px solid", borderColor: "divider",
          bgcolor: "background.neutral",
          color: "text.secondary",
        }}>
          <Box sx={{
            width: 7, height: 7, borderRadius: "50%",
            bgcolor: "#F59E0B",
            animation: `${beamPulse} 1.4s ease-in-out infinite`,
          }} />
          <Typography sx={{ typography: "s3", fontWeight: 700, letterSpacing: 0.3 }}>
            PROVISIONING
          </Typography>
        </Stack>
      </Stack>

      {/* split body */}
      <Box sx={{
        flex: 1, minHeight: 0, display: "grid", gap: 2, p: 2,
        gridTemplateColumns: { xs: "1fr", lg: "minmax(360px, 400px) 1fr" },
      }}>
        {/* left — narrating chat */}
        <SectionCard sx={{ height: "100%", minHeight: 0, display: "flex", flexDirection: "column", overflow: "hidden" }}>
          <AssistantConsole
            turns={turns}
            running={phaseIdx < PHASES.length}
            chips={phaseIdx < PHASES.length ? chips : []}
            onSend={() => {}}
            onChip={() => {}}
          />
        </SectionCard>

        {/* right — animation + step list */}
        <SectionCard sx={{ minHeight: 0, display: "flex", flexDirection: "column", overflow: "hidden", px: 2.5 }}>
          <Box sx={{ pt: 2, overflow: "auto" }}>
            <SandboxSpinup services={services} phaseIdx={phaseIdx} serviceProgress={serviceProgress} />
            <Box sx={{ mt: 2.5 }}>
              <PhaseTimeline phaseIdx={phaseIdx} services={services} />
            </Box>
          </Box>
        </SectionCard>
      </Box>
    </Box>
  );
}
TwinProvisioningView.propTypes = {
  env: PropTypes.object,
  envState: PropTypes.object,
  onDone: PropTypes.func,
};

/* ── sandbox mockup grid ────────────────────────────────────────────── */

function SandboxSpinup({ services, phaseIdx }) {
  const phaseKey = PHASES[phaseIdx]?.key || "ready";
  return (
    <Box>
      <Stack direction="row" alignItems="center" spacing={1} sx={{ mb: 1 }}>
        <Box sx={{
          width: 6, height: 6, borderRadius: "50%", bgcolor: "#F59E0B",
          animation: `${beamPulse} 1.4s ease-in-out infinite`,
        }} />
        <Typography sx={{ typography: "s3", fontWeight: 700, color: "text.subtitle", textTransform: "uppercase", letterSpacing: 0.4 }}>
          Sandbox — {services.length} service{services.length === 1 ? "" : "s"} spinning up
        </Typography>
        <Box flex={1} />
        <Typography sx={{
          typography: "s3", color: "text.subtitle", fontFamily: "ui-monospace, Menlo, monospace",
        }}>
          clone-runtime · {phaseKey}
        </Typography>
      </Stack>
      <Box sx={{
        position: "relative", borderRadius: 1.5, overflow: "hidden",
        border: "1px solid", borderColor: "divider",
        bgcolor: (t) => t.palette.mode === "dark" ? "#0B0B0B" : "#FAFAFA",
        p: 2, minHeight: 340,
      }}>
        {/* subtle grid */}
        <Box sx={{
          position: "absolute", inset: 0, pointerEvents: "none",
          backgroundImage: (t) => `linear-gradient(${alpha(t.palette.text.primary, t.palette.mode === "dark" ? 0.05 : 0.04)} 1px, transparent 1px),
                                    linear-gradient(90deg, ${alpha(t.palette.text.primary, t.palette.mode === "dark" ? 0.05 : 0.04)} 1px, transparent 1px)`,
          backgroundSize: "22px 22px",
        }} />

        {/* Control-plane hub on the left + streaming beams to each service */}
        <Box sx={{ position: "relative", display: "grid", gap: 2, gridTemplateColumns: "auto 1fr", alignItems: "stretch" }}>
          <ControlHub phaseIdx={phaseIdx} />
          <Box sx={{
            display: "grid", gap: 1.25,
            gridTemplateColumns: services.length <= 2 ? "1fr 1fr" : "1fr 1fr 1fr",
          }}>
            {services.map((sId, i) => (
              <ServiceCard key={sId} serviceId={sId} phaseIdx={phaseIdx} orderIndex={i} />
            ))}
          </Box>
        </Box>
      </Box>
    </Box>
  );
}
SandboxSpinup.propTypes = { services: PropTypes.array, phaseIdx: PropTypes.number };

/*
  Left-side hub — the "twin runtime" that provisions everything. A
  glowing circular node with the FAGI mark, particle-emitting when
  active. Reads as the source of the traffic the service cards on the
  right are receiving.
*/
function ControlHub({ phaseIdx }) {
  const done = phaseIdx >= PHASES.length;
  return (
    <Box sx={{
      position: "relative", width: 90, minHeight: 260,
      display: "flex", flexDirection: "column", alignItems: "center", justifyContent: "center",
      gap: 1,
    }}>
      <Box sx={{
        position: "relative",
        width: 62, height: 62, borderRadius: "50%",
        display: "grid", placeItems: "center",
        bgcolor: (t) => alpha(t.palette.text.primary, t.palette.mode === "dark" ? 0.08 : 0.05),
        border: (t) => `1.5px solid ${alpha(t.palette.text.primary, t.palette.mode === "dark" ? 0.28 : 0.2)}`,
        animation: done ? "none" : `${beamPulse} 1.6s ease-in-out infinite`,
      }}>
        <Iconify icon="solar:server-square-linear" width={22} sx={{ color: "text.primary" }} />
        {/* orbiting particles — neutral text-primary so they read as
            "data leaving the runtime", not "look how purple this is" */}
        {!done && [0, 0.4, 0.8, 1.2].map((d, i) => (
          <Box key={i} sx={{
            position: "absolute", top: "50%", left: "50%",
            width: 5, height: 5, borderRadius: "50%",
            bgcolor: (t) => alpha(t.palette.text.primary, t.palette.mode === "dark" ? 0.7 : 0.55),
            boxShadow: (t) => `0 0 6px ${alpha(t.palette.text.primary, t.palette.mode === "dark" ? 0.35 : 0.2)}`,
            "--dx": `${80 + (i % 2) * 20}px`,
            "--dy": `${(i - 1.5) * 40}px`,
            animation: `${particleFly} 1.2s linear infinite`,
            animationDelay: `${d}s`,
          }} />
        ))}
      </Box>
      <Typography sx={{
        typography: "s3", fontWeight: 700, color: "text.subtitle",
        textTransform: "uppercase", letterSpacing: 0.4,
      }}>
        Runtime
      </Typography>
      {/* status pill */}
      <Box sx={{
        px: 0.75, py: 0.125, borderRadius: 0.5,
        typography: "s3", fontWeight: 700, fontSize: 9, letterSpacing: 0.3,
        color: done ? SUCCESS : "text.secondary",
        bgcolor: (t) => done
          ? alpha(SUCCESS, t.palette.mode === "dark" ? 0.16 : 0.09)
          : alpha(t.palette.text.primary, t.palette.mode === "dark" ? 0.1 : 0.06),
      }}>
        {done ? "IDLE" : "STREAMING"}
      </Box>
    </Box>
  );
}
ControlHub.propTypes = { phaseIdx: PropTypes.number };

function ServiceCard({ serviceId, phaseIdx, orderIndex }) {
  const twin = twinById(serviceId);
  const phaseKey = PHASES[phaseIdx]?.key;
  const done = phaseIdx >= PHASES.length;
  const active = !done && !!phaseKey;

  /* Rolling counter that flips with each phase — reads as a live
     status the service is reporting back. */
  const counterTargets = {
    provision: { label: "instances", value: 1 },
    seed: { label: "rows seeded", value: 18 + (serviceId.length * 3) % 40 },
    rotate: { label: "tokens minted", value: 2 },
    probe: { label: "probe ms", value: 40 + (serviceId.length * 7) % 90 },
  };
  const ct = counterTargets[phaseKey] || { label: "ready", value: 100 };
  const n = useTicker(ct.value, active, 750);

  /* Streaming log lines — reset per phase, appear one by one with a
     small stagger. Reads as a real service coming online, not a bar
     ticking silently. */
  const lines = active ? logLinesFor(phaseKey, twin?.name) : [];

  return (
    <Box sx={{
      p: 1.25, borderRadius: 1, minHeight: 260,
      display: "flex", flexDirection: "column",
      border: (t) => `1px solid ${alpha(t.palette.text.primary, t.palette.mode === "dark" ? (active ? 0.28 : 0.14) : (active ? 0.2 : 0.1))}`,
      bgcolor: (t) => t.palette.mode === "dark" ? "#131313" : "#FFFFFF",
      boxShadow: (t) => t.palette.mode === "dark" ? "0 4px 12px rgba(0,0,0,0.35)" : "0 4px 12px rgba(16,24,40,0.06)",
      position: "relative",
      overflow: "hidden",
    }}>
      {/* incoming beam from left — neutral so it reads as data flow
          rather than brand purple every direction you look */}
      {active && (
        <Box sx={{
          position: "absolute", left: -18, top: 22, width: 18, height: 2,
          background: (t) => `linear-gradient(90deg, transparent, ${alpha(t.palette.text.primary, t.palette.mode === "dark" ? 0.65 : 0.5)})`,
          animation: `${beamPulse} 1.2s ease-in-out infinite`,
          animationDelay: `${orderIndex * 150}ms`,
        }} />
      )}

      {/* header */}
      <Stack direction="row" alignItems="center" spacing={1} sx={{ mb: 0.75 }}>
        <TwinLogo twin={twin} width={16} />
        <Typography sx={{ typography: "s2", fontWeight: 700, flex: 1 }} noWrap>
          {twin?.name || serviceId}
        </Typography>
        {done ? (
          <Iconify icon="solar:check-circle-bold" width={13} sx={{ color: SUCCESS }} />
        ) : (
          <Box sx={{
            width: 8, height: 8, borderRadius: "50%",
            bgcolor: "#F59E0B",
            animation: `${beamPulse} 1.4s ease-in-out infinite`,
            animationDelay: `${orderIndex * 120}ms`,
          }} />
        )}
      </Stack>

      {/* phase pips */}
      <Stack direction="row" spacing={0.5} sx={{ mb: 1 }}>
        {PHASES.map((p, i) => (
          <Box key={p.key} sx={{
            flex: 1, height: 4, borderRadius: 999,
            overflow: "hidden", position: "relative",
            bgcolor: (t) => alpha(t.palette.text.primary, t.palette.mode === "dark" ? 0.08 : 0.06),
          }}>
            <Box sx={{
              position: "absolute", inset: 0,
              bgcolor: (t) => i < phaseIdx
                ? SUCCESS
                : (i === phaseIdx ? alpha(t.palette.text.primary, t.palette.mode === "dark" ? 0.55 : 0.4) : "transparent"),
              width: i < phaseIdx ? "100%" : (i === phaseIdx ? "100%" : "0%"),
              backgroundImage: (t) => i === phaseIdx
                ? `linear-gradient(90deg, ${alpha(t.palette.text.primary, 0.2)} 0%, ${alpha(t.palette.text.primary, t.palette.mode === "dark" ? 0.8 : 0.55)} 50%, ${alpha(t.palette.text.primary, 0.2)} 100%)`
                : "none",
              backgroundSize: "200% 100%",
              animation: i === phaseIdx ? `${barShimmer} 1.6s linear infinite` : "none",
              transition: "width 220ms ease-out",
            }} />
          </Box>
        ))}
      </Stack>

      {/* live counter */}
      <Stack direction="row" alignItems="baseline" spacing={0.75} sx={{ mb: 1 }}>
        <Box sx={{
          overflow: "hidden", display: "inline-block",
        }}>
          <Typography key={`${phaseKey}-${n}`} sx={{
            typography: "s1", fontWeight: 800, lineHeight: 1,
            fontVariantNumeric: "tabular-nums",
            color: done ? SUCCESS : "text.primary",
            animation: `${counterFlick} 400ms ease-out`,
          }}>
            {done ? "ready" : n}
            {!done && phaseKey === "probe" && n > 0 && (
              <Box component="span" sx={{ typography: "s3", fontWeight: 600, ml: 0.25, color: "text.subtitle" }}>ms</Box>
            )}
          </Typography>
        </Box>
        <Typography sx={{
          typography: "s3", color: "text.subtitle",
          textTransform: "uppercase", letterSpacing: 0.4, fontWeight: 700,
        }}>
          {done ? "sandbox live" : ct.label}
        </Typography>
      </Stack>

      {/* streaming log — the "something is happening in there" tell */}
      <Box sx={{
        flex: 1, minHeight: 0,
        borderRadius: 0.75, p: 1,
        border: (t) => `1px solid ${alpha(t.palette.text.primary, t.palette.mode === "dark" ? 0.08 : 0.06)}`,
        bgcolor: (t) => t.palette.mode === "dark" ? "#0A0A0A" : "#F5F6F7",
        overflow: "hidden",
      }}>
        {done ? (
          <Stack direction="row" alignItems="center" spacing={0.75} sx={{ height: "100%" }}>
            <Iconify icon="solar:check-circle-bold" width={12} sx={{ color: SUCCESS }} />
            <Typography sx={{
              typography: "s3", color: SUCCESS, fontWeight: 700,
              fontFamily: "ui-monospace, Menlo, monospace",
            }}>
              serving · idle
            </Typography>
          </Stack>
        ) : (
          <Stack spacing={0.375}>
            {lines.map((l, i) => (
              <Typography key={`${phaseKey}-${i}`}
                sx={{
                  typography: "s3", fontSize: 10.5, lineHeight: 1.4,
                  color: "text.secondary",
                  fontFamily: "ui-monospace, Menlo, monospace",
                  opacity: 0,
                  animation: `${logIn} 260ms ease-out forwards`,
                  animationDelay: `${140 + i * 220}ms`,
                }} noWrap>
                <Box component="span" sx={{ color: "text.disabled", mr: 0.5 }}>
                  {`0.${String(i * 2 + 1).padStart(2, "0")}s`}
                </Box>
                {l}
              </Typography>
            ))}
          </Stack>
        )}
      </Box>

      {/* dropped-chip pool — each phase drops one chip that accumulates */}
      <Stack direction="row" spacing={0.5} sx={{ mt: 1 }} flexWrap="wrap" useFlexGap>
        {PHASES.slice(0, Math.min(phaseIdx + 1, PHASES.length)).map((p, i) => {
          const settled = i < phaseIdx;
          if (!settled) return null;
          return (
            <Box key={p.key} sx={{
              px: 0.75, py: 0.125, borderRadius: 0.5,
              typography: "s3", fontSize: 9.5, fontWeight: 700, letterSpacing: 0.3,
              color: SUCCESS,
              bgcolor: (t) => alpha(SUCCESS, t.palette.mode === "dark" ? 0.16 : 0.1),
              animation: `${chipDrop} 260ms ease-out`,
            }}>
              {p.key.toUpperCase()}
            </Box>
          );
        })}
      </Stack>
    </Box>
  );
}
ServiceCard.propTypes = {
  serviceId: PropTypes.string, phaseIdx: PropTypes.number, orderIndex: PropTypes.number,
};

/* ── step timeline ───────────────────────────────────────────────────── */

function PhaseTimeline({ phaseIdx, services }) {
  return (
    <Box>
      <Stack direction="row" alignItems="center" spacing={1} sx={{ mb: 1.5 }}>
        <Typography sx={{ typography: "s3", fontWeight: 700, color: "text.subtitle", textTransform: "uppercase", letterSpacing: 0.4 }}>
          Setup — spinning up the sandbox
        </Typography>
        <Box flex={1} />
        <Typography sx={{ typography: "s3", color: "text.subtitle", fontVariantNumeric: "tabular-nums" }}>
          {Math.min(phaseIdx, PHASES.length)} of {PHASES.length}
        </Typography>
      </Stack>
      <Stack>
        {PHASES.map((phase, i) => {
          const done = i < phaseIdx;
          const running = i === phaseIdx;
          const pending = i > phaseIdx;
          const tone = done ? SUCCESS : running ? "text.primary" : undefined;
          return (
            <Stack key={phase.key} direction="row" spacing={1.5} sx={{
              px: 1.5, py: 1.25, borderRadius: 1,
              bgcolor: (t) => running
                ? alpha(t.palette.text.primary, t.palette.mode === "dark" ? 0.06 : 0.04)
                : "transparent",
              opacity: pending ? 0.55 : 1,
              transition: "background-color .2s ease, opacity .2s ease",
            }}>
              <Box sx={{
                width: 22, height: 22, borderRadius: "50%", flexShrink: 0,
                display: "grid", placeItems: "center",
                bgcolor: (t) => done
                  ? alpha(SUCCESS, t.palette.mode === "dark" ? 0.18 : 0.12)
                  : running
                    ? alpha(t.palette.text.primary, t.palette.mode === "dark" ? 0.14 : 0.09)
                    : alpha(t.palette.text.primary, t.palette.mode === "dark" ? 0.06 : 0.05),
                color: tone || "text.subtitle",
                animation: running ? `${beamPulse} 1.6s ease-in-out infinite` : "none",
              }}>
                <Iconify
                  icon={done ? "solar:check-circle-bold" : running ? "solar:refresh-circle-linear" : phaseIconFor(phase.key)}
                  width={12}
                  sx={{ animation: running ? "spin 1.4s linear infinite" : "none",
                    "@keyframes spin": { to: { transform: "rotate(360deg)" } } }}
                />
              </Box>
              <Box flex={1} minWidth={0}>
                <Stack direction="row" alignItems="baseline" spacing={1}>
                  <Typography sx={{ typography: "s2", fontWeight: 700 }}>
                    {phase.title}
                  </Typography>
                  {done && (
                    <Typography sx={{ typography: "s3", color: "text.subtitle", fontVariantNumeric: "tabular-nums" }}>
                      {(phase.dwell / 1000).toFixed(1)}s
                    </Typography>
                  )}
                </Stack>
                <Typography sx={{ typography: "s3", color: "text.subtitle" }}>
                  {phase.subtitle}
                </Typography>
              </Box>
            </Stack>
          );
        })}
      </Stack>
    </Box>
  );
}
PhaseTimeline.propTypes = { phaseIdx: PropTypes.number, services: PropTypes.array };

function phaseIconFor(key) {
  return ({
    provision: "solar:server-square-linear",
    seed: "solar:database-linear",
    rotate: "solar:key-linear",
    probe: "solar:pulse-2-linear",
  })[key] || "solar:box-linear";
}
