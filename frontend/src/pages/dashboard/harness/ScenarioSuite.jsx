import {
  Accordion,
  AccordionDetails,
  AccordionSummary,
  Box,
  Button,
  Checkbox,
  Chip,
  Drawer,
  IconButton,
  Paper,
  Stack,
  Typography,
} from "@mui/material";
import PropTypes from "prop-types";
import React, { useEffect, useMemo, useState } from "react";
import { useSnackbar } from "notistack";

import Iconify from "src/components/iconify";
import PersonaCreateEditForm from "src/sections/persona/PersonaCreateEdit/PersonaCreateEditForm";
import { amendHarnessScenarios } from "src/api/harness/harness";

// A use case is how the suite is read, so it is how the suite is shown. The axes a scenario was
// planned against stay internal: they decide what gets written, not how it is grouped here.
const UNGROUPED = "Other scenarios";

const byUseCase = (scenarios) => {
  const groups = new Map();
  scenarios.forEach((scenario) => {
    const key = scenario.use_case?.trim() || UNGROUPED;
    groups.set(key, [...(groups.get(key) || []), scenario]);
  });
  return [...groups.entries()].sort(([a], [b]) => a.localeCompare(b));
};

const readable = (name) => String(name || "").replace(/[_-]+/g, " ").trim();

// The harness answers each change separately, so the summary counts outcomes rather than claiming
// a single verdict for the batch. A rework that touched files is worth saying out loud.
const summarise = (receipts) => {
  const counts = receipts.reduce(
    (totals, one) => ({ ...totals, [one.outcome]: (totals[one.outcome] || 0) + 1 }),
    {},
  );
  const parts = [];
  if (counts.applied) parts.push(`${counts.applied} applied`);
  if (counts.reworked) parts.push(`${counts.reworked} reworked`);
  if (counts.queued) parts.push(`${counts.queued} being re-checked`);
  if (counts.refused) parts.push(`${counts.refused} refused`);
  return parts.join(", ") || "nothing changed";
};

export default function ScenarioSuite({ scenarios, jobId, editable, onChanged }) {
  const { enqueueSnackbar } = useSnackbar();
  const [selected, setSelected] = useState(() => new Set());
  const [editing, setEditing] = useState(null);
  const [busy, setBusy] = useState(false);
  const [waiting, setWaiting] = useState(false);

  const groups = useMemo(() => byUseCase(scenarios), [scenarios]);

  // While the harness is re-checking, ask the job again on a slow interval. A rework is a model
  // session and a proof, so seconds are the right unit; stop as soon as the suite we were handed
  // differs from the one we asked about.
  const fingerprint = useMemo(
    () => scenarios.map((one) => `${one.name}:${one.sub_goals?.length ?? 0}`).join("|"),
    [scenarios],
  );
  const seenRef = React.useRef(fingerprint);
  useEffect(() => {
    if (!waiting) {
      seenRef.current = fingerprint;
      return undefined;
    }
    if (fingerprint !== seenRef.current) {
      setWaiting(false);
      enqueueSnackbar("The suite has been re-checked", { variant: "success" });
      return undefined;
    }
    const timer = setInterval(() => onChanged?.(), 6000);
    return () => clearInterval(timer);
  }, [waiting, fingerprint, onChanged, enqueueSnackbar]);

  const toggle = (name) =>
    setSelected((prev) => {
      const next = new Set(prev);
      if (next.has(name)) next.delete(name);
      else next.add(name);
      return next;
    });

  const toggleGroup = (rows, allOn) =>
    setSelected((prev) => {
      const next = new Set(prev);
      rows.forEach((row) => (allOn ? next.delete(row.name) : next.add(row.name)));
      return next;
    });

  const send = async (changes, { rework }) => {
    setBusy(true);
    try {
      const reply = await amendHarnessScenarios(jobId, changes, { rework });
      const receipts = reply?.receipts || [];
      const refused = receipts.filter((one) => one.outcome === "refused");
      const queued = receipts.filter((one) => one.outcome === "queued");
      enqueueSnackbar(summarise(receipts), {
        variant: refused.length ? "warning" : "success",
      });
      // A queued change is still running in the harness. Poll until the suite itself changes,
      // because the receipt is a promise and the suite is the answer.
      if (queued.length) setWaiting(true);
      // A refusal carries the reason the harness gave. Showing it is the difference between "that
      // did not work" and knowing which change to send differently.
      refused.forEach((one) =>
        enqueueSnackbar(`${readable(one.scenario)}: ${one.why}`, { variant: "warning" }),
      );
      setSelected(new Set());
      setEditing(null);
      onChanged?.();
    } catch (error) {
      // The harness names the offending op or field; anything else is ours to keep off screen.
      const detail = error?.response?.data?.detail;
      enqueueSnackbar(detail || "The suite could not be edited", { variant: "error" });
    } finally {
      setBusy(false);
    }
  };

  const deleteSelected = () =>
    send(
      [...selected].map((name) => ({ op: "drop", scenario: name })),
      { rework: false },
    );

  const savePersona = (payload) =>
    send(
      [
        {
          op: "set_persona",
          scenario: editing.name,
          persona: {
            name: payload.name,
            gender: payload.gender,
            age_group: payload.age_group,
            occupation: payload.profession,
            location: payload.location,
            personality: payload.personality,
            communication_style: payload.communication_style,
            accent: payload.accent,
            languages: payload.languages,
          },
        },
      ],
      // A persona can change what the world must seed and what a correct agent does, so the
      // harness is allowed to work that out and re-prove the scenario.
      { rework: true },
    );

  if (!scenarios.length) {
    return (
      <Typography variant="body2" color="text.secondary">
        No scenarios yet.
      </Typography>
    );
  }

  return (
    <Stack spacing={1.5}>
      {waiting && (
        <Typography variant="caption" color="text.secondary">
          The harness is re-checking a scenario, rewriting its setup and checks where the change
          matters and proving it again. This takes a minute or two.
        </Typography>
      )}

      {selected.size > 0 && (
        <Stack direction="row" spacing={1} alignItems="center">
          <Typography variant="body2">{selected.size} selected</Typography>
          <Button
            size="small"
            color="error"
            variant="outlined"
            disabled={busy}
            startIcon={<Iconify icon="eva:trash-2-outline" width={16} />}
            onClick={deleteSelected}
          >
            Delete
          </Button>
          <Button size="small" disabled={busy} onClick={() => setSelected(new Set())}>
            Clear
          </Button>
        </Stack>
      )}

      {groups.map(([useCase, rows]) => {
        const allOn = rows.every((row) => selected.has(row.name));
        const someOn = !allOn && rows.some((row) => selected.has(row.name));
        return (
          <Box key={useCase}>
            <Stack direction="row" spacing={1} alignItems="center" sx={{ mb: 0.5 }}>
              <Checkbox
                size="small"
                checked={allOn}
                indeterminate={someOn}
                onChange={() => toggleGroup(rows, allOn)}
              />
              <Typography variant="subtitle2">{useCase}</Typography>
              <Chip size="small" label={rows.length} variant="outlined" />
            </Stack>

            <Stack spacing={0.75} sx={{ pl: 1 }}>
              {rows.map((scenario) => (
                <ScenarioRow
                  key={scenario.name}
                  scenario={scenario}
                  checked={selected.has(scenario.name)}
                  onToggle={() => toggle(scenario.name)}
                  onEditPersona={editable ? () => setEditing(scenario) : null}
                />
              ))}
            </Stack>
          </Box>
        );
      })}

      <Drawer anchor="right" open={Boolean(editing)} onClose={() => setEditing(null)}>
        <Box sx={{ width: "96vw", maxWidth: 720, p: 2 }}>
          <Typography variant="subtitle1" sx={{ mb: 1 }}>
            {editing ? readable(editing.name) : ""}
          </Typography>
          <Typography variant="caption" color="text.secondary">
            Saving re-checks this scenario. Where the change affects what the world holds or what a
            correct agent does, its setup and checks are rewritten and proved again.
          </Typography>
          {editing && (
            <PersonaCreateEditForm
              editPersona={editing.persona || {}}
              onCancel={() => setEditing(null)}
              onSave={savePersona}
            />
          )}
        </Box>
      </Drawer>
    </Stack>
  );
}

function ScenarioRow({ scenario, checked, onToggle, onEditPersona }) {
  return (
    <Paper variant="outlined" sx={{ bgcolor: "background.default" }}>
      <Accordion
        variant="outlined"
        disableGutters
        sx={{ bgcolor: "transparent", "&:before": { display: "none" } }}
      >
        <AccordionSummary expandIcon={<Iconify icon="eva:arrow-ios-downward-fill" width={16} />}>
          <Stack direction="row" spacing={1} alignItems="center" sx={{ minWidth: 0, flex: 1 }}>
            <Checkbox
              size="small"
              checked={checked}
              // The row expands on click; the checkbox must not, or selecting one opens it too.
              onClick={(event) => event.stopPropagation()}
              onChange={onToggle}
            />
            <Box sx={{ minWidth: 0, flex: 1 }}>
              <Typography variant="body2" noWrap>
                {readable(scenario.name)}
              </Typography>
              {scenario.tests && (
                <Typography variant="caption" color="text.secondary" noWrap display="block">
                  {scenario.tests}
                </Typography>
              )}
            </Box>
            {onEditPersona && scenario.persona && (
              <IconButton
                size="small"
                aria-label="Edit persona"
                onClick={(event) => {
                  event.stopPropagation();
                  onEditPersona();
                }}
              >
                <Iconify icon="eva:edit-outline" width={16} />
              </IconButton>
            )}
          </Stack>
        </AccordionSummary>
        <AccordionDetails>
          <Stack spacing={1}>
            <Typography variant="body2">{scenario.instruction}</Typography>
            {scenario.persona?.name && (
              <Typography variant="caption" color="text.secondary">
                {[
                  scenario.persona.name,
                  scenario.persona.age_group,
                  scenario.persona.accent,
                  scenario.persona.location,
                ]
                  .filter(Boolean)
                  .join(" · ")}
              </Typography>
            )}
            {Boolean(scenario.sub_goals?.length) && (
              <Stack direction="row" spacing={0.5} flexWrap="wrap" useFlexGap>
                {scenario.sub_goals.map((goal) => (
                  <Chip key={goal} size="small" label={goal} variant="outlined" />
                ))}
              </Stack>
            )}
          </Stack>
        </AccordionDetails>
      </Accordion>
    </Paper>
  );
}

ScenarioRow.propTypes = {
  scenario: PropTypes.object.isRequired,
  checked: PropTypes.bool,
  onToggle: PropTypes.func,
  onEditPersona: PropTypes.func,
};

ScenarioSuite.propTypes = {
  scenarios: PropTypes.arrayOf(PropTypes.object),
  jobId: PropTypes.string,
  // An agent that talks to nobody has no persona to edit, so the affordance is not shown at all.
  editable: PropTypes.bool,
  onChanged: PropTypes.func,
};
