import PropTypes from "prop-types";
import { useState } from "react";
import { Box, Button, Stack, Typography, Collapse } from "@mui/material";

import Iconify from "src/components/iconify";
import { SCENARIO_SHAPE } from "./scenarios.shapes";
import { normaliseSubTasks } from "./scenarioEditor.constants";
import { genderLabel } from "./personaLabels";
import { SCENARIOS_COPY } from "./scenarios.constants";
import { useScenarioRevisions } from "src/api/harness/scenarios";
import { useDrafts, useSaveDrafts } from "src/api/simulate-environments/versions";
import { draftSaveRequests } from "../drafts/draftSave";
import SourceCompareDialog from "./SourceCompareDialog";
import ValidateScenariosButton from "./ValidateScenariosButton";

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
        direction="row"
        alignItems="center"
        spacing={1.5}
        role="button"
        tabIndex={0}
        onClick={() => setOpen((o) => !o)}
        onKeyDown={onKeyActivate(() => setOpen((o) => !o))}
        sx={{
          px: 3,
          py: 1.25,
          cursor: "pointer",
          "&:hover": { bgcolor: "action.hover" },
        }}
      >
        <Iconify
          icon={
            open
              ? "solar:alt-arrow-down-linear"
              : "solar:alt-arrow-right-linear"
          }
          width={13}
          sx={{ color: "text.subtitle", flexShrink: 0 }}
        />
        <Typography
          noWrap
          sx={{
            typography: "s1",
            fontWeight: "fontWeightRegular",
            fontFamily: "ui-monospace, Menlo, monospace",
            color: "text.primary",
            flexShrink: 0,
          }}
        >
          {s.name || s.title}
        </Typography>
        {s.summary && (
          <Typography
            noWrap
            sx={{
              typography: "s3",
              color: "text.subtitle",
              flex: 1,
              minWidth: 0,
            }}
          >
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
  [
    "Languages",
    (p) => (Array.isArray(p.languages) ? p.languages.join(", ") : p.languages),
  ],
  ["Accent", (p) => p.accent],
  ["Opens with", (p) => p.initialMessage],
];

// What a scenario is, section by section: the brief, its sub-goals, the
// persona, the conversation flow and what it tests: the list view's expanded
// row.
export function ScenarioDetailBody({ row, sx, jobId }) {
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
    ? PERSONA_ROWS.map(([label, read]) => [label, read(s.persona)]).filter(
        ([, value]) => value,
      )
    : [];

  return (
    <Stack
      spacing={3}
      divider={
        <Box sx={{ borderBottom: "1px solid", borderColor: "divider" }} />
      }
      sx={{
        "& p, & li, & > .MuiStack-root > .MuiTypography-root": {
          maxWidth: 780,
        },
        ...sx,
      }}
    >
      {(s.task || s.persona?.name) && (
        <Section title="The person is told">
          <Typography
            sx={{
              typography: "s1",
              color: "text.secondary",
              borderLeft: "1px solid",
              borderColor: "divider",
              pl: 1.5,
            }}
          >
            {s.task} {s.persona?.name && `You are ${s.persona.name}.`}
          </Typography>
        </Section>
      )}

      {steps.length > 0 && (
        <Section
          title={`Sub-goals: the moves that settle it (${steps.length})`}
        >
          <Stack spacing={0.75}>
            {steps.map((st, i) => (
              <Stack
                key={st.id}
                direction="row"
                spacing={1.25}
                alignItems="flex-start"
              >
                <Box
                  sx={{
                    width: 18,
                    height: 18,
                    borderRadius: "50%",
                    flexShrink: 0,
                    mt: "1px",
                    display: "grid",
                    placeItems: "center",
                    bgcolor: "action.selected",
                    color: "text.secondary",
                    typography: "s3",
                    fontWeight: "fontWeightMedium",
                    fontVariantNumeric: "tabular-nums",
                  }}
                >
                  {i + 1}
                </Box>
                <Typography sx={{ typography: "s1", color: "text.secondary" }}>
                  {st.label}
                </Typography>
              </Stack>
            ))}
          </Stack>
        </Section>
      )}

      {persona.length > 0 && (
        <Section title="Persona">
          <Stack spacing={1}>
            {persona.map(([label, value]) => (
              <PolicyRow key={label} label={label}>
                {value}
              </PolicyRow>
            ))}
          </Stack>
        </Section>
      )}

      {(row.conversationBranch || row.branchCategory) && (
        <Section title="Conversation flow">
          <Stack spacing={1}>
            {showCategory && (
              <PolicyRow label="Category">{row.branchCategory}</PolicyRow>
            )}
            {row.conversationBranch && (
              <PolicyRow label="Branch">
                <Typography
                  sx={{
                    typography: "s1",
                    color: "text.primary",
                    wordBreak: "break-word",
                  }}
                >
                  {branchText}
                </Typography>
              </PolicyRow>
            )}
          </Stack>
        </Section>
      )}

      {whatItTests}

      <LifecycleSections row={row} jobId={jobId} />
    </Stack>
  );
}

ScenarioDetailBody.propTypes = {
  row: SCENARIO_SHAPE,
  sx: PropTypes.object,
  jobId: PropTypes.string,
};

// Status, sources, negative controls and revisions: what the lifecycle says about this case and why.
export function LifecycleSections({ row, jobId }) {
  const [comparing, setComparing] = useState(false);
  const lifecycle = row?.lifecycle ?? row?._raw?.lifecycle;
  if (!lifecycle) return null;
  const sources = lifecycle.sources ?? [];
  const controls = lifecycle.proof?.controls ?? [];
  return (
    <>
      <Section title={SCENARIOS_COPY.statusTitle}>
        <Stack spacing={0.5} data-testid="lifecycle-status">
          <Typography sx={{ typography: "s1", color: "text.primary" }}>
            {SCENARIOS_COPY.lifecycle[lifecycle.state] ?? lifecycle.state}
            {lifecycle.proof?.proved_at
              ? ` · ${lifecycle.proof.proved_at}`
              : ""}
          </Typography>
          {lifecycle.reason_text && (
            <Typography sx={{ typography: "s2", color: "text.subtitle" }}>
              {lifecycle.reason_text}
            </Typography>
          )}
          {lifecycle.warning_text && (
            <Typography sx={{ typography: "s2", color: "text.subtitle" }}>
              {lifecycle.warning_text}
            </Typography>
          )}
          {lifecycle.legacy && !lifecycle.warning_text && (
            <Typography sx={{ typography: "s2", color: "text.subtitle" }}>
              {SCENARIOS_COPY.legacyTooltip}
            </Typography>
          )}
          {lifecycle.source_changed?.length > 0 && (
            <Stack direction="row" alignItems="center" spacing={1}>
              <Typography
                sx={{ typography: "s2", color: "text.subtitle" }}
                data-testid="lifecycle-source-changed"
              >
                {SCENARIOS_COPY.sourceChanged(
                  lifecycle.source_changed_version ?? "",
                )}
                :{" "}
                {lifecycle.source_changed
                  .map((ref) => ref || SCENARIOS_COPY.citedRule)
                  .join(", ")}
              </Typography>
              {jobId && lifecycle.source_changed_version_id && (
                <Button
                  size="small"
                  variant="outlined"
                  color="inherit"
                  onClick={() => setComparing(true)}
                >
                  {SCENARIOS_COPY.compare}
                </Button>
              )}
            </Stack>
          )}
          {lifecycle.state === "validated" && jobId && (
            <ActivateButton jobId={jobId} />
          )}
          {jobId && row?.scenarioKey && lifecycle.state !== "archived" && (
            <ValidateScenariosButton
              jobId={jobId}
              keys={[row.scenarioKey]}
              label={SCENARIOS_COPY.validateOne}
            />
          )}
          {comparing && (
            <SourceCompareDialog
              open
              onClose={() => setComparing(false)}
              jobId={jobId}
              versionId={lifecycle.source_changed_version_id}
              sources={sources}
            />
          )}
        </Stack>
      </Section>
      <Section title={SCENARIOS_COPY.sourcesTitle}>
        {sources.length === 0 ? (
          <Typography sx={{ typography: "s2", color: "text.subtitle" }}>
            {SCENARIOS_COPY.noSources}
          </Typography>
        ) : (
          <Stack spacing={1} data-testid="lifecycle-sources">
            {sources.map((source, index) => (
              <Stack
                key={`${source.kind}-${source.ref}-${index}`}
                spacing={0.25}
              >
                <Typography
                  sx={{
                    typography: "s2",
                    color: "text.primary",
                    fontWeight: "fontWeightMedium",
                  }}
                >
                  {source.kind === "assumption"
                    ? SCENARIOS_COPY.assumption
                    : source.label}
                  {source.resolved === false ? ` · ${source.problem}` : ""}
                </Typography>
                {source.quote && (
                  <Typography sx={{ typography: "s2", color: "text.subtitle" }}>
                    “{source.quote}”
                  </Typography>
                )}
              </Stack>
            ))}
          </Stack>
        )}
      </Section>
      {controls.length > 0 && (
        <Section title={SCENARIOS_COPY.controlsTitle}>
          <Stack spacing={0.5} data-testid="lifecycle-controls">
            {controls.map((control) => (
              <PolicyRow
                key={control.name}
                label={SCENARIOS_COPY.controlResult(control)}
              >
                <Typography sx={{ typography: "s2", color: "text.primary" }}>
                  {control.name}: {control.why}
                </Typography>
              </PolicyRow>
            ))}
          </Stack>
        </Section>
      )}
      {jobId && row?.scenarioKey && (
        <Revisions jobId={jobId} scenarioKey={row.scenarioKey} />
      )}
    </>
  );
}

LifecycleSections.propTypes = {
  row: PropTypes.object,
  jobId: PropTypes.string,
};

// Activate is the Save of the unsaved versions this Validated case was proved on: saving them makes
// its cases Active. It starts no run.
function ActivateButton({ jobId }) {
  const { data: drafts } = useDrafts(jobId);
  const save = useSaveDrafts(jobId);
  const nothing = !drafts?.any;
  return (
    <Stack spacing={0.25} alignItems="flex-start" data-testid="lifecycle-activate">
      <Button
        size="small"
        variant="contained"
        disabled={nothing || save.isPending}
        onClick={() => save.mutate(draftSaveRequests(drafts))}
      >
        {SCENARIOS_COPY.activate}
      </Button>
      <Typography sx={{ typography: "s3", color: "text.subtitle" }}>
        {nothing ? SCENARIOS_COPY.activateNothing : SCENARIOS_COPY.activateHint}
      </Typography>
    </Stack>
  );
}

ActivateButton.propTypes = { jobId: PropTypes.string.isRequired };

function Revisions({ jobId, scenarioKey }) {
  const { data } = useScenarioRevisions(jobId, scenarioKey);
  const revisions = data?.revisions ?? [];
  if (revisions.length === 0) return null;
  return (
    <Section title={SCENARIOS_COPY.revisionsTitle}>
      <Stack spacing={0.5} data-testid="lifecycle-revisions">
        {revisions.map((one) => (
          <PolicyRow key={one.digest} label={`Revision ${one.revision}`}>
            <Typography sx={{ typography: "s2", color: "text.primary" }}>
              Suite v{one.suite_versions.join(", v")}
              {one.changed_fields.length
                ? ` · changed ${one.changed_fields.join(", ")}`
                : ""}
            </Typography>
          </PolicyRow>
        ))}
      </Stack>
    </Section>
  );
}

Revisions.propTypes = {
  jobId: PropTypes.string,
  scenarioKey: PropTypes.string,
};

ScenarioDetail.propTypes = {
  row: SCENARIO_SHAPE,
  defaultOpen: PropTypes.bool,
};

function PolicyRow({ label, children }) {
  return (
    <Stack
      direction={{ xs: "column", sm: "row" }}
      spacing={{ xs: 0.5, sm: 2 }}
      sx={{ py: 0.5 }}
    >
      <Typography
        sx={{
          typography: "s1",
          color: "text.subtitle",
          width: { sm: 170 },
          flexShrink: 0,
        }}
      >
        {label}
      </Typography>
      <Box sx={{ flex: 1, minWidth: 0 }}>
        {typeof children === "string" ? (
          <Typography sx={{ typography: "s1", color: "text.primary" }}>
            {children}
          </Typography>
        ) : (
          children
        )}
      </Box>
    </Stack>
  );
}
PolicyRow.propTypes = { label: PropTypes.string, children: PropTypes.node };

function Section({ title, children }) {
  return (
    <Box>
      <Typography
        sx={{
          typography: "s1",
          fontWeight: "fontWeightMedium",
          color: "text.primary",
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
