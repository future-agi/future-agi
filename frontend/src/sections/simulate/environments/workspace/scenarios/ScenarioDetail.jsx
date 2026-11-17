import PropTypes from "prop-types";
import { useState } from "react";
import { Box, Stack, Typography, Collapse } from "@mui/material";

import Iconify from "src/components/iconify";
import { SCENARIO_SHAPE } from "./scenarios.shapes";
import { normaliseSubTasks } from "./scenarioEditor.constants";
import { genderLabel } from "./personaLabels";

// A scenario, in full — the expanded body of a list-view row.
//
// Strips from the designer source (REF components/ScenarioDetail.jsx; each
// noted so nobody re-derives it): the validation-proof section, the simulated-
// caller policy section, the reference solution, "Graded against" and "Its
// folder" all read fixtures (validate / proofStatus / VALIDATION_CHECKS /
// simulatorPolicy / scenarioFolder) that are not ported in this phase and sit
// outside the scenarios slice. The sections kept below render entirely from the
// scenario row.
const onKeyActivate = (fn) => (e) => {
  if (e.key === "Enter" || e.key === " ") {
    e.preventDefault();
    fn();
  }
};

export default function ScenarioDetail({ row, defaultOpen = false }) {
  const [open, setOpen] = useState(defaultOpen);
  const s = row;
  if (!s) return null;

  return (
    <Box>
      <Stack
        direction="row" alignItems="center" spacing={1.5}
        role="button" tabIndex={0}
        onClick={() => setOpen((o) => !o)}
        onKeyDown={onKeyActivate(() => setOpen((o) => !o))}
        sx={{ px: 3, py: 1.25, cursor: "pointer", "&:hover": { bgcolor: "action.hover" } }}
      >
        <Iconify
          icon={open ? "solar:alt-arrow-down-linear" : "solar:alt-arrow-right-linear"}
          width={13}
          sx={{ color: "text.subtitle", flexShrink: 0 }}
        />
        <Typography
          noWrap
          sx={{
            typography: "s1", fontWeight: "fontWeightRegular",
            fontFamily: "ui-monospace, Menlo, monospace",
            color: "text.primary", flexShrink: 0,
          }}
        >
          {s.name || s.title}
        </Typography>
        {s.summary && (
          <Typography noWrap sx={{ typography: "s3", color: "text.subtitle", flex: 1, minWidth: 0 }}>
            {s.summary}
          </Typography>
        )}
      </Stack>

      <Collapse in={open} unmountOnExit>
        <ScenarioDetailBody row={s} sx={{ px: 3, pb: 3, pt: 1.5 }} />
      </Collapse>
    </Box>
  );
}

// The persona facts the API serves, in reading order; each shows only when set.
const PERSONA_ROWS = [
  ["Name", (p) => p.name],
  ["Gender", (p) => genderLabel(p.gender)],
  ["Age group", (p) => p.ageGroup],
  ["Occupation", (p) => p.occupation],
  ["Location", (p) => p.location],
  ["Personality", (p) => p.personality],
  ["Communication style", (p) => p.communicationStyle],
  ["Languages", (p) => (Array.isArray(p.languages) ? p.languages.join(", ") : p.languages)],
  ["Accent", (p) => p.accent],
  ["Opens with", (p) => p.initialMessage],
];

// What a scenario is, section by section: the brief, its sub-goals, the
// persona, the conversation flow and what it tests: the list view's expanded
// row.
export function ScenarioDetailBody({ row, sx }) {
  const s = row;
  if (!s) return null;
  // A section with nothing to say is left out rather than shown empty.
  const testsText = s.expected || s.outcome;
  const whatItTests = testsText ? (
    <Section title="What this tests">
      <Typography sx={{ typography: "s1", color: "text.secondary" }}>
        {testsText}
      </Typography>
    </Section>
  ) : null;
  // Category and branch are often the same text (both come from `branch`);
  // show the category only when it says something the branch doesn't.
  const branchText = Array.isArray(row.conversationBranch)
    ? row.conversationBranch.join(" → ")
    : row.conversationBranch;
  const showCategory = row.branchCategory && row.branchCategory !== branchText;
  const steps = normaliseSubTasks(row.subTasks);
  const persona = s.persona
    ? PERSONA_ROWS.map(([label, read]) => [label, read(s.persona)]).filter(([, value]) => value)
    : [];

  return (
    <Stack
      spacing={3}
      divider={<Box sx={{ borderBottom: "1px solid", borderColor: "divider" }} />}
      sx={{
        "& p, & li, & > .MuiStack-root > .MuiTypography-root": { maxWidth: 780 },
        ...sx,
      }}
    >
      {(s.task || s.persona?.name) && (
      <Section title="The person is told">
        <Typography
          sx={{
            typography: "s1", color: "text.secondary",
            borderLeft: "1px solid", borderColor: "divider", pl: 1.5,
          }}
        >
          {s.task}{" "}
          {s.persona?.name && `You are ${s.persona.name}.`}
        </Typography>
      </Section>
      )}

      {steps.length > 0 && (
        <Section title={`Sub-goals: the moves that settle it (${steps.length})`}>
          <Stack spacing={0.75}>
            {steps.map((st, i) => (
              <Stack key={st.id} direction="row" spacing={1.25} alignItems="flex-start">
                <Box
                  sx={{
                    width: 18, height: 18, borderRadius: "50%", flexShrink: 0, mt: "1px",
                    display: "grid", placeItems: "center",
                    bgcolor: "action.selected",
                    color: "text.secondary",
                    typography: "s3", fontWeight: "fontWeightMedium", fontVariantNumeric: "tabular-nums",
                  }}
                >
                  {i + 1}
                </Box>
                <Typography sx={{ typography: "s1", color: "text.secondary" }}>{st.label}</Typography>
              </Stack>
            ))}
          </Stack>
        </Section>
      )}

      {persona.length > 0 && (
        <Section title="Persona">
          <Stack spacing={1}>
            {persona.map(([label, value]) => (
              <PolicyRow key={label} label={label}>{value}</PolicyRow>
            ))}
          </Stack>
        </Section>
      )}

      {(row.conversationBranch || row.branchCategory) && (
        <Section title="Conversation flow">
          <Stack spacing={1}>
            {showCategory && <PolicyRow label="Category">{row.branchCategory}</PolicyRow>}
            {row.conversationBranch && (
              <PolicyRow label="Branch">
                <Typography sx={{
                  typography: "s1",
                  color: "text.primary", wordBreak: "break-word",
                }}>
                  {branchText}
                </Typography>
              </PolicyRow>
            )}
          </Stack>
        </Section>
      )}

      {whatItTests}
    </Stack>
  );
}

ScenarioDetailBody.propTypes = {
  row: SCENARIO_SHAPE,
  sx: PropTypes.object,
};

ScenarioDetail.propTypes = {
  row: SCENARIO_SHAPE,
  defaultOpen: PropTypes.bool,
};

function PolicyRow({ label, children }) {
  return (
    <Stack direction={{ xs: "column", sm: "row" }} spacing={{ xs: 0.5, sm: 2 }} sx={{ py: 0.5 }}>
      <Typography sx={{ typography: "s1", color: "text.subtitle", width: { sm: 170 }, flexShrink: 0 }}>
        {label}
      </Typography>
      <Box sx={{ flex: 1, minWidth: 0 }}>
        {typeof children === "string"
          ? <Typography sx={{ typography: "s1", color: "text.primary" }}>{children}</Typography>
          : children}
      </Box>
    </Stack>
  );
}
PolicyRow.propTypes = { label: PropTypes.string, children: PropTypes.node };

function Section({ title, children }) {
  return (
    <Box>
      <Typography sx={{ typography: "s1", fontWeight: "fontWeightMedium", color: "text.primary", mb: 1 }}>
        {title}
      </Typography>
      {children}
    </Box>
  );
}
Section.propTypes = { title: PropTypes.string, children: PropTypes.node };
