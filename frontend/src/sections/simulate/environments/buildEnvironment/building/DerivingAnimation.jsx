import PropTypes from "prop-types";
import { alpha, useTheme, keyframes } from "@mui/material/styles";
import { Box, Stack, Typography } from "@mui/material";
import Iconify from "src/components/iconify";

import { SURFACE_INK, TRAFFIC_LIGHTS } from "../buildTones";
import { DERIVING_LABEL } from "../build.constants";
import { LandedChip, MiniCount, KIND_COLOR } from "./LandedChip";

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
 * This used to animate a canned token list (the same tools/rules/tables every
 * build). Now it's driven by the real job: the source panel names the actual
 * repo/source being read, and the sandbox fills with the REAL derived tools /
 * rules / tables from the harness job's stage outputs. Those only exist once
 * the "Generating environment" stage lands, and only the genuinely-derived
 * world reaches this component (never the MOCK_WORLD overlay), so before then
 * the container shows neutral skeleton pills rather than invented names.
 */

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

const pulseSoft = keyframes`
  0%,100% { opacity: 0.55; }
  50%     { opacity: 1; }
`;

const shimmerBg = keyframes`
  0%   { background-position: 0% 50%; }
  100% { background-position: 200% 50%; }
`;

const truncate = (s, n) => {
  const str = String(s || "");
  return str.length > n ? `${str.slice(0, n - 1)}…` : str;
};

// A hard_constraint is a full sentence; trim the trailing period and clip it so
// it reads as a chip rather than a paragraph.
const shortRule = (r) => truncate(String(r || "").replace(/\.\s*$/, ""), 22);

// Real derived artifacts → the chips that land in the sandbox. Tools and tables
// carry natural short labels; rules are clipped sentences. Capped so a large
// world can't overflow the container.
function chipsFromWorld(world) {
  const tools = world?.tools || [];
  const rules = world?.rules || [];
  const tables = world?.seed?.tables || [];
  const chips = [
    ...tools.map((t, i) => ({ id: `tool-${i}`, kind: "tool", label: t.name })),
    ...tables.map((t, i) => ({
      id: `data-${i}`,
      kind: "data",
      label: t.rows != null ? `${t.name} × ${t.rows}` : t.name,
    })),
    ...rules.map((r, i) => ({ id: `rule-${i}`, kind: "rule", label: shortRule(r) })),
  ].filter((c) => c.label);
  return {
    chips: chips.slice(0, 14),
    counts: { tool: tools.length, rule: rules.length, data: tables.length },
  };
}

const SKELETON_WIDTHS = [64, 92, 48, 76, 58, 84];

export default function DerivingAnimation({ label, source, world = null }) {
  const theme = useTheme();
  const dark = theme.palette.mode === "dark";

  const { chips, counts } = chipsFromWorld(world);
  const hasReal = chips.length > 0;
  const fileLabel = source ? truncate(source, 26) : DERIVING_LABEL.readingSource;

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
        <Typography sx={{ typography: "s2", fontWeight: "fontWeightSemiBold", color: "text.secondary" }}>
          {label || DERIVING_LABEL.idle}
        </Typography>
      </Stack>

      {/* the illustration */}
      <Box
        sx={{
          position: "relative", height: 260, borderRadius: 2, overflow: "hidden",
          border: "1px solid", borderColor: "divider",
          bgcolor: dark ? SURFACE_INK.dark : SURFACE_INK.light,
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
            bgcolor: dark ? SURFACE_INK.panelDark : SURFACE_INK.panelLight,
            border: "1px solid",
            borderColor: alpha(theme.palette.text.primary, dark ? 0.1 : 0.08),
            boxShadow: dark ? "0 8px 32px rgba(0,0,0,0.45)" : "0 8px 32px rgba(16,24,40,0.08)",
          }}
        >
          {/* file header — the real source being read */}
          <Stack
            direction="row" alignItems="center" spacing={0.5}
            sx={{
              px: 1.25, py: 0.75, borderBottom: "1px solid",
              borderColor: alpha(theme.palette.text.primary, dark ? 0.08 : 0.06),
            }}
          >
            <Box sx={{ width: 6, height: 6, borderRadius: "50%", bgcolor: TRAFFIC_LIGHTS[0] }} />
            <Box sx={{ width: 6, height: 6, borderRadius: "50%", bgcolor: TRAFFIC_LIGHTS[1] }} />
            <Box sx={{ width: 6, height: 6, borderRadius: "50%", bgcolor: TRAFFIC_LIGHTS[2] }} />
            <Box sx={{ flex: 1 }} />
            <Typography
              title={source || undefined}
              sx={{ typography: "s3", color: "text.disabled", fontFamily: "ui-monospace, Menlo, monospace", fontSize: 9, whiteSpace: "nowrap" }}
            >
              {fileLabel}
            </Typography>
          </Stack>

          {/* code lines — an abstract "reading source" shimmer, not a real file */}
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
            bgcolor: dark ? SURFACE_INK.sandboxDark : SURFACE_INK.panelLight,
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
            <Typography sx={{ typography: "s3", color: "text.secondary", fontWeight: "fontWeightBold", letterSpacing: 0.5 }}>
              SANDBOX
            </Typography>
            <Box flex={1} />
            <Typography sx={{ typography: "s3", color: "text.disabled", fontVariantNumeric: "tabular-nums", fontSize: 10 }}>
              {counts.tool + counts.rule + counts.data}
            </Typography>
          </Stack>

          {/* landed chips — real derived world, or neutral skeleton pills until
              the environment stage lands */}
          <Box sx={{ p: 1, height: 172, overflow: "hidden" }}>
            <Stack direction="row" flexWrap="wrap" gap={0.5}>
              {hasReal
                ? chips.map((item) => <LandedChip key={item.id} item={item} dark={dark} />)
                : SKELETON_WIDTHS.map((w, i) => (
                    <Box
                      key={i}
                      sx={{
                        height: 20, width: w, borderRadius: 999,
                        background: `linear-gradient(90deg,
                          ${alpha(theme.palette.text.primary, dark ? 0.06 : 0.05)} 0%,
                          ${alpha(theme.palette.text.primary, dark ? 0.12 : 0.09)} 50%,
                          ${alpha(theme.palette.text.primary, dark ? 0.06 : 0.05)} 100%)`,
                        backgroundSize: "200% 100%",
                        animation: `${shimmerBg} 1.8s linear infinite`,
                      }}
                    />
                  ))}
            </Stack>
          </Box>
        </Box>

        {/* ─── legend under the sandbox — real tallies ─── */}
        <Stack
          direction="row" spacing={1.5}
          sx={{ position: "absolute", bottom: 10, right: 24 }}
        >
          <MiniCount color={KIND_COLOR.tool} label={`${counts.tool} tools`} />
          <MiniCount color={KIND_COLOR.rule} label={`${counts.rule} rules`} />
          <MiniCount color={KIND_COLOR.data} label={`${counts.data} tables`} />
        </Stack>
      </Box>
    </Box>
  );
}

DerivingAnimation.propTypes = {
  label: PropTypes.string,
  // The real source being read (repo/spec) — shown in the file header.
  source: PropTypes.string,
  // The real derived world from stage outputs: { tools:[{name}], rules:[str],
  // seed:{ tables:[{name, rows?}] } }. Null/empty until the stage lands.
  world: PropTypes.object,
};
