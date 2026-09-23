import PropTypes from "prop-types";
import { useState } from "react";
import { useTheme, alpha } from "@mui/material/styles";
import { Box, Stack, Typography, Tab } from "@mui/material";
import { SegmentedTabs } from "src/components/tabs/tabs";
import Iconify from "src/components/iconify";
import CustomTooltip from "src/components/tooltip";
import { contractFor } from "src/api/simulate-environments/_fixtures/contract";
import { BUILD_TONES } from "../../buildEnvironment/buildTones";
import { ENV_SHAPE, ENV_STATE_SHAPE } from "./overview.constants";
import SectionCard from "../../components/SectionCard";

/**
 * The capability graph.
 *
 * The same facts the contract lists, drawn as what they actually are: one agent,
 * four kinds of thing hanging off it — Tools, Flows, Personas, Guardrails.
 *
 * A backed env passes real §6 data through `data` (tools names, real_use_cases,
 * world.personas names, hard_constraints); a branch the backend returned empty
 * renders an honest "none yet" leaf rather than a fixture stand-in. Without
 * `data` (a non-backed template/fork) it derives from the env + contract fixture.
 *
 * Plain SVG on purpose. MUI's Box routes width/height through the style system
 * and mangles SVG geometry.
 */
const BRANCHES = [
  { id: "tools", label: "Tools", color: BUILD_TONES.orange, icon: "solar:settings-minimalistic-linear" },
  { id: "flows", label: "Flows", color: BUILD_TONES.blue, icon: "solar:route-linear" },
  { id: "personas", label: "Personas", color: BUILD_TONES.accent, icon: "solar:users-group-rounded-linear" },
  { id: "guardrails", label: "Guardrails", color: BUILD_TONES.red, icon: "solar:shield-check-linear" },
];

// How many leaves a branch shows in the "All" view before it collapses to a
// "+N more" affordance. Focusing a single branch (the tab, or clicking "+N
// more") lifts the cap and shows every item.
const MAX_LEAVES = 5;

export default function CapabilityGraph({ env, envState, data }) {
  const theme = useTheme();
  const [focus, setFocus] = useState(null);
  const contract = contractFor(env);

  // Non-backed fallback: personas derived from scenario personas (deduped).
  const personaNames = (() => {
    const seen = new Map();
    (envState?.scenarios || []).forEach((s) => {
      if (!s.persona) return;
      const key = s.persona.slug || s.persona.name;
      if (!seen.has(key)) seen.set(key, s.persona.name);
    });
    return [...seen.values()];
  })();

  // Real §6 data when the caller passes it; otherwise the fixture derivation.
  const branchData = data || {
    tools: (env.tools || []).map((t) => t.name),
    flows: (contract.useCases || []).map((u) =>
      u.replace(/ using .*/, "").replace(/^Refuse the request that would break: /, "Refuse: "),
    ),
    personas: personaNames,
    guardrails: contract.hardRules || [],
  };

  const branches = BRANCHES.map((b) => ({ ...b, items: branchData[b.id] || [] }));
  const shown = focus ? branches.filter((b) => b.id === focus) : branches;
  // The focused tab shows its branch in full; the "All" view caps each branch at
  // MAX_LEAVES and surfaces the rest in a hover tooltip on "+N more" — no click,
  // so the user is never moved off the view they're on.
  const cap = focus ? Infinity : MAX_LEAVES;

  // geometry
  const rowH = 22;
  const headH = 34;
  const gap = 26;
  const colX = 250;
  const leafX = 400;
  // Every branch draws at least one row (a real leaf, the "+N more" line, or the
  // empty "none yet" placeholder), so the hub never floats over blank space.
  const rowsFor = (b) => {
    if (b.items.length === 0) return 1;
    const visible = Math.min(b.items.length, cap);
    return visible + (b.items.length > cap ? 1 : 0);
  };
  const heights = shown.map((b) => headH + rowsFor(b) * rowH);
  const totalH = heights.reduce((a, h) => a + h, 0) + gap * (shown.length - 1);
  const H = Math.max(260, totalH + 40);
  const W = 900;
  const agentY = H / 2;

  let cursor = 20;
  const placed = shown.map((b, i) => {
    const h = heights[i];
    const top = cursor;
    cursor += h + gap;
    return { ...b, top, h, hubY: top + headH / 2 };
  });

  const line = theme.palette.divider;
  const dim = theme.palette.text.secondary;
  const faint = theme.palette.text.disabled;
  const strong = theme.palette.text.primary;
  const bold = theme.typography.fontWeightBold;

  return (
    <SectionCard
      title="Capability graph"
      subtitle="Everything read from your agent, as one object rather than four lists"
      sx={{ mb: 2 }}
      action={
        <SegmentedTabs
          value={focus || "all"}
          onChange={(_, v) => setFocus(v === "all" ? null : v)}
          variant="scrollable"
          scrollButtons={false}
          sx={{ flexShrink: 0 }}
        >
          <Tab value="all" label="All" />
          {BRANCHES.map((b) => (
            <Tab key={b.id} value={b.id} label={b.label} />
          ))}
        </SegmentedTabs>
      }
    >
      <Box sx={{ p: 2.5, overflowX: "auto" }}>
        <svg width={W} height={H} viewBox={`0 0 ${W} ${H}`} style={{ maxWidth: "100%", height: "auto", display: "block" }}>
          {/* agent → hub edges */}
          {placed.map((b) => (
            <path
              key={`e-${b.id}`}
              d={`M 150 ${agentY} C 200 ${agentY}, ${colX - 60} ${b.hubY}, ${colX - 10} ${b.hubY}`}
              fill="none"
              stroke={alpha(b.color, 0.5)}
              strokeWidth={1.5}
            />
          ))}

          {/* the agent */}
          <rect x={16} y={agentY - 21} width={134} height={42} rx={8}
            fill={alpha(theme.palette.primary.main, theme.palette.mode === "dark" ? 0.16 : 0.08)}
            stroke={theme.palette.primary.main} strokeWidth={1} />
          <text x={83} y={agentY - 3} textAnchor="middle" fill={strong} fontSize={12} fontWeight={bold}>
            {env.name.length > 18 ? `${env.name.slice(0, 17)}…` : env.name}
          </text>
          <text x={83} y={agentY + 12} textAnchor="middle" fill={dim} fontSize={10}>
            read from your agent
          </text>

          {placed.map((b) => {
            const leaves = b.items.slice(0, cap);
            const hidden = b.items.slice(cap);
            return (
              <g key={b.id}>
                {/* hub */}
                <rect x={colX - 10} y={b.top} width={130} height={headH - 6} rx={6}
                  fill={alpha(b.color, theme.palette.mode === "dark" ? 0.16 : 0.09)}
                  stroke={alpha(b.color, 0.45)} strokeWidth={1} />
                <text x={colX + 2} y={b.top + 18} fill={b.color} fontSize={11} fontWeight={bold}>
                  {b.label}
                </text>
                <text x={colX + 112} y={b.top + 18} textAnchor="end" fill={b.color} fontSize={11} fontWeight={bold}>
                  {b.items.length}
                </text>

                {/* empty branch — an honest placeholder drawn like a leaf (edge
                    + faint dot) so it reads as connected to the hub, not floating */}
                {b.items.length === 0 && (
                  <>
                    <path
                      d={`M ${colX + 120} ${b.hubY} C ${colX + 160} ${b.hubY}, ${leafX - 30} ${b.top + headH + 8}, ${leafX - 6} ${b.top + headH + 8}`}
                      fill="none" stroke={line} strokeWidth={1}
                    />
                    <circle cx={leafX - 3} cy={b.top + headH + 8} r={2.5} fill={alpha(faint, 0.8)} />
                    <text x={leafX + 8} y={b.top + headH + 12} fill={faint} fontSize={11} fontStyle="italic">
                      none yet
                    </text>
                  </>
                )}

                {/* hub → leaf edges + leaves */}
                {leaves.map((item, j) => {
                  const y = b.top + headH + j * rowH + 8;
                  return (
                    <g key={item}>
                      <path
                        d={`M ${colX + 120} ${b.hubY} C ${colX + 160} ${b.hubY}, ${leafX - 30} ${y}, ${leafX - 6} ${y}`}
                        fill="none" stroke={line} strokeWidth={1}
                      />
                      <circle cx={leafX - 3} cy={y} r={2.5} fill={alpha(b.color, 0.8)} />
                      <text x={leafX + 8} y={y + 4} fill={dim} fontSize={11}
                        fontFamily={b.id === "tools" ? "ui-monospace, Menlo, monospace" : "inherit"}>
                        {item.length > 74 ? `${item.slice(0, 73)}…` : item}
                        {item.length > 74 && <title>{item}</title>}
                      </text>
                    </g>
                  );
                })}

                {/* "+N more": hover lists the rest in a tooltip (product flow).
                    Not clickable — it never moves the user off the current view. */}
                {hidden.length > 0 && (
                  <CustomTooltip
                    show
                    arrow
                    size="small"
                    placement="right"
                    title={
                      <Box sx={{ maxWidth: 320, maxHeight: 280, overflowY: "auto" }}>
                        {hidden.map((item) => (
                          <Stack key={item} direction="row" spacing={0.75} sx={{ py: 0.125 }}>
                            <Box
                              component="span"
                              sx={{ color: b.color, flexShrink: 0, lineHeight: 1.55 }}
                            >
                              •
                            </Box>
                            <Typography
                              sx={{
                                typography: "s3",
                                fontFamily: b.id === "tools" ? "ui-monospace, Menlo, monospace" : "inherit",
                              }}
                            >
                              {item}
                            </Typography>
                          </Stack>
                        ))}
                      </Box>
                    }
                  >
                    <text
                      x={leafX + 8}
                      y={b.top + headH + leaves.length * rowH + 12}
                      fill={b.color}
                      fontSize={11}
                      fontWeight={bold}
                      style={{ cursor: "default" }}
                    >
                      + {hidden.length} more
                    </text>
                  </CustomTooltip>
                )}
              </g>
            );
          })}
        </svg>
      </Box>

      <Stack
        direction="row" spacing={1.25} alignItems="center"
        sx={{ px: 2.5, py: 1.75, borderTop: "1px solid", borderColor: "divider" }}
      >
        <Iconify icon="solar:info-circle-linear" width={15} sx={{ color: "text.subtitle", flexShrink: 0 }} />
        <Typography sx={{ typography: "s2", color: "text.secondary" }}>
          Tools and guardrails are read from the source. Flows are derived from them. Personas
          are the callers the agent serves.
        </Typography>
      </Stack>
    </SectionCard>
  );
}

CapabilityGraph.propTypes = {
  env: ENV_SHAPE.isRequired,
  envState: ENV_STATE_SHAPE,
  // Real §6 branch data for a backed env: { tools[], flows[], personas[],
  // guardrails[] }. Omitted for a non-backed env (uses the fixture derivation).
  data: PropTypes.shape({
    tools: PropTypes.arrayOf(PropTypes.string),
    flows: PropTypes.arrayOf(PropTypes.string),
    personas: PropTypes.arrayOf(PropTypes.string),
    guardrails: PropTypes.arrayOf(PropTypes.string),
  }),
};
