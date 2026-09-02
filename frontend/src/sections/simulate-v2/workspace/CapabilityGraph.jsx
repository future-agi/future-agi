import PropTypes from "prop-types";
import { useState } from "react";
import { useTheme, alpha } from "@mui/material/styles";
import { Box, Stack, Typography, Button, Tab } from "@mui/material";
import { SegmentedTabs } from "src/components/tabs/tabs";
import Iconify from "src/components/iconify";
import { SectionCard } from "../components/primitives";
import { contractFor } from "../_mock/contract";
import { ACTOR_LIBRARY, castFor as actorCastFor } from "../_mock/actors";

/**
 * The capability graph.
 *
 * The same facts the contract lists, drawn as what they actually are: one
 * agent, four kinds of thing hanging off it. A list of twelve tools and five
 * rules reads as two unrelated inventories; the graph makes it one object, and
 * makes the shape of an environment legible at a glance — tool-heavy, rule-
 * heavy, thin on the people.
 *
 * Plain SVG on purpose. MUI's Box routes width/height through the style system
 * and mangles SVG geometry.
 */
const BRANCHES = [
  { id: "tools", label: "Tools", color: "#EA580C", icon: "solar:settings-minimalistic-linear" },
  { id: "flows", label: "Flows", color: "#2563EB", icon: "solar:route-linear" },
  { id: "people", label: "Personas & actors", color: "#7857FC", icon: "solar:users-group-rounded-linear" },
  { id: "guardrails", label: "Guardrails", color: "#DC2626", icon: "solar:shield-check-linear" },
];

const MAX_LEAVES = 5;

export default function CapabilityGraph({ env, envState, onGo }) {
  const theme = useTheme();
  const [focus, setFocus] = useState(null);
  const contract = contractFor(env);

  const actorIds = envState?.actors || actorCastFor(env);
  /*
    Personas are derived from scenarios now — dedupe by slug so a graph
    that used to show a placeholder library shows the archetypes
    actually in play.
  */
  const personaNames = (() => {
    const seen = new Map();
    (envState?.scenarios || []).forEach((s) => {
      if (!s.persona) return;
      const key = s.persona.slug || s.persona.name;
      if (!seen.has(key)) seen.set(key, s.persona.name);
    });
    return [...seen.values()];
  })();

  const data = {
    tools: (env.tools || []).map((t) => t.name),
    flows: (contract.useCases || []).map((u) => u.replace(/ using .*/, "").replace(/^Refuse the request that would break: /, "Refuse: ")),
    people: [
      ...personaNames,
      ...ACTOR_LIBRARY.filter((a) => actorIds.includes(a.id)).map((a) => a.name),
    ],
    guardrails: contract.hardRules || [],
  };

  const branches = BRANCHES.map((b) => ({ ...b, items: data[b.id] || [] }));
  const shown = focus ? branches.filter((b) => b.id === focus) : branches;

  /* ── geometry ── */
  const rowH = 22;
  const headH = 34;
  const gap = 26;
  const colX = 250;
  const leafX = 400;
  const heights = shown.map((b) => headH + Math.min(b.items.length, MAX_LEAVES) * rowH + (b.items.length > MAX_LEAVES ? rowH : 0));
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
  const strong = theme.palette.text.primary;

  /*
    A filter with an "all" state is a tab strip, so it is drawn as one, and it
    stays where it was — the card header's action slot. That slot gives an
    underline indicator no baseline to sit on, so this uses the segmented pill
    strip (the eval detail page's control, now shared), which reads as a
    control on its own. "All" as the first tab is also what retires the
    separate "Show all" button that existed only to undo the filter.
  */
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
          <text x={83} y={agentY - 3} textAnchor="middle" fill={strong} fontSize={12} fontWeight={700}>
            {env.name.length > 18 ? `${env.name.slice(0, 17)}…` : env.name}
          </text>
          <text x={83} y={agentY + 12} textAnchor="middle" fill={dim} fontSize={10}>
            read from your agent
          </text>

          {placed.map((b) => {
            const leaves = b.items.slice(0, MAX_LEAVES);
            return (
              <g key={b.id}>
                {/* hub */}
                <rect x={colX - 10} y={b.top} width={130} height={headH - 6} rx={6}
                  fill={alpha(b.color, theme.palette.mode === "dark" ? 0.16 : 0.09)}
                  stroke={alpha(b.color, 0.45)} strokeWidth={1} />
                <text x={colX + 2} y={b.top + 18} fill={b.color} fontSize={11} fontWeight={700}>
                  {b.label}
                </text>
                <text x={colX + 112} y={b.top + 18} textAnchor="end" fill={b.color} fontSize={11} fontWeight={700}>
                  {b.items.length}
                </text>

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
                      </text>
                    </g>
                  );
                })}

                {b.items.length > MAX_LEAVES && (
                  <text x={leafX + 8} y={b.top + headH + MAX_LEAVES * rowH + 12} fill={theme.palette.text.disabled} fontSize={11}>
                    + {b.items.length - MAX_LEAVES} more
                  </text>
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
        <Typography sx={{ typography: "s2", color: "text.secondary", flex: 1 }}>
          Tools and guardrails are read from the source. Flows are derived from them. Personas
          and actors are what you injected — the only part of the graph that is authored.
        </Typography>
        <Button size="small" onClick={() => onGo?.("contract")} sx={{ typography: "s2", fontWeight: 700, color: "primary.main", flexShrink: 0 }}>
          Contract
        </Button>
      </Stack>
    </SectionCard>
  );
}

CapabilityGraph.propTypes = {
  env: PropTypes.object.isRequired,
  envState: PropTypes.object,
  onGo: PropTypes.func,
};
