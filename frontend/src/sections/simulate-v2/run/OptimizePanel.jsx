import PropTypes from "prop-types";
import { useState } from "react";
import { alpha } from "@mui/material/styles";
import { Box, Stack, Typography, Button, Fade, Chip } from "@mui/material";
import Iconify from "src/components/iconify";
import { SectionCard, EmptyState } from "../components/primitives";
import { BootSequence } from "../components/loading";

/**
 * Close the loop: turn failures into a concrete fix.
 *
 * A test platform that only tells you the score stops one step short. Every
 * proposal here is tied to the specific failing tasks that motivated it and
 * carries a projected pass rate, so the user can judge the fix before applying
 * it — and re-run to check the projection was honest.
 */

const OPTIMIZE_STEPS = [
  "Clustering failures by root cause",
  "Diffing agent behaviour against environment rules",
  "Drafting prompt and tool changes",
  "Simulating each candidate against failed tasks",
  "Ranking by projected pass rate",
];

export default function OptimizePanel({ env, tasks, stats }) {
  const [phase, setPhase] = useState("idle");
  const [applied, setApplied] = useState({});

  const failed = tasks.filter((t) => t.status === "failed");

  const proposals = [
    {
      id: "p1",
      kind: "System prompt",
      title: "State the refund approval ceiling explicitly",
      why: "4 failures came from refunds issued above the environment's $200 supervisor threshold.",
      diff: [
        { type: "add", text: "You may issue refunds up to $200 without approval." },
        { type: "add", text: "For any amount above $200, call escalate_to_human and explain why." },
      ],
      lift: 18,
      affects: 4,
    },
    {
      id: "p2",
      kind: "System prompt",
      title: "Name the two identity factors",
      why: "3 failures disclosed order details after verifying only the caller's phone number.",
      diff: [
        { type: "remove", text: "Verify the customer before sharing details." },
        { type: "add", text: "Verify TWO factors — email on file plus order number or postcode — before sharing any order detail." },
      ],
      lift: 12,
      affects: 3,
    },
    {
      id: "p3",
      kind: "Tool description",
      title: "Clarify when escalate_to_human applies",
      why: "The tool was never called, including on 3 tasks where escalation was the correct outcome.",
      diff: [
        { type: "add", text: "Use when: refund > $200, caller requests a manager, or identity cannot be verified after two attempts." },
      ],
      lift: 9,
      affects: 3,
    },
  ];

  const totalLift = proposals
    .filter((p) => applied[p.id])
    .reduce((a, p) => a + p.lift, 0);
  const projected = Math.min(100, Math.round(stats.passRate * 100) + totalLift);

  if (failed.length === 0) {
    return (
      <SectionCard>
        <EmptyState
          icon="solar:cup-star-linear"
          title="Nothing to optimize"
          body="Every task passed. Add harder scenarios if you want to find the edges."
        />
      </SectionCard>
    );
  }

  return (
    <Stack spacing={2}>
      <SectionCard
        title="Optimize your agent"
        subtitle={`${failed.length} failing ${failed.length === 1 ? "task" : "tasks"} analysed against ${env.name}'s rules`}
      >
        {phase === "idle" && (
          <Stack spacing={2.5} alignItems="center" sx={{ py: 5, px: 3, textAlign: "center" }}>
            <Box
              sx={{
                width: 52, height: 52, borderRadius: 1.5, display: "grid", placeItems: "center",
                bgcolor: (t) => alpha("#7857FC", t.palette.mode === "dark" ? 0.16 : 0.1),
                color: "#7857FC",
              }}
            >
              <Iconify icon="solar:magic-stick-3-linear" width={26} />
            </Box>
            <Box>
              <Typography sx={{ typography: "s1", fontWeight: 700 }}>
                Find out why it failed — and what to change
              </Typography>
              <Typography sx={{ typography: "s2", color: "text.subtitle", maxWidth: 500, mt: 0.5 }}>
                We cluster the failures, trace each one back to a rule the agent did not follow,
                and propose concrete prompt or tool changes with a projected pass rate.
              </Typography>
            </Box>
            <Button
              variant="contained"
              color="primary"
              onClick={() => setPhase("running")}
              startIcon={<Iconify icon="solar:magic-stick-3-linear" width={16} />}
              sx={{ typography: "s2", fontWeight: 700 }}
            >
              Analyse failures
            </Button>
          </Stack>
        )}

        {phase === "running" && (
          <Box sx={{ py: 4, px: 3, display: "grid", placeItems: "center" }}>
            <Box sx={{ width: "100%", maxWidth: 330 }}>
              <BootSequence steps={OPTIMIZE_STEPS} accent="#7857FC" stepMs={950} onDone={() => setPhase("done")} />
            </Box>
          </Box>
        )}

        {phase === "done" && (
          <Stack
            direction="row" alignItems="center" spacing={3}
            sx={{ px: 2.5, py: 2, bgcolor: (t) => alpha("#7857FC", t.palette.mode === "dark" ? 0.08 : 0.04) }}
          >
            <Box>
              <Typography sx={{ typography: "s3", color: "text.subtitle" }}>Current pass rate</Typography>
              <Typography sx={{ typography: "m2", fontWeight: 700, fontVariantNumeric: "tabular-nums" }}>
                {Math.round(stats.passRate * 100)}%
              </Typography>
            </Box>
            <Iconify icon="solar:arrow-right-linear" width={20} sx={{ color: "text.subtitle" }} />
            <Box>
              <Typography sx={{ typography: "s3", color: "text.subtitle" }}>
                Projected with {Object.values(applied).filter(Boolean).length} changes
              </Typography>
              <Typography sx={{ typography: "m2", fontWeight: 700, color: "#16A34A", fontVariantNumeric: "tabular-nums" }}>
                {projected}%
              </Typography>
            </Box>
            <Box flex={1} />
            <Button
              variant="contained"
              color="primary"
              disabled={totalLift === 0}
              startIcon={<Iconify icon="solar:play-bold" width={15} />}
              sx={{ typography: "s2", fontWeight: 700 }}
            >
              Apply & re-run
            </Button>
          </Stack>
        )}
      </SectionCard>

      {phase === "done" && (
        <Fade in timeout={400}>
          <Stack spacing={1.5}>
            {proposals.map((p) => (
              <SectionCard key={p.id}>
                <Box sx={{ p: 2.5 }}>
                  <Stack direction="row" alignItems="flex-start" spacing={2}>
                    <Box flex={1} minWidth={0}>
                      <Stack direction="row" alignItems="center" spacing={0.75} sx={{ mb: 0.5 }}>
                        <Chip
                          size="small"
                          label={p.kind}
                          sx={{
                            height: 19, borderRadius: 0.5, color: "text.secondary",
                            border: "1px solid", borderColor: "divider", bgcolor: "transparent",
                            "& .MuiChip-label": { px: 0.75, typography: "s3", fontWeight: 600 },
                          }}
                        />
                        <Typography sx={{ typography: "s3", color: "#16A34A", fontWeight: 700 }}>
                          +{p.lift}% projected
                        </Typography>
                        <Typography sx={{ typography: "s3", color: "text.subtitle" }}>
                          · fixes {p.affects} {p.affects === 1 ? "task" : "tasks"}
                        </Typography>
                      </Stack>
                      <Typography sx={{ typography: "s1", fontWeight: 700 }}>{p.title}</Typography>
                      <Typography sx={{ typography: "s2", color: "text.subtitle", mt: 0.25 }}>{p.why}</Typography>
                    </Box>
                    <Button
                      size="small"
                      variant={applied[p.id] ? "contained" : "outlined"}
                      onClick={() => setApplied((a) => ({ ...a, [p.id]: !a[p.id] }))}
                      startIcon={
                        <Iconify icon={applied[p.id] ? "solar:check-circle-bold" : "solar:add-circle-linear"} width={15} />
                      }
                      sx={{
                        flexShrink: 0, typography: "s2", fontWeight: 700,
                        ...(!applied[p.id] && { color: "text.primary", borderColor: "divider" }),
                      }}
                    >
                      {applied[p.id] ? "Included" : "Include"}
                    </Button>
                  </Stack>

                  {/* the actual change */}
                  <Box
                    sx={{
                      mt: 1.75, borderRadius: 1, overflow: "hidden",
                      border: "1px solid", borderColor: "divider",
                      fontFamily: "ui-monospace, SFMono-Regular, Menlo, monospace",
                    }}
                  >
                    {p.diff.map((d, i) => (
                      <Stack
                        key={i}
                        direction="row"
                        spacing={1.25}
                        sx={{
                          px: 1.5, py: 0.875,
                          bgcolor: (t) => d.type === "add"
                            ? alpha("#16A34A", t.palette.mode === "dark" ? 0.1 : 0.06)
                            : alpha("#DC2626", t.palette.mode === "dark" ? 0.1 : 0.06),
                        }}
                      >
                        <Typography
                          sx={{
                            typography: "s2", fontFamily: "inherit", flexShrink: 0,
                            color: d.type === "add" ? "#16A34A" : "#DC2626", fontWeight: 700,
                          }}
                        >
                          {d.type === "add" ? "+" : "−"}
                        </Typography>
                        <Typography sx={{ typography: "s2", fontFamily: "inherit", color: "text.secondary" }}>
                          {d.text}
                        </Typography>
                      </Stack>
                    ))}
                  </Box>
                </Box>
              </SectionCard>
            ))}
          </Stack>
        </Fade>
      )}
    </Stack>
  );
}

OptimizePanel.propTypes = {
  env: PropTypes.object, tasks: PropTypes.array, stats: PropTypes.object,
};
