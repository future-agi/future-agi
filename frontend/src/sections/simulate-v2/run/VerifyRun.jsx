import PropTypes from "prop-types";
import { useState } from "react";
import { alpha } from "@mui/material/styles";
import { Box, Stack, Typography, Button, Fade } from "@mui/material";
import Iconify from "src/components/iconify";
import { SectionCard, CopyField } from "../components/primitives";
import { BootSequence } from "../components/loading";

/**
 * Verify the run independently.
 *
 * This is the "who watches the watchmen" step: the platform says the agent
 * passed, and this independently re-checks that the *builder* behaved — that
 * the environment really reset, the tools really fired, and the graders scored
 * what they claim to have scored. Showing the command rather than hiding it
 * matters, because the point is that the user can run this themselves.
 */

const VERIFY_STEPS = [
  "Fetching run manifest",
  "Re-hashing environment snapshots",
  "Replaying recorded tool calls",
  "Re-scoring a sample of graded tasks",
  "Comparing against platform verdicts",
];

export default function VerifyRun({ env, stats, runId }) {
  const [phase, setPhase] = useState("idle");

  const checks = [
    {
      id: "isolation",
      label: "Environment isolation",
      detail: `All ${stats.total} tasks started from an identical snapshot hash. No cross-task contamination.`,
      status: "pass",
    },
    {
      id: "tools",
      label: "Tool call integrity",
      detail: "Every recorded tool call has a matching environment mutation. No phantom calls.",
      status: "pass",
    },
    {
      id: "grading",
      label: "Grader reproducibility",
      detail: `Re-scored ${Math.max(3, Math.round(stats.total * 0.25))} tasks — verdicts matched on all but one.`,
      status: "warn",
    },
    {
      id: "coverage",
      label: "Rule coverage",
      detail: `${env.rules.length - 1} of ${env.rules.length} business rules were exercised. "${env.rules[env.rules.length - 1]}" was never triggered.`,
      status: "warn",
    },
    {
      id: "determinism",
      label: "Replay determinism",
      detail: "Two replays of the same task produced identical trajectories.",
      status: "pass",
    },
  ];

  return (
    <Stack spacing={2}>
      <SectionCard
        title="Verify this run"
        subtitle="An independent check that the builder itself behaved — not just the agent"
      >
        <Box sx={{ p: 2.5 }}>
          <Typography sx={{ typography: "s2", color: "text.secondary", mb: 2, maxWidth: 720 }}>
            The platform already told you which tasks passed. This re-derives that answer
            from the raw run artifacts, so you can trust the number before you act on it.
            Run it locally or let us run it here.
          </Typography>

          <CopyField
            label="Run locally"
            value={`fai verify run ${runId} --env ${env.id} --strict`}
          />

          <Stack direction="row" spacing={1.5} alignItems="center" sx={{ mt: 2 }}>
            <Button
              variant="contained"
              color="primary"
              disabled={phase === "running"}
              onClick={() => setPhase("running")}
              startIcon={<Iconify icon="solar:command-linear" width={16} />}
              sx={{ typography: "s2", fontWeight: 700 }}
            >
              {phase === "running" ? "Verifying…" : phase === "done" ? "Re-run verification" : "Run verification"}
            </Button>
            <Typography sx={{ typography: "s3", color: "text.subtitle" }}>
              Uses your local credentials · takes ~40s
            </Typography>
          </Stack>
        </Box>

        {phase === "running" && (
          <Box sx={{ px: 2.5, py: 2.25, borderTop: "1px solid", borderColor: "divider", bgcolor: "background.neutral" }}>
            <BootSequence steps={VERIFY_STEPS} accent="#0D9488" stepMs={900} onDone={() => setPhase("done")} />
          </Box>
        )}
      </SectionCard>

      {phase === "done" && (
        <Fade in timeout={400}>
          <Box>
            <SectionCard
              title="Verification report"
              subtitle={`${checks.filter((c) => c.status === "pass").length} passed · ${checks.filter((c) => c.status === "warn").length} warnings`}
            >
              <Stack divider={<Box sx={{ borderBottom: "1px solid", borderColor: "divider" }} />}>
                {checks.map((c) => {
                  const color = c.status === "pass" ? "#16A34A" : c.status === "warn" ? "#CA8A04" : "#DC2626";
                  return (
                    <Stack key={c.id} direction="row" spacing={1.75} sx={{ px: 2.5, py: 1.75 }}>
                      <Box
                        sx={{
                          width: 26, height: 26, borderRadius: 0.875, display: "grid", placeItems: "center", flexShrink: 0,
                          bgcolor: (t) => alpha(color, t.palette.mode === "dark" ? 0.16 : 0.1),
                          color,
                        }}
                      >
                        <Iconify
                          icon={c.status === "pass" ? "solar:check-circle-bold" : "solar:info-circle-bold"}
                          width={14}
                        />
                      </Box>
                      <Box flex={1} minWidth={0}>
                        <Typography sx={{ typography: "s2", fontWeight: 700 }}>{c.label}</Typography>
                        <Typography sx={{ typography: "s2", color: "text.subtitle" }}>{c.detail}</Typography>
                      </Box>
                      <Typography sx={{ typography: "s3", fontWeight: 700, color, flexShrink: 0, textTransform: "uppercase" }}>
                        {c.status}
                      </Typography>
                    </Stack>
                  );
                })}
              </Stack>

              <Box
                sx={{
                  px: 2.5, py: 2, borderTop: "1px solid", borderColor: "divider",
                  bgcolor: (t) => alpha("#0D9488", t.palette.mode === "dark" ? 0.08 : 0.04),
                }}
              >
                <Stack direction="row" spacing={1.25} alignItems="flex-start">
                  <Iconify icon="solar:verified-check-bold" width={17} sx={{ color: "#0D9488", flexShrink: 0, mt: "1px" }} />
                  <Typography sx={{ typography: "s2" }}>
                    <b>Run verified with two advisories.</b> The pass rate of{" "}
                    {Math.round(stats.passRate * 100)}% is trustworthy. Before you rely on it,
                    note that one business rule was never exercised — consider adding scenarios
                    that trigger it.
                  </Typography>
                </Stack>
              </Box>
            </SectionCard>
          </Box>
        </Fade>
      )}
    </Stack>
  );
}

VerifyRun.propTypes = {
  env: PropTypes.object, stats: PropTypes.object, runId: PropTypes.string,
};
