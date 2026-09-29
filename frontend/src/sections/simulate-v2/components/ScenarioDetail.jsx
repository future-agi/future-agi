import PropTypes from "prop-types";
import { Fragment, isValidElement, useState } from "react";
import { alpha } from "@mui/material/styles";
import { Box, Stack, Typography, Collapse, Tooltip } from "@mui/material";
import Iconify from "src/components/iconify";
import { validate, scenarioFolder, subTasksFor } from "../_mock/contract";
import { proofStatus, INVALIDATING } from "../_mock/proofs";
import { simulatorPolicy } from "../_mock/simulatorPolicy";
import { parseBrief, subGoalText, asSentence } from "../_mock/scenarioBrief";

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
  const [showInternals, setShowInternals] = useState(false);
  const s = validate(row, env);
  /* A proof is only true of the world it was run against. In buildMode the
     environment was just derived (v1 by definition), so nothing has drifted
     and every scenario reads as validated. */
  const proof = buildMode
    ? { proved: "v1", current: "v1", stale: false, reasons: [], since: [] }
    : proofStatus(row, env, envState);
  const policy = simulatorPolicy(row, env);
  if (!s) return null;
  const brief = parseBrief(s.task);
  /* Sub-goals arrive as objects in the mocks and as plain strings from the
     API — reading only `.label` left production rows with empty numbers. */
  const goals = (row.subTasks?.length ? row.subTasks : subTasksFor(row, env)).map(subGoalText).filter(Boolean);
  const path = conversationPath(row);

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
        {/* The one-line summary, else the conversation category. Never the
            brief itself — shouted in capitals, it read as an error. */}
        {(s.summary || s.branchCategory) && (
          <Typography
            noWrap
            sx={{
              typography: "s3", color: "text.subtitle",
              flex: 1, minWidth: 0,
            }}
          >
            {s.summary || s.branchCategory}
          </Typography>
        )}
      </Stack>

      {/*
        Expanded: one column of labelled rows. Labels sit in a fixed gutter so
        every fact lines up whether the brief is one line or a page, and the
        content shares one size and colour so nothing competes. The machinery
        — caller policy, reference solution, files — opens from the Proof row.
      */}
      <Collapse in={open} unmountOnExit>
        <Box sx={{ pl: "45px", pr: 2.5, pt: 0.25, pb: 2 }}>
          <Box
            sx={{
              display: "grid",
              gridTemplateColumns: { xs: "1fr", sm: "104px minmax(0, 1fr)" },
              columnGap: 2.5, rowGap: { xs: 0.5, sm: 1.5 },
              "& .sd-val": { typography: "s2", color: "text.secondary", lineHeight: 1.6, maxWidth: 760 },
            }}
          >
            {s.expected && (
              <Field label="Passes when">
                <Typography className="sd-val" sx={{ color: "text.primary !important" }}>{asSentence(s.expected)}</Typography>
              </Field>
            )}

            {s.persona && (
              <Field label="Caller">
                <Typography className="sd-val">
                  <Box component="span" sx={{ color: "text.primary", fontWeight: 600 }}>{s.persona.name}</Box>
                  {[personaMeta(s.persona), s.persona.traits?.join(", ")].filter(Boolean).map((part) => ` · ${part}`)}
                </Typography>
              </Field>
            )}

            {(brief.intro || brief.steps.length > 0 || brief.details.length > 0) && (
              <Field label="Brief">
                {brief.intro && <Typography className="sd-val">{brief.intro}</Typography>}
                {brief.steps.length > 0 && (
                  <NumberedList items={brief.steps} sx={{ mt: brief.intro ? 0.75 : 0 }} />
                )}
                {brief.details.length > 0 && (
                  <Box
                    sx={{
                      mt: 1, display: "grid", gridTemplateColumns: "max-content minmax(0, 1fr)",
                      columnGap: 2, rowGap: 0.25, maxWidth: 760,
                    }}
                  >
                    {brief.details.map((d, i) => (
                      <Fragment key={`${d.key}-${i}`}>
                        <Typography sx={{ typography: "s2", color: "text.subtitle", lineHeight: 1.6 }}>{d.key || "Detail"}</Typography>
                        <Typography className="sd-val">{d.value}</Typography>
                      </Fragment>
                    ))}
                  </Box>
                )}
              </Field>
            )}

            {goals.length > 0 && (
              <Field label="Sub-goals">
                <NumberedList items={goals} />
              </Field>
            )}

            {path && (
              <Field label="Path">
                {Array.isArray(path.steps) ? (
                  <Tooltip arrow placement="top-start" title={path.steps.join(" → ")}>
                    <Typography
                      noWrap
                      className="sd-val"
                      sx={{ fontFamily: "ui-monospace, Menlo, monospace", fontSize: "12.5px !important" }}
                    >
                      {path.steps.join("  →  ")}
                    </Typography>
                  </Tooltip>
                ) : (
                  <Typography className="sd-val">{path.steps}</Typography>
                )}
              </Field>
            )}

            {s.checks?.length > 0 && (
              <Field label="Graded on">
                <Typography className="sd-val">
                  {s.checks.map((c, i) => (
                    <Fragment key={c.id}>
                      {i > 0 && <Box component="span" sx={{ color: "text.disabled" }}>{"  ·  "}</Box>}
                      <Tooltip arrow title={c.kind === "judge" ? "Judged by a grader reading the conversation" : "Checked against the world's state after the run"}>
                        <Box component="span" sx={{ borderBottom: "1px dotted", borderColor: "text.disabled", cursor: "help" }}>
                          {checkName(c.label)}
                        </Box>
                      </Tooltip>
                    </Fragment>
                  ))}
                </Typography>
              </Field>
            )}

            <Field label="Proof">
              <ProofRow proof={proof} open={showInternals} onToggle={() => setShowInternals((o) => !o)} />
            </Field>
          </Box>

          <Collapse in={showInternals} unmountOnExit>
            <Internals s={s} env={env} policy={policy} />
          </Collapse>
        </Box>
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
    /* Same gutter as the rows above it, so the details open in line. */
    <Stack direction={{ xs: "column", sm: "row" }} spacing={{ xs: 0.5, sm: 2.5 }} sx={{ py: 0.375 }}>
      <Typography sx={{ typography: "s2", color: "text.subtitle", width: { sm: 104 }, flexShrink: 0, lineHeight: 1.6 }}>
        {label}
      </Typography>
      <Box sx={{ flex: 1, minWidth: 0 }}>
        {/* Plain text — one string or several pieces joined in JSX — gets the
            body style; without it "Manner" rendered at the page's base size. */}
        {isValidElement(children)
          ? children
          : <Typography sx={{ typography: "s2", color: "text.secondary", lineHeight: 1.6 }}>{children}</Typography>}
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
          typography: "s3", fontWeight: 600, color: "text.subtitle",
          mb: 0.75,
        }}
      >
        {title}
      </Typography>
      {children}
    </Box>
  );
}
Section.propTypes = { title: PropTypes.string, children: PropTypes.node };

/* ── pieces ─────────────────────────────────────────────────────────────── */

/* One labelled row: quiet label in the gutter, content beside it. */
function Field({ label, children }) {
  return (
    <>
      <Typography sx={{ typography: "s2", color: "text.subtitle", lineHeight: 1.6, pt: { sm: 0 } }}>{label}</Typography>
      <Box minWidth={0} sx={{ mb: { xs: 1, sm: 0 } }}>{children}</Box>
    </>
  );
}
Field.propTypes = { label: PropTypes.string, children: PropTypes.node };

/* A plain numbered list — the numbers order it, they don't need a badge. */
function NumberedList({ items, sx }) {
  return (
    <Stack spacing={0.5} sx={{ maxWidth: 760, ...sx }}>
      {items.map((text, i) => (
        <Stack key={i} direction="row" spacing={1} alignItems="flex-start">
          <Typography sx={{ typography: "s2", color: "text.disabled", lineHeight: 1.6, width: 14, flexShrink: 0, fontVariantNumeric: "tabular-nums" }}>
            {i + 1}.
          </Typography>
          <Typography className="sd-val">{text}</Typography>
        </Stack>
      ))}
    </Stack>
  );
}
NumberedList.propTypes = { items: PropTypes.array, sx: PropTypes.object };

/* TASK_COMPLETED → "Task completed" */
const checkName = (label = "") => {
  const words = label.toLowerCase().replace(/_/g, " ");
  return words.charAt(0).toUpperCase() + words.slice(1);
};

/* "Male · 40–50 · Lead Finance Administrator" */
const personaMeta = (p) => [
  p.gender && p.gender.charAt(0).toUpperCase() + p.gender.slice(1),
  p.ageGroup ? String(p.ageGroup).replace("-", "–") : p.age && `Age ${p.age}`,
  p.role,
  !p.gender && p.voice && `${p.voice} voice`,
].filter(Boolean).join(" · ");

/*
  The path the conversation should take. In production the category and the
  branch are often the same sentence — it is shown once. A branch that is a
  list of handlers reads as a path of chips.
*/
const conversationPath = (row) => {
  const branch = row.conversationBranch;
  const category = row.branchCategory;
  if (!branch && !category) return null;
  const same = typeof branch === "string" && category && branch.trim() === category.trim();
  const text = !branch || same ? category || branch : branch;
  /* Already on the collapsed row, next to the name — don't say it twice. */
  if (typeof text === "string" && text.trim() === String(row.summary || row.branchCategory || "").trim()) return null;
  return { steps: text, category: !branch || same ? null : category || null };
};

/* Proved or not, what that means, and the way into the machinery. */
function ProofRow({ proof, open, onToggle }) {
  const ok = !proof.stale;
  return (
    <Stack direction="row" alignItems="center" spacing={0.75} flexWrap="wrap" rowGap={0.25}>
      <Iconify
        icon={ok ? "solar:check-circle-bold" : "solar:question-circle-bold"}
        width={14}
        sx={{ color: ok ? "#16A34A" : "#CA8A04", flexShrink: 0 }}
      />
      <Typography sx={{ typography: "s2", color: "text.secondary", lineHeight: 1.6 }}>
        {ok
          ? `Proved against ${proof.proved} — the world is ready, a reference solution passes, and every check can fail`
          : `Needs re-proving on ${proof.current} — ${proof.reasons.map((r) => INVALIDATING[r]).join("; ")}`}
      </Typography>
      <Box
        component="button"
        type="button"
        onClick={onToggle}
        sx={{
          p: 0, border: 0, bgcolor: "transparent", cursor: "pointer", font: "inherit",
          typography: "s2", fontWeight: 600, color: "text.primary",
          display: "inline-flex", alignItems: "center", gap: 0.25,
          "&:hover": { textDecoration: "underline" },
        }}
      >
        {open ? "Hide details" : "Show details"}
        <Iconify icon={open ? "solar:alt-arrow-up-linear" : "solar:alt-arrow-down-linear"} width={12} />
      </Box>
    </Stack>
  );
}
ProofRow.propTypes = { proof: PropTypes.object, open: PropTypes.bool, onToggle: PropTypes.func };

/* The simulated caller's policy, the reference solution and the files. */
function Internals({ s, env, policy }) {
  return (
    <Stack
      spacing={2}
      divider={<Box sx={{ borderBottom: "1px dashed", borderColor: "divider" }} />}
      sx={{ mt: 2, pt: 2, borderTop: "1px dashed", borderColor: "divider", "& p, & li": { maxWidth: 780 } }}
    >
          {policy && (
            <Section title="The simulated caller — policy, not a prompt">
              <Stack spacing={1.25}>
                {/* Usually the brief, word for word — shown above already. */}
                {policy.goal && policy.goal !== s.task && <PolicyRow label="Goal">{policy.goal}</PolicyRow>}
                <PolicyRow label="States plainly">
                  <Stack component="ul" sx={{ m: 0, pl: 2 }}>
                    {policy.facts.map((f) => (
                      <Typography key={f} component="li" sx={{ typography: "s2", color: "text.secondary" }}>{f}</Typography>
                    ))}
                  </Stack>
                </PolicyRow>
                <PolicyRow label="Held back">
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
                <PolicyRow label="Hangs up when">
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
  );
}
Internals.propTypes = { s: PropTypes.object, env: PropTypes.object, policy: PropTypes.object };
