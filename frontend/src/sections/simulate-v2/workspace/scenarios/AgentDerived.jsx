import PropTypes from "prop-types";
import { useMemo, useState } from "react";
import { alpha } from "@mui/material/styles";
import { Box, Stack, Typography, Button, Fade, Chip } from "@mui/material";
import Iconify from "src/components/iconify";
import { generatedPool, derivedFindings } from "../../_mock/scenarios";
import { getAgentType } from "../../_mock/agentTypes";
import { SectionCard, cardGrid } from "../../components/primitives";
import { BootSequence } from "../../components/loading";
import { ScenarioRow } from "../ScenariosStep";

const ANALYSIS_STEPS = [
  "Reading the agent's system prompt",
  "Enumerating declared tools",
  "Mapping tools onto environment rules",
  "Looking for uncovered failure modes",
  "Drafting targeted scenarios",
];

/**
 * Scenarios derived from the agent itself.
 *
 * The value here is that we can see both halves — the agent's prompt and tools,
 * and the environment's rules — so we can write scenarios aimed at the gap
 * between them. Showing *what we found* before showing the scenarios is what
 * makes the output trustworthy rather than a black box.
 */
export default function AgentDerived({ env, envState, onAdd }) {
  const [phase, setPhase] = useState("idle");
  const [selected, setSelected] = useState({});
  const type = getAgentType(envState.agent?.typeId);

  const rows = useMemo(
    () => generatedPool(env).slice(0, 10).map((r, i) => ({ ...r, id: `agt-${i}` })),
    [env],
  );

  const findings = useMemo(() => derivedFindings(env), [env]);

  const chosen = rows.filter((r) => selected[r.id]);

  return (
    <SectionCard
      title="Generate from your agent"
      subtitle={`We compare ${type?.label || "your agent"} against ${env.name}'s rules and write scenarios for the gaps`}
      action={
        phase === "done" && (
          <Button
            variant="contained"
            color="primary"
            size="small"
            disabled={chosen.length === 0}
            onClick={() => onAdd(chosen)}
            sx={{ typography: "s2", fontWeight: 700 }}
          >
            Add {chosen.length || ""} {chosen.length === 1 ? "scenario" : "scenarios"}
          </Button>
        )
      }
    >
      <Box sx={{ p: 2.5 }}>

      {phase === "idle" && (
        <Box>
          <Stack spacing={2.5} alignItems="center" sx={{ py: 6, px: 3, textAlign: "center" }}>
            <Box
              sx={{
                width: 56, height: 56, borderRadius: 1.5, display: "grid", placeItems: "center",
                bgcolor: (t) => alpha("#EA580C", t.palette.mode === "dark" ? 0.16 : 0.1),
                color: "#EA580C",
              }}
            >
              <Iconify icon="solar:cpu-bolt-linear" width={28} />
            </Box>
            <Box>
              <Typography sx={{ typography: "s1", fontWeight: 700 }}>
                Analyse the connected agent
              </Typography>
              <Typography sx={{ typography: "s2", color: "text.subtitle", maxWidth: 480, mt: 0.5 }}>
                We read its prompt and tool definitions, hold them against this environment&apos;s
                business rules, and write scenarios aimed at whatever is missing.
              </Typography>
            </Box>
            <Button
              variant="contained"
              color="primary"
              onClick={() => setPhase("analysing")}
              startIcon={<Iconify icon="solar:magic-stick-3-linear" width={17} />}
              sx={{ typography: "s2", fontWeight: 700 }}
            >
              Analyse agent
            </Button>
          </Stack>
        </Box>
      )}

      {phase === "analysing" && (
        <Box>
          <Box sx={{ py: 5, px: 3, display: "grid", placeItems: "center" }}>
            <Box sx={{ width: "100%", maxWidth: 320 }}>
              <Typography sx={{ typography: "s1", fontWeight: 700, mb: 2, textAlign: "center" }}>
                Analysing your agent
              </Typography>
              <BootSequence
                steps={ANALYSIS_STEPS}
                accent="#EA580C"
                stepMs={950}
                onDone={() => setPhase("done")}
              />
            </Box>
          </Box>
        </Box>
      )}

      {phase === "done" && (
        <Fade in timeout={400}>
          <Box>
            <Box sx={{ ...cardGrid(260), mb: 2 }}>
              {findings.map((f) => (
                  <Box
                    key={f.title}
                    sx={{
                      height: "100%", p: 2, borderRadius: 1.5,
                      border: "1px solid", borderColor: "divider", bgcolor: "background.paper",
                    }}
                  >
                    <Stack direction="row" alignItems="center" justifyContent="space-between" sx={{ mb: 1.25 }}>
                      <Box
                        sx={{
                          width: 30, height: 30, borderRadius: 0.875, display: "grid", placeItems: "center",
                          bgcolor: (t) => alpha(f.color, t.palette.mode === "dark" ? 0.16 : 0.1),
                          color: f.color,
                        }}
                      >
                        <Iconify icon={f.icon} width={16} />
                      </Box>
                      <Chip
                        size="small"
                        label={`${f.generated} scenarios`}
                        sx={{
                          height: 19, borderRadius: 0.5, color: "text.secondary",
                          border: "1px solid", borderColor: "divider", bgcolor: "transparent",
                          "& .MuiChip-label": { px: 0.75, typography: "s3", fontWeight: 600 },
                        }}
                      />
                    </Stack>
                    <Typography sx={{ typography: "s2", fontWeight: 700, mb: 0.5 }}>{f.title}</Typography>
                    <Typography sx={{ typography: "s3", color: "text.subtitle" }}>{f.body}</Typography>
                  </Box>
              ))}
            </Box>

            <SectionCard
              title="Suggested scenarios"
              subtitle="Each one targets a gap we found above"
              action={
                <Button
                  size="small"
                  onClick={() =>
                    setSelected(
                      Object.fromEntries(rows.map((r) => [r.id, chosen.length !== rows.length])),
                    )
                  }
                  sx={{ typography: "s2", color: "primary.main", fontWeight: 600 }}
                >
                  {chosen.length === rows.length ? "Clear all" : "Select all"}
                </Button>
              }
            >
              <Stack divider={<Box sx={{ borderBottom: "1px solid", borderColor: "divider" }} />}>
                {rows.map((r, i) => (
                  <ScenarioRow
                    key={r.id}
                    row={r}
                    index={i}
                    selectable
                    checked={!!selected[r.id]}
                    onToggle={() => setSelected((s) => ({ ...s, [r.id]: !s[r.id] }))}
                  />
                ))}
              </Stack>
            </SectionCard>
          </Box>
        </Fade>
      )}
      </Box>
    </SectionCard>
  );
}

AgentDerived.propTypes = {
  env: PropTypes.object,
  envState: PropTypes.object,
  onAdd: PropTypes.func,
};
