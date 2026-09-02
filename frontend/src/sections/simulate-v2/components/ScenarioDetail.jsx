import PropTypes from "prop-types";
import { useState } from "react";
import { alpha } from "@mui/material/styles";
import { Box, Stack, Typography, Collapse } from "@mui/material";
import Iconify from "src/components/iconify";
import { validate, scenarioFolder, VALIDATION_CHECKS, subTasksFor } from "../_mock/contract";
import { proofStatus, INVALIDATING } from "../_mock/proofs";
import { simulatorPolicy } from "../_mock/simulatorPolicy";

/**
 * A scenario, in full.
 *
 * The row alone says what the task is; it does not say whether the task is
 * worth running. Expanding shows the proof — the world was built, a reference
 * solution passed, and every check was made to fail on purpose — plus the brief
 * the person is given and what the run will be graded on.
 *
 * The reference solution is listed but never run against the agent: it exists
 * to show the scenario is passable, not to tell the agent how.
 */
export default function ScenarioDetail({ row, env, envState, defaultOpen = false, buildMode = false }) {
  const [open, setOpen] = useState(defaultOpen);
  const s = validate(row, env);
  /* A proof is only true of the world it was run against. In buildMode the
     environment was just derived (v1 by definition), so nothing has drifted
     and every scenario reads as validated. */
  const proof = buildMode
    ? { proved: "v1", current: "v1", stale: false, reasons: [], since: [] }
    : proofStatus(row, env, envState);
  const policy = simulatorPolicy(row, env);
  if (!s) return null;

  return (
    <Box>
      {/*
        Collapsed row shape asked for by the product team:
          - Short kebab-case name in mono (row label)
          - One-line summary next to it (context)
          - No VALIDATED chip (moved into the expanded body where the
            proof detail lives, so the row itself stays scannable)
      */}
      <Stack
        direction="row"
        alignItems="center"
        spacing={1.5}
        onClick={() => setOpen((o) => !o)}
        sx={{ px: 2.5, py: 1.25, cursor: "pointer", "&:hover": { bgcolor: "action.hover" } }}
      >
        <Iconify
          icon={open ? "solar:alt-arrow-down-linear" : "solar:alt-arrow-right-linear"}
          width={13}
          sx={{ color: "text.subtitle", flexShrink: 0 }}
        />
        <Typography
          noWrap
          sx={{
            typography: "s2", fontWeight: 600,
            fontFamily: "ui-monospace, Menlo, monospace",
            color: "text.primary",
            flexShrink: 0,
          }}
        >
          {s.name || s.title}
        </Typography>
        {(s.summary || s.headline) && (
          <Typography
            noWrap
            sx={{
              typography: "s3", color: "text.subtitle",
              flex: 1, minWidth: 0,
            }}
          >
            {s.summary || s.headline}
          </Typography>
        )}
      </Stack>

      <Collapse in={open} unmountOnExit>
        <Stack
          spacing={2.75}
          divider={<Box sx={{ borderBottom: "1px dashed", borderColor: "divider" }} />}
          sx={{
            px: 3, pb: 3, pt: 1.5,
            /*
              Cap the reading width. Prose across the full 900px panel
              breaks scanning — the eye has to travel too far. This is
              the same 72-column rule good docs use.
            */
            "& p, & li, & > .MuiStack-root > .MuiTypography-root": { maxWidth: 780 },
          }}
        >
          <Section title={`Validation — proved against ${proof.proved}`}>
            <Stack direction="row" spacing={2} flexWrap="wrap" rowGap={0.75}>
              {VALIDATION_CHECKS.map((v) => (
                <Stack key={v.id} direction="row" alignItems="center" spacing={0.625}>
                  <Iconify
                    icon={proof.stale ? "solar:question-circle-bold" : "solar:check-circle-bold"}
                    width={14}
                    sx={{ color: proof.stale ? "#CA8A04" : "#16A34A", flexShrink: 0 }}
                  />
                  <Typography sx={{ typography: "s2", color: "text.secondary" }}>{v.label}</Typography>
                </Stack>
              ))}
            </Stack>
            {proof.stale && (
              <Typography sx={{ typography: "s2", color: "#CA8A04", mt: 0.875 }}>
                The environment is on {proof.current} now — {proof.reasons.map((r) => INVALIDATING[r]).join("; ")}.
                Until it is re-proved, these three are claims about a world that no longer exists.
              </Typography>
            )}
          </Section>

          <Section title="The person is told">
            <Typography
              sx={{
                typography: "s2", color: "text.secondary",
                borderLeft: "2px solid", borderColor: "divider", pl: 1.5,
              }}
            >
              {s.task} {s.persona && `You are ${s.persona.name}${s.persona.role ? `, ${s.persona.role.toLowerCase()}` : ""}${s.persona.traits?.length ? ` — ${s.persona.traits.join(", ")}` : ""}.`}
            </Typography>
          </Section>

          {/*
            The caller, as policy rather than as prose. Inspectable before the
            run, because "the simulated user went off-script" is only a usable
            finding when there was a script to go off — and because a caller
            that breaks these rules is a simulator failure, not an agent one.
          */}
          {policy && (
            <Section title="The simulated caller — policy, not a prompt">
              <Stack spacing={1.25}>
                <PolicyRow label="Goal">{policy.goal}</PolicyRow>
                <PolicyRow label="States plainly">
                  <Stack component="ul" sx={{ m: 0, pl: 2 }}>
                    {policy.facts.map((f) => (
                      <Typography key={f} component="li" sx={{ typography: "s2", color: "text.secondary" }}>{f}</Typography>
                    ))}
                  </Stack>
                </PolicyRow>
                <PolicyRow label="Held back until asked">
                  <Stack spacing={0.375}>
                    {policy.private.map((f) => (
                      <Stack key={f.fact} direction="row" spacing={0.75} alignItems="flex-start">
                        <Iconify icon="solar:lock-keyhole-minimalistic-linear" width={13} sx={{ color: "#CA8A04", flexShrink: 0, mt: "2px" }} />
                        <Typography sx={{ typography: "s2", color: "text.secondary" }}>
                          <Box component="span" sx={{ color: "text.primary", fontWeight: 600 }}>{f.fact}</Box> — {f.trigger}
                        </Typography>
                      </Stack>
                    ))}
                  </Stack>
                </PolicyRow>
                <PolicyRow label="Manner">
                  {policy.style.verbosity}, {policy.style.patience} patience, {policy.style.precision} precision
                  {" · "}{policy.objections.style.toLowerCase()} (max {policy.objections.max})
                  {" · "}{policy.interruption.allowed ? policy.interruption.when : "does not interrupt"}
                </PolicyRow>
                <PolicyRow label="Ends the call when">
                  <Stack component="ul" sx={{ m: 0, pl: 2 }}>
                    {policy.termination.map((t) => (
                      <Typography key={t} component="li" sx={{ typography: "s2", color: "text.secondary" }}>{t}</Typography>
                    ))}
                  </Stack>
                </PolicyRow>
                <PolicyRow label="Never">
                  <Stack spacing={0.25}>
                    {policy.prohibited.map((t) => (
                      <Typography key={t} sx={{ typography: "s2", color: "text.secondary" }}>· {t}</Typography>
                    ))}
                  </Stack>
                </PolicyRow>
                <Typography sx={{ typography: "s3", color: "text.subtitle" }}>
                  The caller is never tuned to make the agent pass. It follows this policy, and if
                  that exposes the agent, that is the point.
                </Typography>
              </Stack>
            </Section>
          )}

          {/*
            The sub-tasks: the moves the agent has to settle to complete the
            main task. Read from `row.subTasks` if the author overrode them,
            otherwise derived so every scenario has a breakdown to show.
          */}
          {(() => {
            const steps = row.subTasks?.length ? row.subTasks : subTasksFor(row, env);
            if (!steps.length) return null;
            return (
              <Section title={`Sub-tasks — the moves that settle it (${steps.length})`}>
                <Stack spacing={0.75}>
                  {steps.map((st, i) => (
                    <Stack key={st.id || i} direction="row" spacing={1.25} alignItems="flex-start">
                      <Box
                        sx={{
                          width: 18, height: 18, borderRadius: "50%", flexShrink: 0, mt: "1px",
                          display: "grid", placeItems: "center",
                          bgcolor: (t) => alpha("#7857FC", t.palette.mode === "dark" ? 0.16 : 0.1),
                          color: "#7857FC",
                          typography: "s3", fontWeight: 700, fontVariantNumeric: "tabular-nums",
                        }}
                      >
                        {i + 1}
                      </Box>
                      <Typography sx={{ typography: "s2", color: "text.secondary" }}>
                        {st.label}
                      </Typography>
                    </Stack>
                  ))}
                </Stack>
              </Section>
            );
          })()}

          {/*
            Persona meta — same shape as the "old" scenarios table
            showed (name / gender / age group / role for requesters). A
            proper structured section instead of only inline in the
            brief above, so the reader can scan it at a glance.
          */}
          {s.persona && (
            <Section title="Persona">
              <Stack spacing={1}>
                <PolicyRow label="Name">{s.persona.name}</PolicyRow>
                {s.persona.gender && (
                  <PolicyRow label="Gender">
                    {s.persona.gender.charAt(0).toUpperCase() + s.persona.gender.slice(1)}
                  </PolicyRow>
                )}
                {s.persona.ageGroup && <PolicyRow label="Age group">{s.persona.ageGroup}</PolicyRow>}
                {!s.persona.gender && s.persona.role && (
                  <PolicyRow label="Role">{s.persona.role}</PolicyRow>
                )}
                {s.persona.traits?.length > 0 && (
                  <PolicyRow label="Traits">{s.persona.traits.join(", ")}</PolicyRow>
                )}
              </Stack>
            </Section>
          )}

          {/* Conversation flow — the branch of handlers the run should
              travel through, plus a short category label. Both come
              straight from the scenario mock. */}
          {(row.conversationBranch || row.branchCategory) && (
            <Section title="Conversation flow">
              <Stack spacing={1}>
                {row.branchCategory && (
                  <PolicyRow label="Category">{row.branchCategory}</PolicyRow>
                )}
                {row.conversationBranch && (
                  <PolicyRow label="Branch">
                    <Typography sx={{
                      typography: "s2",
                      fontFamily: "ui-monospace, Menlo, monospace",
                      color: "text.primary", wordBreak: "break-word",
                    }}>
                      {Array.isArray(row.conversationBranch)
                        ? row.conversationBranch.join(" → ")
                        : row.conversationBranch}
                    </Typography>
                  </PolicyRow>
                )}
              </Stack>
            </Section>
          )}

          <Section title="What this tests">
            <Typography sx={{ typography: "s2", color: "text.secondary", fontStyle: "italic" }}>
              {s.expected}
            </Typography>
          </Section>

          <Section title="The reference solution — proves it can be passed, never run against the agent">
            <Stack spacing={0.375}>
              {s.reference.map((r, i) => (
                <Typography
                  key={i}
                  sx={{ typography: "s2", fontFamily: "ui-monospace, Menlo, monospace", color: "text.secondary" }}
                >
                  {i + 1}. {r.tool}
                  {Object.keys(r.args).length > 0 && (
                    <Box component="span" sx={{ color: "text.subtitle" }}>
                      {" "}{Object.keys(r.args).join(", ")}
                    </Box>
                  )}
                </Typography>
              ))}
            </Stack>
          </Section>

          <Section title="Graded against">
            <Stack direction="row" spacing={0.75} flexWrap="wrap" rowGap={0.75}>
              {s.checks.map((c) => <CheckChip key={c.id} check={c} />)}
            </Stack>
          </Section>

          <Section title="Its folder">
            <Typography
              sx={{ typography: "s3", color: "text.subtitle", fontFamily: "ui-monospace, Menlo, monospace", mb: 0.75 }}
            >
              {scenarioFolder(env, s)}
            </Typography>
            <Stack direction="row" spacing={0.75} flexWrap="wrap" rowGap={0.75}>
              {s.files.map((f) => (
                <Typography
                  key={f}
                  sx={{
                    px: 0.875, py: 0.375, borderRadius: 0.75,
                    typography: "s3", fontFamily: "ui-monospace, Menlo, monospace",
                    color: "text.secondary", border: "1px solid", borderColor: "divider",
                  }}
                >
                  {f}
                </Typography>
              ))}
            </Stack>
          </Section>
        </Stack>
      </Collapse>
    </Box>
  );
}

ScenarioDetail.propTypes = {
  envState: PropTypes.object,
  row: PropTypes.object,
  env: PropTypes.object,
  defaultOpen: PropTypes.bool,
  buildMode: PropTypes.bool,
};

export function ValidatedBadge({ stale }) {
  const color = stale ? "#CA8A04" : "#16A34A";
  return (
    <Typography
      sx={{
        px: 0.75, py: 0.25, borderRadius: 0.5, flexShrink: 0,
        typography: "s3", fontWeight: 700, color,
        bgcolor: (t) => alpha(color, t.palette.mode === "dark" ? 0.16 : 0.1),
      }}
    >
      {stale ? "RE-PROVE" : "VALIDATED"}
    </Typography>
  );
}
ValidatedBadge.propTypes = { stale: PropTypes.bool };

export function CheckChip({ check }) {
  const judged = check.kind === "judge";
  const color = judged ? "#CA8A04" : "#16A34A";
  return (
    <Stack
      direction="row"
      alignItems="center"
      spacing={0.5}
      sx={{
        px: 0.875, py: 0.375, borderRadius: 0.75,
        bgcolor: (t) => alpha(color, t.palette.mode === "dark" ? 0.16 : 0.1),
      }}
    >
      <Typography sx={{ typography: "s3", fontWeight: 700, color }}>{check.label}</Typography>
      <Typography sx={{ typography: "s3", color: "text.subtitle" }}>
        {judged ? "judged" : "world state"}
      </Typography>
    </Stack>
  );
}
CheckChip.propTypes = { check: PropTypes.object };

function PolicyRow({ label, children }) {
  /*
    Two-column layout — label on the left, value on the right. Reads as
    a real key/value list rather than a stack of uppercase headings +
    prose. Uppercase small-caps here competed with the section headers
    and made the caller-policy block dense.
  */
  return (
    <Stack direction={{ xs: "column", sm: "row" }} spacing={{ xs: 0.5, sm: 2 }} sx={{ py: 0.5 }}>
      <Typography sx={{ typography: "s2", color: "text.subtitle", width: { sm: 170 }, flexShrink: 0 }}>
        {label}
      </Typography>
      <Box sx={{ flex: 1, minWidth: 0 }}>
        {typeof children === "string"
          ? <Typography sx={{ typography: "s2", color: "text.primary" }}>{children}</Typography>
          : children}
      </Box>
    </Stack>
  );
}

PolicyRow.propTypes = { label: PropTypes.string, children: PropTypes.node };

function Section({ title, children }) {
  /*
    Softer section headers — sentence-case, medium weight, no small-caps
    letter spacing. The aggressive uppercase treatment competed with the
    scenario title above and made every section header shout equally, so
    the reader had no hierarchy to land on. This lets the body of each
    section carry the eye instead.
  */
  return (
    <Box>
      <Typography
        sx={{
          typography: "s1", fontWeight: 700, color: "text.primary",
          mb: 1,
        }}
      >
        {title}
      </Typography>
      {children}
    </Box>
  );
}
Section.propTypes = { title: PropTypes.string, children: PropTypes.node };
