import PropTypes from "prop-types";
import { useState } from "react";
import { alpha } from "@mui/material/styles";
import { Box, Stack, Typography, Collapse } from "@mui/material";

import Iconify from "src/components/iconify";
import { BUILD_TONES } from "../../buildEnvironment/buildTones";
import { SCENARIO_SHAPE } from "./scenarios.shapes";

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

  const steps = row.subTasks || [];

  return (
    <Box>
      <Stack
        direction="row" alignItems="center" spacing={1.5}
        role="button" tabIndex={0}
        onClick={() => setOpen((o) => !o)}
        onKeyDown={onKeyActivate(() => setOpen((o) => !o))}
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
            typography: "s2", fontWeight: "fontWeightSemiBold",
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
        <Stack
          spacing={2.75}
          divider={<Box sx={{ borderBottom: "1px dashed", borderColor: "divider" }} />}
          sx={{
            px: 3, pb: 3, pt: 1.5,
            "& p, & li, & > .MuiStack-root > .MuiTypography-root": { maxWidth: 780 },
          }}
        >
          <Section title="The person is told">
            <Typography
              sx={{
                typography: "s2", color: "text.secondary",
                borderLeft: "2px solid", borderColor: "divider", pl: 1.5,
              }}
            >
              {s.task}{" "}
              {s.persona && `You are ${s.persona.name}${s.persona.role ? `, ${s.persona.role.toLowerCase()}` : ""}${s.persona.traits?.length ? ` — ${s.persona.traits.join(", ")}` : ""}.`}
            </Typography>
          </Section>

          {steps.length > 0 && (
            <Section title={`Sub-goals — the moves that settle it (${steps.length})`}>
              <Stack spacing={0.75}>
                {steps.map((st, i) => (
                  <Stack key={st.id || i} direction="row" spacing={1.25} alignItems="flex-start">
                    <Box
                      sx={{
                        width: 18, height: 18, borderRadius: "50%", flexShrink: 0, mt: "1px",
                        display: "grid", placeItems: "center",
                        bgcolor: (t) => alpha(BUILD_TONES.accent, t.palette.mode === "dark" ? 0.16 : 0.1),
                        color: BUILD_TONES.accent,
                        typography: "s3", fontWeight: "fontWeightBold", fontVariantNumeric: "tabular-nums",
                      }}
                    >
                      {i + 1}
                    </Box>
                    <Typography sx={{ typography: "s2", color: "text.secondary" }}>{st.label}</Typography>
                  </Stack>
                ))}
              </Stack>
            </Section>
          )}

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

          {(row.conversationBranch || row.branchCategory) && (
            <Section title="Conversation flow">
              <Stack spacing={1}>
                {row.branchCategory && <PolicyRow label="Category">{row.branchCategory}</PolicyRow>}
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
              {s.expected || s.outcome}
            </Typography>
          </Section>
        </Stack>
      </Collapse>
    </Box>
  );
}

ScenarioDetail.propTypes = {
  row: SCENARIO_SHAPE,
  defaultOpen: PropTypes.bool,
};

function PolicyRow({ label, children }) {
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
  return (
    <Box>
      <Typography sx={{ typography: "s1", fontWeight: "fontWeightBold", color: "text.primary", mb: 1 }}>
        {title}
      </Typography>
      {children}
    </Box>
  );
}
Section.propTypes = { title: PropTypes.string, children: PropTypes.node };
