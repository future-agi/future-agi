import {
  Box,
  Button,
  Checkbox,
  Chip,
  Drawer,
  IconButton,
  Stack,
  Table,
  TableBody,
  TableCell,
  TableContainer,
  TableHead,
  TableRow,
  Tooltip,
  Typography,
} from "@mui/material";
import { alpha } from "@mui/material/styles";
import PropTypes from "prop-types";
import React, { useEffect, useMemo, useState } from "react";
import { useSnackbar } from "notistack";

import Iconify from "src/components/iconify";
import ScenarioEditForm from "./ScenarioEditForm";
import { amendHarnessScenarios } from "src/api/harness/harness";

// A use case is how the suite is read, so it is how the suite is shown. The axes a scenario was
// planned against stay internal: they decide what gets written, not how it is grouped here.
const UNGROUPED = "Other scenarios";

// One scenario per line, the derived parts as columns, so a suite is comparable without opening
// anything. The number is the row's own handle: it is what a person names when they say which rows
// to act on.
const selectableCheckboxSx = {
  p: 0,
  color: "text.disabled",
  "&.Mui-checked": { color: "text.primary" },
  "&.MuiCheckbox-indeterminate": { color: "text.primary" },
};

const COLUMNS = [
  "select",
  "#",
  "Scenario",
  "Persona",
  "Situation",
  "Sub-goals",
  "Passes when",
  "",
];

// Grouped on the contract's use cases rather than only on what the scenarios claim, so a use case
// nobody wrote a scenario for still appears. Hiding it would hide a coverage gap. A scenario whose
// use case matches none of them is kept in its own group and marked, because a paraphrase should be
// visible rather than quietly sitting beside the real ones.
const byUseCase = (scenarios, useCases = []) => {
  const groups = new Map();
  useCases.forEach((one) => {
    const key = String(one || "").trim();
    if (key) groups.set(key, []);
  });
  scenarios.forEach((scenario) => {
    const key = scenario.use_case?.trim() || UNGROUPED;
    groups.set(key, [...(groups.get(key) || []), scenario]);
  });
  const known = new Set(useCases.map((one) => String(one || "").trim()).filter(Boolean));
  return [...groups.entries()]
    .map(([useCase, rows]) => ({
      useCase,
      rows,
      covered: rows.length > 0,
      matched: known.size === 0 || known.has(useCase) || useCase === UNGROUPED,
    }))
    .sort((a, b) => a.useCase.localeCompare(b.useCase));
};

const matches = (scenario, chosen) =>
  !chosen.size || (scenario.persona?.keywords || []).some((word) => chosen.has(word));

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

export default function ScenarioSuite({ scenarios, jobId, editable, useCases, onChanged }) {
  const { enqueueSnackbar } = useSnackbar();
  const [selected, setSelected] = useState(() => new Set());
  const [editing, setEditing] = useState(null);
  const [busy, setBusy] = useState(false);
  const [waiting, setWaiting] = useState(false);

  const [chosen, setChosen] = useState(() => new Set());
  const [opened, setOpened] = useState(() => new Set());
  const toggleOpen = (name) =>
    setOpened((prev) => {
      const next = new Set(prev);
      if (next.has(name)) next.delete(name);
      else next.add(name);
      return next;
    });

  // Every keyword in the suite, most used first, so the row reads as the suite's own vocabulary.
  const keywords = useMemo(() => {
    const seen = new Map();
    scenarios.forEach((one) =>
      (one.persona?.keywords || []).forEach((word) => seen.set(word, (seen.get(word) || 0) + 1)),
    );
    return [...seen.entries()].sort((a, b) => b[1] - a[1] || a[0].localeCompare(b[0]));
  }, [scenarios]);

  const shown = useMemo(
    () => scenarios.filter((one) => matches(one, chosen)),
    [scenarios, chosen],
  );
  const allSelected = shown.length > 0 && shown.every((one) => selected.has(one.name));
  const someSelected = !allSelected && shown.some((one) => selected.has(one.name));
  const toggleAll = () =>
    setSelected((prev) => {
      const next = new Set(prev);
      shown.forEach((one) => (allSelected ? next.delete(one.name) : next.add(one.name)));
      return next;
    });
  const groups = useMemo(() => byUseCase(shown, useCases), [shown, useCases]);

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

  // One edit, two kinds of change. The descriptive fields are written straight to the scenario;
  // the persona may still turn out to matter for this agent, and the harness decides that, not us.
  const saveScenario = (form) => {
    const target = editing.name;
    const changes = [
      { op: "set_field", scenario: target, field: "tests", value: form.tests },
      { op: "set_field", scenario: target, field: "branch", value: form.branch },
      {
        op: "set_field",
        scenario: target,
        field: "background_noise",
        value: form.background_noise,
      },
    ];
    if (editing.persona) {
      changes.push({
        op: "set_persona",
        scenario: target,
        persona: {
          keywords: form.keywords,
          personality: form.personality,
          communication_style: form.communication_style,
          accent: form.accent,
          languages: form.languages,
          occupation: form.occupation,
          location: form.location,
        },
      });
    }
    send(changes, { rework: true });
  };

  if (!scenarios.length) {
    return (
      <Typography variant="body2" color="text.secondary">
        No scenarios yet.
      </Typography>
    );
  }

  let counter = 0;

  return (
    <Stack spacing={1.5}>
      {waiting && (
        <Typography variant="caption" color="text.secondary">
          The harness is re-checking a scenario, rewriting its setup and checks where the change
          matters and proving it again. This takes a minute or two.
        </Typography>
      )}

      {keywords.length > 0 && (
        <Stack direction="row" gap={0.75} flexWrap="wrap" alignItems="center">
          {keywords.map(([word, count]) => (
            <Chip
              key={word}
              size="small"
              label={`${word} ${count}`}
              variant={chosen.has(word) ? "filled" : "outlined"}
              color={chosen.has(word) ? "primary" : "default"}
              onClick={() =>
                setChosen((prev) => {
                  const next = new Set(prev);
                  if (next.has(word)) next.delete(word);
                  else next.add(word);
                  return next;
                })
              }
            />
          ))}
          {chosen.size > 0 && (
            <Button size="small" onClick={() => setChosen(new Set())}>
              Clear filter
            </Button>
          )}
        </Stack>
      )}

      {selected.size > 0 && (
        <Stack direction="row" spacing={1} alignItems="center">
          <Typography variant="body2">{selected.size} selected</Typography>
          <Button
            size="small"
            color="error"
            variant="outlined"
            disabled={busy}
            startIcon={<Iconify icon="solar:trash-bin-trash-linear" width={16} />}
            onClick={deleteSelected}
          >
            Delete
          </Button>
          <Button size="small" disabled={busy} onClick={() => setSelected(new Set())}>
            Clear
          </Button>
        </Stack>
      )}

      <TableContainer>
        <Table size="small" sx={{ minWidth: 1100 }}>
          <TableHead>
            <TableRow>
              {COLUMNS.map((head, index) => {
                const isActions = index === COLUMNS.length - 1;
                const isSelect = head === "select";
                return (
                  <TableCell
                    key={head || index}
                    align={isActions ? "right" : "left"}
                    padding={isSelect ? "checkbox" : "normal"}
                    sx={{
                      typography: "s3",
                      fontWeight: 700,
                      textTransform: "uppercase",
                      letterSpacing: 0.4,
                      color: "text.subtitle",
                      bgcolor: "background.neutral",
                      borderBottom: "1px solid",
                      borderColor: "divider",
                      whiteSpace: "nowrap",
                      ...(head === "#" && { width: 44 }),
                      ...(isSelect && { width: 44, pl: 1.5 }),
                      // Pinned, so the edit control stays reachable however far the situation and
                      // passes-when columns push the table. The shadow keeps it reading as one
                      // column rather than as content that happens to be at the edge.
                      ...(isActions && {
                        position: "sticky",
                        right: 0,
                        zIndex: 2,
                        width: 64,
                        minWidth: 64,
                        boxShadow: (theme) =>
                          `-8px 0 12px -6px ${alpha(
                            theme.palette.common.black,
                            theme.palette.mode === "dark" ? 0.45 : 0.08,
                          )}`,
                      }),
                    }}
                  >
                    {isSelect ? (
                      <Checkbox
                        size="small"
                        checked={allSelected}
                        indeterminate={someSelected}
                        onChange={toggleAll}
                        sx={selectableCheckboxSx}
                      />
                    ) : (
                      head
                    )}
                  </TableCell>
                );
              })}
            </TableRow>
          </TableHead>

          <TableBody>
            {groups.map(({ useCase, rows, covered, matched }) => {
              const allOn = rows.length > 0 && rows.every((row) => selected.has(row.name));
              const someOn = !allOn && rows.some((row) => selected.has(row.name));
              return (
                <React.Fragment key={useCase}>
                  <TableRow>
                    <TableCell
                      colSpan={COLUMNS.length}
                      sx={{
                        bgcolor: (theme) =>
                          alpha(theme.palette.text.primary, theme.palette.mode === "dark" ? 0.08 : 0.05),
                        py: 1,
                      }}
                    >
                      <Stack direction="row" spacing={1.25} alignItems="center">
                        <Checkbox
                          size="small"
                          checked={allOn}
                          indeterminate={someOn}
                          disabled={!rows.length}
                          onChange={() => toggleGroup(rows, allOn)}
                          sx={selectableCheckboxSx}
                        />
                        <Typography
                          sx={{ typography: "s2", fontWeight: 700, flex: 1, minWidth: 0 }}
                        >
                          {useCase}
                        </Typography>
                        <Typography
                          sx={{
                            typography: "s3",
                            fontWeight: 700,
                            color: "text.subtitle",
                            fontVariantNumeric: "tabular-nums",
                          }}
                        >
                          {rows.length} {rows.length === 1 ? "scenario" : "scenarios"}
                        </Typography>
                        {!covered && (
                          <Chip size="small" color="error" variant="outlined" label="not covered" />
                        )}
                        {!matched && (
                          <Chip size="small" color="warning" variant="outlined" label="unmatched" />
                        )}
                      </Stack>
                    </TableCell>
                  </TableRow>

                  {!covered && (
                    <TableRow>
                      <TableCell colSpan={COLUMNS.length}>
                        <Typography variant="caption" color="text.secondary">
                          No scenarios were written for this use case.
                        </Typography>
                      </TableCell>
                    </TableRow>
                  )}

                  {rows.map((scenario) => {
                    counter += 1;
                    const persona = scenario.persona || {};
                    const who = [persona.gender, persona.age_group].filter(Boolean).join(" \u00b7 ");
                    return (
                      <React.Fragment key={scenario.name}>
                      <TableRow hover>
                        <TableCell padding="checkbox">
                          <Checkbox
                            size="small"
                            checked={selected.has(scenario.name)}
                            onChange={() => toggle(scenario.name)}
                            sx={selectableCheckboxSx}
                          />
                        </TableCell>
                        <TableCell
                          sx={{
                            typography: "s3",
                            color: "text.subtitle",
                            fontVariantNumeric: "tabular-nums",
                            verticalAlign: "top",
                          }}
                        >
                          {counter}
                        </TableCell>
                        <TableCell sx={{ maxWidth: 260, verticalAlign: "top" }}>
                          <Typography noWrap sx={{ typography: "s2", fontWeight: 600 }}>
                            {readable(scenario.name)}
                          </Typography>
                          <Tooltip title={scenario.branch || ""}>
                            <Typography noWrap sx={{ typography: "s3", color: "text.subtitle" }}>
                              {scenario.branch}
                            </Typography>
                          </Tooltip>
                        </TableCell>
                        <TableCell sx={{ maxWidth: 170, verticalAlign: "top" }}>
                          <Typography noWrap sx={{ typography: "s2" }}>
                            {persona.name}
                          </Typography>
                          <Typography noWrap sx={{ typography: "s3", color: "text.subtitle" }}>
                            {who}
                          </Typography>
                        </TableCell>
                        <TableCell sx={{ maxWidth: 300, verticalAlign: "top" }}>
                          <Clamped text={scenario.instruction} />
                        </TableCell>
                        <TableCell sx={{ maxWidth: 220, verticalAlign: "top" }}>
                          <Stack direction="row" gap={0.5} flexWrap="wrap">
                            {(scenario.sub_goals || []).map((goal) => (
                              <Chip key={goal} size="small" variant="outlined" label={readable(goal)} />
                            ))}
                          </Stack>
                        </TableCell>
                        <TableCell sx={{ maxWidth: 280, verticalAlign: "top" }}>
                          <Clamped text={scenario.tests} />
                        </TableCell>
                        <TableCell
                          align="right"
                          sx={{
                            position: "sticky",
                            right: 0,
                            bgcolor: "background.default",
                            verticalAlign: "top",
                          }}
                        >
                          <Stack direction="row" spacing={0.25} justifyContent="flex-end">
                            {editable && (
                              <Tooltip title="Edit">
                                <IconButton size="small" onClick={() => setEditing(scenario)}>
                                  <Iconify icon="solar:pen-new-square-linear" width={16} />
                                </IconButton>
                              </Tooltip>
                            )}
                            <Tooltip title={opened.has(scenario.name) ? "Hide detail" : "Show detail"}>
                              <IconButton size="small" onClick={() => toggleOpen(scenario.name)}>
                                <Iconify
                                  icon={
                                    opened.has(scenario.name)
                                      ? "solar:alt-arrow-up-linear"
                                      : "solar:alt-arrow-down-linear"
                                  }
                                  width={16}
                                />
                              </IconButton>
                            </Tooltip>
                          </Stack>
                        </TableCell>
                      </TableRow>
                      {opened.has(scenario.name) && (
                        <TableRow>
                          <TableCell colSpan={COLUMNS.length} sx={{ bgcolor: "background.neutral" }}>
                            <Detail scenario={scenario} />
                          </TableCell>
                        </TableRow>
                      )}
                      </React.Fragment>
                    );
                  })}
                </React.Fragment>
              );
            })}
          </TableBody>
        </Table>
      </TableContainer>

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
            <ScenarioEditForm
              scenario={editing}
              busy={busy}
              onCancel={() => setEditing(null)}
              onSave={saveScenario}
            />
          )}
        </Box>
      </Drawer>
    </Stack>
  );
}

// Everything the columns do not have room for. A scenario carries more than fits on one line, and
// leaving the rest unreachable would mean the tab shows a summary of the suite rather than the
// suite. Fields a given agent does not use stay out rather than showing as blank rows.
function Detail({ scenario }) {
  const persona = scenario.persona || {};
  const caller = [
    ["Personality", persona.personality],
    ["Communication style", persona.communication_style],
    ["Accent", persona.accent],
    ["Profession", persona.occupation],
    ["Location", persona.location],
    ["Languages", (persona.languages || []).join(", ")],
  ].filter(([, value]) => value);

  const run = [
    ["Background noise", scenario.background_noise ? "On" : "Off"],
    ["Max turns", scenario.max_turns],
    ["Call direction", scenario.call_direction],
    ["Answered by", scenario.answered_by],
  ].filter(([, value]) => value !== "" && value !== undefined && value !== null);

  const seeded = Object.entries(scenario.fixture || {}).filter(([key]) => key !== "origin");

  return (
    <Stack spacing={2} sx={{ py: 1.5 }}>
      {persona.initial_message && (
        <Field label="Opens with">
          <Typography sx={{ typography: "s3" }}>{persona.initial_message}</Typography>
        </Field>
      )}

      {Boolean((persona.keywords || []).length) && (
        <Field label="Keywords">
          <Stack direction="row" gap={0.5} flexWrap="wrap">
            {persona.keywords.map((word) => (
              <Chip key={word} size="small" variant="outlined" label={word} />
            ))}
          </Stack>
        </Field>
      )}

      {Boolean(caller.length) && (
        <Field label="Caller">
          <Stack direction="row" gap={2} flexWrap="wrap">
            {caller.map(([label, value]) => (
              <Pair key={label} label={label} value={value} />
            ))}
          </Stack>
        </Field>
      )}

      {Boolean(run.length) && (
        <Field label="Run conditions">
          <Stack direction="row" gap={2} flexWrap="wrap">
            {run.map(([label, value]) => (
              <Pair key={label} label={label} value={String(value)} />
            ))}
          </Stack>
        </Field>
      )}

      {Boolean(seeded.length) && (
        <Field label="Seeded into the world">
          <Stack direction="row" gap={2} flexWrap="wrap">
            {seeded.map(([key, value]) => (
              <Pair key={key} label={readable(key)} value={String(value)} />
            ))}
          </Stack>
        </Field>
      )}

      {Boolean((scenario.solution || []).length) && (
        <Field label="Known-good solution">
          <Stack direction="row" gap={0.5} flexWrap="wrap" alignItems="center">
            {scenario.solution.map((step, index) => (
              <Chip
                key={`${step.tool}-${index}`}
                size="small"
                variant="outlined"
                label={`${index + 1}. ${step.tool}`}
              />
            ))}
          </Stack>
        </Field>
      )}
    </Stack>
  );
}

Detail.propTypes = { scenario: PropTypes.object.isRequired };

function Field({ label, children }) {
  return (
    <Stack spacing={0.5}>
      <Typography
        sx={{
          typography: "s3",
          fontWeight: 700,
          textTransform: "uppercase",
          letterSpacing: 0.4,
          color: "text.subtitle",
        }}
      >
        {label}
      </Typography>
      {children}
    </Stack>
  );
}

Field.propTypes = { label: PropTypes.string, children: PropTypes.node };

function Pair({ label, value }) {
  return (
    <Stack spacing={0.25}>
      <Typography sx={{ typography: "s3", color: "text.subtitle" }}>{label}</Typography>
      <Typography sx={{ typography: "s3" }}>{value}</Typography>
    </Stack>
  );
}

Pair.propTypes = { label: PropTypes.string, value: PropTypes.string };

// Two lines, then ellipsis, with the whole value on hover. A situation can run to a paragraph and
// a table that lets one row grow to five lines stops being scannable.
function Clamped({ text }) {
  const value = String(text || "");
  return (
    <Tooltip title={value}>
      <Typography
        sx={{
          typography: "s3",
          display: "-webkit-box",
          WebkitLineClamp: 2,
          WebkitBoxOrient: "vertical",
          overflow: "hidden",
        }}
      >
        {value}
      </Typography>
    </Tooltip>
  );
}

Clamped.propTypes = { text: PropTypes.string };

ScenarioSuite.propTypes = {
  scenarios: PropTypes.arrayOf(PropTypes.object),
  jobId: PropTypes.string,
  // An agent that talks to nobody has no persona to edit, so the affordance is not shown at all.
  editable: PropTypes.bool,
  // The contract's own use cases, so one nobody wrote a scenario for still shows as a gap.
  useCases: PropTypes.arrayOf(PropTypes.string),
  onChanged: PropTypes.func,
};
