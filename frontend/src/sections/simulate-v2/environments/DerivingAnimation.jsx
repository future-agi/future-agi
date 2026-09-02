import PropTypes from "prop-types";
import { useEffect, useState } from "react";
import { alpha, useTheme, keyframes } from "@mui/material/styles";
import { Box, Stack, Typography } from "@mui/material";
import Iconify from "src/components/iconify";

/**
 * Hero illustration for the derivation panel.
 *
 * The right pane sat empty while the builder worked. Skeleton bars said
 * "something is coming"; a live discovery list said "these are the things".
 * Neither said *what the engine is actually doing*, which is the point: it is
 * reading a codebase and turning it into a sandbox. That transformation is a
 * shape — a source on one side, particles crossing a beam, a container filling
 * on the other — and drawing it directly is worth more than any amount of
 * incremental text.
 *
 * Every element here has a job. The scanning beam moves through the source
 * file at a real read pace. Particles emit from wherever the beam is, arc
 * toward the container, and land as a tool/rule/data icon that stays. Counts
 * next to the container tick up as each icon arrives — the discovery is
 * literal, not decorative.
 */

const TOKENS = [
  { kind: "tool", icon: "solar:code-scan-bold", label: "verify_identity" },
  { kind: "rule", icon: "solar:shield-check-bold", label: "return-window rule" },
  { kind: "tool", icon: "solar:code-scan-bold", label: "lookup_order" },
  { kind: "data", icon: "solar:database-bold", label: "customers × 240" },
  { kind: "tool", icon: "solar:code-scan-bold", label: "issue_refund" },
  { kind: "rule", icon: "solar:shield-check-bold", label: "OTP read-aloud rule" },
  { kind: "data", icon: "solar:database-bold", label: "orders × 610" },
  { kind: "tool", icon: "solar:code-scan-bold", label: "escalate_to_human" },
  { kind: "rule", icon: "solar:shield-check-bold", label: "goodwill cap" },
  { kind: "data", icon: "solar:database-bold", label: "payments × 480" },
  { kind: "tool", icon: "solar:code-scan-bold", label: "send_replacement" },
  { kind: "tool", icon: "solar:code-scan-bold", label: "get_refund_quote" },
];

/*
  Three semantic colours — teal for tools, green for rules, amber for data.
  The neutral single-tone version read as too muted; at a glance the sandbox
  filling up with distinct colours signals "different kinds of things
  arriving", which is the story worth telling here.
*/
const KIND_COLOR = {
  tool: "#0891B2",
  rule: "#16A34A",
  data: "#B45309",
};

/** Emit a new token every `everyMs`, cap at TOKENS.length; caller receives the array so far. */
function useEmit(everyMs = 850, cap = TOKENS.length) {
  const [items, setItems] = useState([]);
  useEffect(() => {
    setItems([]);
    let i = 0;
    const t = setInterval(() => {
      i += 1;
      setItems((prev) => {
        if (prev.length >= cap) return prev;
        const next = TOKENS[(i - 1) % TOKENS.length];
        return [...prev, { ...next, id: `${i}-${next.label}` }];
      });
    }, everyMs);
    return () => clearInterval(t);
  }, [everyMs, cap]);
  return items;
}

const beamMove = keyframes`
  0%   { transform: translateY(6px); opacity: 0.4; }
  15%  { opacity: 1; }
  85%  { opacity: 1; }
  100% { transform: translateY(184px); opacity: 0.4; }
`;

const codeAppear = keyframes`
  0% { opacity: 0; transform: translateX(-4px); }
  100% { opacity: 1; transform: translateX(0); }
`;

const particleFly = keyframes`
  0%   { transform: translate(0, 0) scale(1); opacity: 0; }
  15%  { opacity: 1; }
  85%  { opacity: 1; }
  100% { transform: translate(var(--dx), var(--dy)) scale(0.6); opacity: 0; }
`;

const chipLand = keyframes`
  0%   { opacity: 0; transform: translateY(6px) scale(0.9); }
  60%  { opacity: 1; transform: translateY(-1px) scale(1.04); }
  100% { opacity: 1; transform: translateY(0)  scale(1); }
`;

const pulseSoft = keyframes`
  0%,100% { opacity: 0.55; }
  50%     { opacity: 1; }
`;

const shimmerBg = keyframes`
  0%   { background-position: 0% 50%; }
  100% { background-position: 200% 50%; }
`;

export default function DerivingAnimation({ label }) {
  const theme = useTheme();
  const dark = theme.palette.mode === "dark";
  const items = useEmit(850);

  const toolCount = items.filter((i) => i.kind === "tool").length;
  const ruleCount = items.filter((i) => i.kind === "rule").length;
  const dataCount = items.filter((i) => i.kind === "data").length;

  return (
    <Box sx={{ px: 2.5, pt: 1 }}>
      {/* live phase label */}
      <Stack
        direction="row" alignItems="center" spacing={1} sx={{ px: 0.5, mb: 1.5 }}
      >
        <Box
          sx={{
            width: 6, height: 6, borderRadius: "50%", flexShrink: 0,
            bgcolor: "text.disabled",
            animation: `${pulseSoft} 1.4s ease-in-out infinite`,
          }}
        />
        <Typography sx={{ typography: "s2", fontWeight: 600, color: "text.secondary" }}>
          {label || "Reading your agent…"}
        </Typography>
      </Stack>

      {/* the illustration */}
      <Box
        sx={{
          position: "relative", height: 260, borderRadius: 2, overflow: "hidden",
          border: "1px solid", borderColor: "divider",
          /* Neutral near-black instead of #0B0B12 — the higher blue
             channel in the old value read as cool/purple against the
             chip colors. */
          bgcolor: dark ? "#0B0B0B" : "#FAFAFA",
        }}
      >
        {/* subtle grid so it doesn't feel like an empty box */}
        <Box
          sx={{
            position: "absolute", inset: 0, pointerEvents: "none",
            backgroundImage: `linear-gradient(${alpha(theme.palette.text.primary, dark ? 0.05 : 0.04)} 1px, transparent 1px),
                              linear-gradient(90deg, ${alpha(theme.palette.text.primary, dark ? 0.05 : 0.04)} 1px, transparent 1px)`,
            backgroundSize: "22px 22px",
          }}
        />

        {/* ─── source panel (left) ─── */}
        <Box
          sx={{
            position: "absolute", top: 30, left: 24, width: 200, height: 200,
            borderRadius: 1.5, overflow: "hidden",
            bgcolor: dark ? "#131313" : "#FFFFFF",
            border: "1px solid",
            borderColor: alpha(theme.palette.text.primary, dark ? 0.1 : 0.08),
            boxShadow: dark ? "0 8px 32px rgba(0,0,0,0.45)" : "0 8px 32px rgba(16,24,40,0.08)",
          }}
        >
          {/* file header */}
          <Stack
            direction="row" alignItems="center" spacing={0.5}
            sx={{
              px: 1.25, py: 0.75, borderBottom: "1px solid",
              borderColor: alpha(theme.palette.text.primary, dark ? 0.08 : 0.06),
            }}
          >
<Box sx={{ width: 6, height: 6, borderRadius: "50%", bgcolor: "#EF4444" }} />
            <Box sx={{ width: 6, height: 6, borderRadius: "50%", bgcolor: "#F59E0B" }} />
            <Box sx={{ width: 6, height: 6, borderRadius: "50%", bgcolor: "#10B981" }} />
            <Box sx={{ flex: 1 }} />
            <Typography sx={{ typography: "s3", color: "text.disabled", fontFamily: "ui-monospace, Menlo, monospace", fontSize: 9 }}>
              handlers/refunds.py
            </Typography>
          </Stack>

          {/* code lines */}
          <Box sx={{ position: "relative", height: 172, py: 1, px: 1.25 }}>
            {[92, 60, 78, 40, 84, 66, 52, 74, 46, 88, 62, 70].map((w, i) => (
              <Box
                key={i}
                sx={{
                  display: "flex", alignItems: "center", gap: 0.75,
                  py: 0.375, opacity: 0,
                  animation: `${codeAppear} 0.4s ease-out forwards`,
                  animationDelay: `${i * 90}ms`,
                }}
              >
                <Typography sx={{ typography: "s3", color: "text.disabled", fontFamily: "ui-monospace, Menlo, monospace", fontSize: 8.5, width: 12 }}>
                  {i + 1}
                </Typography>
                <Box
                  sx={{
                    height: 4, width: `${w}%`, borderRadius: 999,
                    background: alpha(theme.palette.text.primary, dark ? 0.16 : 0.12),
                  }}
                />
              </Box>
            ))}

            {/* the scanning beam */}
            <Box
              sx={{
                position: "absolute", left: 8, right: 8, top: 0, height: 14, pointerEvents: "none",
                animation: `${beamMove} 3.2s ease-in-out infinite`,
              }}
            >
              <Box
                sx={{
                  height: "100%", borderRadius: 999,
                  background: `linear-gradient(90deg, transparent, ${alpha(theme.palette.text.primary, dark ? 0.55 : 0.45)}, transparent)`,
                  boxShadow: `0 0 10px ${alpha(theme.palette.text.primary, dark ? 0.3 : 0.2)}`,
                }}
              />
            </Box>
          </Box>
        </Box>

        {/* ─── conveyor (middle) ─── */}
        <Box
          sx={{
            position: "absolute", top: "50%", left: 224, right: 224, height: 2,
            transform: "translateY(-50%)",
            background: `linear-gradient(90deg,
              transparent 0%,
              ${alpha(theme.palette.text.primary, dark ? 0.28 : 0.22)} 50%,
              transparent 100%)`,
          }}
        />
        <Box
          sx={{
            position: "absolute", top: "50%", left: 224, right: 224, height: 20,
            transform: "translateY(-50%)",
            background: `linear-gradient(90deg,
              transparent 0%,
              ${alpha(theme.palette.text.primary, dark ? 0.08 : 0.05)} 50%,
              transparent 100%)`,
            backgroundSize: "200% 100%",
            animation: `${shimmerBg} 2s linear infinite`,
          }}
        />

        {/* particles emitted from the beam */}
        {[0, 0.3, 0.6, 0.9, 1.2, 1.5, 1.8, 2.1].map((delay, i) => (
          <Box
            key={i}
            sx={{
              position: "absolute", top: "50%", left: 210,
              width: 5, height: 5, borderRadius: "50%",
              bgcolor: alpha(theme.palette.text.primary, dark ? 0.7 : 0.6),
              boxShadow: `0 0 6px ${alpha(theme.palette.text.primary, dark ? 0.35 : 0.25)}`,
              transform: "translate(0, 0)",
              "--dx": `${310}px`,
              "--dy": `${(i % 2 === 0 ? -1 : 1) * 8}px`,
              animation: `${particleFly} 1.6s linear infinite`,
              animationDelay: `${delay}s`,
            }}
          />
        ))}

        {/* ─── sandbox (right) ─── */}
        <Box
          sx={{
            position: "absolute", top: 30, right: 24, width: 240, height: 200,
            borderRadius: 1.5, overflow: "hidden",
            bgcolor: dark ? "#111111" : "#FFFFFF",
            border: "1px solid",
            borderColor: alpha(theme.palette.text.primary, dark ? 0.15 : 0.12),
            boxShadow: dark ? "0 8px 32px rgba(0,0,0,0.45)" : "0 8px 32px rgba(16,24,40,0.08)",
          }}
        >
          {/* sandbox label */}
          <Stack
            direction="row" alignItems="center" spacing={0.75}
            sx={{
              px: 1.25, py: 0.75, borderBottom: "1px solid",
              borderColor: alpha(theme.palette.text.primary, dark ? 0.08 : 0.06),
            }}
          >
            <Iconify icon="solar:box-linear" width={12} sx={{ color: "text.subtitle" }} />
            <Typography sx={{ typography: "s3", color: "text.secondary", fontWeight: 700, letterSpacing: 0.5 }}>
              SANDBOX
            </Typography>
            <Box flex={1} />
            <Typography sx={{ typography: "s3", color: "text.disabled", fontVariantNumeric: "tabular-nums", fontSize: 10 }}>
              {items.length}
            </Typography>
          </Stack>

          {/* landed chips */}
          <Box sx={{ p: 1, height: 172, overflow: "hidden" }}>
            <Stack direction="row" flexWrap="wrap" gap={0.5}>
              {items.map((item) => (
                <LandedChip key={item.id} item={item} dark={dark} />
              ))}
            </Stack>
          </Box>
        </Box>

        {/* ─── legend under the sandbox ─── */}
        <Stack
          direction="row" spacing={1.5}
          sx={{ position: "absolute", bottom: 10, right: 24 }}
        >
          <MiniCount color={KIND_COLOR.tool} label={`${toolCount} tools`} />
          <MiniCount color={KIND_COLOR.rule} label={`${ruleCount} rules`} />
          <MiniCount color={KIND_COLOR.data} label={`${dataCount} tables`} />
        </Stack>
      </Box>
    </Box>
  );
}

DerivingAnimation.propTypes = { label: PropTypes.string };

function LandedChip({ item, dark }) {
  return (
    <Stack
      direction="row" alignItems="center" spacing={0.5}
      sx={{
        px: 0.75, py: 0.375, borderRadius: 0.75,
        border: "1px solid",
        borderColor: alpha(KIND_COLOR[item.kind], 0.35),
        bgcolor: alpha(KIND_COLOR[item.kind], dark ? 0.14 : 0.08),
        animation: `${chipLand} 0.35s cubic-bezier(0.2, 0.9, 0.2, 1.2) forwards`,
      }}
    >
      <Iconify icon={item.icon} width={10} sx={{ color: KIND_COLOR[item.kind] }} />
      <Typography sx={{ typography: "s3", fontSize: 10, fontWeight: 600, color: "text.primary" }}>
        {item.label}
      </Typography>
    </Stack>
  );
}
LandedChip.propTypes = { item: PropTypes.object, dark: PropTypes.bool };

function MiniCount({ color, label }) {
  return (
    <Stack direction="row" alignItems="center" spacing={0.5}>
      <Box sx={{ width: 6, height: 6, borderRadius: "50%", bgcolor: color }} />
      <Typography sx={{ typography: "s3", fontSize: 10, color: "text.subtitle", fontVariantNumeric: "tabular-nums" }}>
        {label}
      </Typography>
    </Stack>
  );
}
MiniCount.propTypes = { color: PropTypes.string, label: PropTypes.string };
