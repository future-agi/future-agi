import PropTypes from "prop-types";
import { useMemo, useState } from "react";
import { alpha } from "@mui/material/styles";
import { Box, Stack, Typography, Button, Slider, Tooltip, IconButton } from "@mui/material";
import Iconify from "src/components/iconify";
import { MODALITY_FOR } from "../_mock/fidelity";
import { runSummaries } from "../_mock/comparison";
import {
  observationFor, ACTION_SPACE, rewardTable, rollouts, rolloutSample, rlSnippet,
} from "../_mock/rl";
import { mcpConfig, mcpPythonSnippet } from "../_mock/agentTypes";
import { Tab } from "@mui/material";
import { CustomTabs } from "src/components/tabs/tabs";
import { SectionCard } from "../components/primitives";

/**
 * The environment, seen as an RL environment.
 *
 * Not a second implementation: the world already resets between tasks, already
 * answers tools truthfully and already settles checks in code. This names those
 * as reset, step and reward so the environment that evaluated an agent can
 * later train one without being rebuilt.
 *
 * Training is deliberately absent. This emits reward and exports rollouts, and
 * stops — hosting the loop is a separate thing.
 */
/** How you reach this environment from your own tools, whatever your agent uses. */
const ACCESS = [
  { id: "gym", label: "Gym loop", snippet: (env) => rlSnippet(env) },
  { id: "mcp", label: "MCP client", snippet: (env) => mcpConfig(env) },
  { id: "python", label: "Python", snippet: (env) => mcpPythonSnippet(env) },
];

export default function RlPanel({ env, envState, patch }) {
  const [access, setAccess] = useState("gym");
  const modality = MODALITY_FOR[env.surface] || "chat";
  const obs = observationFor(modality);
  const weights = envState.reward || rewardTable();
  /* The real ones this environment has produced, not a brochure figure. */
  const summaries = useMemo(() => runSummaries(env, envState), [env, envState]);
  const roll = rollouts(envState, summaries);
  const [copied, setCopied] = useState(false);

  const setWeight = (id, value) =>
    patch({ reward: weights.map((w) => (w.id === id ? { ...w, value } : w)) });

  const copy = () => {
    navigator.clipboard?.writeText(ACCESS.find((a) => a.id === access).snippet(env));
    setCopied(true);
    setTimeout(() => setCopied(false), 1600);
  };

  return (
    <Box sx={{ p: 2 }}>
      <Box sx={{ mb: 2 }}>
        <Typography sx={{ typography: "m2", fontWeight: 600 }}>Interface</Typography>
        <Typography sx={{ typography: "s2", color: "text.secondary", maxWidth: 820 }}>
          This environment already resets between tasks, answers tools truthfully and settles checks
          in code — which is <Box component="span" sx={{ fontFamily: "ui-monospace, Menlo, monospace" }}>reset</Box>,{" "}
          <Box component="span" sx={{ fontFamily: "ui-monospace, Menlo, monospace" }}>step</Box> and{" "}
          <Box component="span" sx={{ fontFamily: "ui-monospace, Menlo, monospace" }}>reward</Box> under other names.
          Exposing them means the environment that evaluated an agent can later train one, without a rewrite.
        </Typography>
      </Box>

      <Stack direction={{ xs: "column", lg: "row" }} spacing={2} alignItems="flex-start" sx={{ mb: 2 }}>
        <Box sx={{ flex: 1, minWidth: 0, width: "100%" }}>
          <SectionCard
          title="Use this environment directly"
          subtitle="From your own code or any MCP-capable tool — independent of how your agent connects for runs"
        >
            {/*
              A full-width tab row rather than three buttons crammed into the
              card header: the card is narrow, and "Gym loop" / "MCP client"
              were wrapping onto two lines beside a two-line title.
            */}
            <CustomTabs
              value={access}
              onChange={(_, v) => setAccess(v)}
              sx={{
                px: 2.5, minHeight: 38,
                borderBottom: "1px solid", borderColor: "divider",
                "& .MuiTab-root": { typography: "s2", minHeight: 38, px: 0, mr: 3, minWidth: 0 },
              }}
            >
              {ACCESS.map((a) => (
                <Tab key={a.id} value={a.id} label={a.label} sx={{ minHeight: 38 }} />
              ))}
            </CustomTabs>

            <Box sx={{ position: "relative" }}>
              <Box
                component="pre"
                sx={{
                  m: 0, px: 2.5, py: 2, overflowX: "auto",
                  typography: "s2", fontFamily: "ui-monospace, SFMono-Regular, Menlo, monospace",
                  color: "text.secondary", lineHeight: 1.7,
                }}
              >
                {ACCESS.find((a) => a.id === access).snippet(env)}
              </Box>
              <Tooltip arrow title={copied ? "Copied" : "Copy"}>
                <IconButton size="small" onClick={copy} sx={{ position: "absolute", top: 8, right: 8 }}>
                  <Iconify
                    icon={copied ? "solar:check-circle-bold" : "solar:copy-linear"}
                    width={15}
                    sx={{ color: copied ? "primary.main" : "text.subtitle" }}
                  />
                </IconButton>
              </Tooltip>
            </Box>
          </SectionCard>
        </Box>

        <Box sx={{ flex: 1, minWidth: 0, width: "100%" }}>
          <SectionCard title="Observation" subtitle={`What step returns for a ${modality} environment`}>
            <Stack divider={<Box sx={{ borderBottom: "1px solid", borderColor: "divider" }} />}>
              {obs.map((o) => (
                <Stack key={o.field} direction="row" alignItems="flex-start" spacing={2} sx={{ px: 2.5, py: 1.25 }}>
                  <Typography sx={{ width: 120, flexShrink: 0, typography: "s2", fontWeight: 600, fontFamily: "ui-monospace, Menlo, monospace" }}>
                    {o.field}
                  </Typography>
                  <Typography sx={{ width: 130, flexShrink: 0, typography: "s3", color: "primary.main", fontFamily: "ui-monospace, Menlo, monospace" }}>
                    {o.type}
                  </Typography>
                  <Typography sx={{ flex: 1, minWidth: 0, typography: "s3", color: "text.subtitle" }}>{o.note}</Typography>
                </Stack>
              ))}
            </Stack>
          </SectionCard>
        </Box>
      </Stack>

      <SectionCard title="Action space" subtitle="What a policy may do each step" sx={{ mb: 2 }}>
        <Stack divider={<Box sx={{ borderBottom: "1px solid", borderColor: "divider" }} />}>
          {ACTION_SPACE.map((a) => (
            <Stack key={a.name} direction="row" alignItems="flex-start" spacing={2} sx={{ px: 2.5, py: 1.25 }}>
              <Typography sx={{ width: 100, flexShrink: 0, typography: "s2", fontWeight: 600, fontFamily: "ui-monospace, Menlo, monospace" }}>
                {a.name}
              </Typography>
              <Typography sx={{ width: 210, flexShrink: 0, typography: "s3", color: "text.subtitle", fontFamily: "ui-monospace, Menlo, monospace" }}>
                {a.args}
              </Typography>
              <Typography sx={{ flex: 1, minWidth: 0, typography: "s3", color: "text.subtitle" }}>{a.note}</Typography>
            </Stack>
          ))}
        </Stack>
      </SectionCard>

      <SectionCard
        title="Reward shaping"
        subtitle="The checks that already grade a run, given weights — so the thing optimised is the thing tested"
        sx={{ mb: 2 }}
      >
        <Stack divider={<Box sx={{ borderBottom: "1px solid", borderColor: "divider" }} />}>
          {weights.map((w) => {
            const penalty = w.value < 0;
            return (
              <Stack key={w.id} direction="row" alignItems="center" spacing={2} sx={{ px: 2.5, py: 1.5 }}>
                <Box sx={{ width: 210, flexShrink: 0, minWidth: 0 }}>
                  <Stack direction="row" alignItems="center" spacing={0.75}>
                    <Typography noWrap sx={{ typography: "s2", fontWeight: 600 }}>{w.label}</Typography>
                    <Typography
                      sx={{
                        px: 0.625, borderRadius: 0.5, flexShrink: 0,
                        typography: "s3", fontWeight: 700, color: "text.subtitle",
                        border: "1px solid", borderColor: "divider",
                      }}
                    >
                      {w.kind}
                    </Typography>
                  </Stack>
                  <Typography noWrap sx={{ typography: "s3", color: "text.subtitle" }}>{w.detail}</Typography>
                </Box>

                <Box sx={{ flex: 1, minWidth: 100, display: { xs: "none", md: "block" } }}>
                  <Slider
                    size="small"
                    value={w.value}
                    min={-1}
                    max={1}
                    step={0.01}
                    onChange={(_, v) => setWeight(w.id, v)}
                    sx={{
                      py: 1,
                      color: penalty ? "#DC2626" : "primary.main",
                      "& .MuiSlider-thumb": { width: 11, height: 11 },
                    }}
                  />
                </Box>

                <Typography
                  sx={{
                    width: 56, textAlign: "right", flexShrink: 0,
                    typography: "s2", fontWeight: 700, fontVariantNumeric: "tabular-nums",
                    color: penalty ? "#DC2626" : "text.primary",
                  }}
                >
                  {w.value > 0 ? "+" : ""}{w.value.toFixed(2)}
                </Typography>
              </Stack>
            );
          })}
        </Stack>
      </SectionCard>

      <SectionCard
        title="Rollouts"
        subtitle="Every run already recorded, as episodes a trainer can read"
        action={
          <Button
            variant="contained"
            color="primary"
            size="small"
            startIcon={<Iconify icon="solar:download-minimalistic-linear" width={15} />}
            sx={{ typography: "s2", fontWeight: 700 }}
          >
            Export rollouts
          </Button>
        }
      >
        <Stack
          direction="row"
          spacing={3}
          sx={{ px: 2.5, py: 2, flexWrap: "wrap", rowGap: 1.5 }}
        >
          <Stat label="Episodes" value={roll.episodes.toLocaleString()} note={`from ${roll.fromRuns} runs`} />
          {/* Derived from real runs now, so it has to survive an environment
              that has none — a panel that crashes on an empty history is worse
              than one that admits it is empty. */}
          <Stat
            label="Mean return"
            value={roll.meanReturn == null ? "—" : roll.meanReturn.toFixed(2)}
            note={roll.meanReturn == null ? "no measured episodes yet" : "per episode"}
          />
          <Stat label="Mean length" value={(roll.meanLength || 0).toFixed(1)} note="steps" />
          <Stat label="Success rate" value={`${Math.round(roll.success * 100)}%`} note="terminal check passed" />
          <Stat label="Format" value={roll.format} note={roll.bytes} />
        </Stack>

        <Box
          component="pre"
          sx={{
            m: 0, px: 2.5, py: 2, overflowX: "auto",
            borderTop: "1px solid", borderColor: "divider", bgcolor: "background.neutral",
            typography: "s3", fontFamily: "ui-monospace, SFMono-Regular, Menlo, monospace",
            color: "text.subtitle", lineHeight: 1.7,
          }}
        >
          {rolloutSample()}
        </Box>

        <Stack
          direction="row"
          spacing={1.25}
          sx={{
            px: 2.5, py: 1.75, borderTop: "1px solid", borderColor: "divider",
            bgcolor: (t) => alpha("#CA8A04", t.palette.mode === "dark" ? 0.1 : 0.05),
          }}
        >
          <Iconify icon="solar:info-circle-linear" width={16} sx={{ color: "#CA8A04", flexShrink: 0, mt: "1px" }} />
          <Typography sx={{ typography: "s2", color: "text.secondary" }}>
            Training runs elsewhere. This makes the environment RL-ready — reward emission and rollout
            export — and stops there; hosting the fine-tuning loop is a separate piece of work.
          </Typography>
        </Stack>
      </SectionCard>
    </Box>
  );
}

RlPanel.propTypes = {
  env: PropTypes.object.isRequired,
  envState: PropTypes.object.isRequired,
  patch: PropTypes.func.isRequired,
};

function Stat({ label, value, note }) {
  return (
    <Box sx={{ minWidth: 0 }}>
      <Typography noWrap sx={{ typography: "s3", color: "text.subtitle" }}>{label}</Typography>
      <Typography noWrap sx={{ typography: "s1", fontWeight: 700, fontVariantNumeric: "tabular-nums" }}>{value}</Typography>
      <Typography noWrap sx={{ typography: "s3", color: "text.subtitle" }}>{note}</Typography>
    </Box>
  );
}
Stat.propTypes = { label: PropTypes.string, value: PropTypes.node, note: PropTypes.string };
