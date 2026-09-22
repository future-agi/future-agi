import {
  Box,
  Button,
  Checkbox,
  Chip,
  Drawer,
  IconButton,
  MenuItem,
  Stack,
  Table,
  TableBody,
  TableCell,
  TableContainer,
  TableHead,
  TableRow,
  TextField,
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

// The design greys these controls out and says "Fork this environment to edit." We have no fork,
// so the reason has to be the one that is actually true here: a suite read outside a run has no
// job to amend against. Saying it in the tooltip beats a control that silently disappears.
const lockedReason = "Open this suite from its run to edit it";

const COLUMNS = [
  "select",
  "#",
  "Scenario",
  "Persona",
  "Levers",
  "Situation",
  "Sub-goals",
  "Passes when",
  "",
];

// Grouped on the use case each scenario claims, which is how a suite is read. Nothing else is
// listed: a contract carries use cases the generator never wrote a scenario for, often because they
// describe how the agent talks rather than a task worth testing, and showing those as empty rows
// buried the scenarios under things that were never missing.
const byUseCase = (scenarios) => {
  const groups = new Map();
  scenarios.forEach((scenario) => {
    const key = scenario.use_case?.trim() || UNGROUPED;
    groups.set(key, [...(groups.get(key) || []), scenario]);
  });
  return [...groups.entries()]
    .map(([useCase, rows]) => ({ useCase, rows }))
    .sort((a, b) => a.useCase.localeCompare(b.useCase));
};

const matches = (scenario, chosen) =>
  !chosen.size || (scenario.persona?.keywords || []).some((word) => chosen.has(word));

// The axes a suite can be filtered by, taken from the scenarios' own coordinates. A level count of
// one is dropped, since a filter every row matches is not one.
const axesOf = (scenarios) => {
  const levels = new Map();
  scenarios.forEach((one) =>
    Object.entries(one?.coverage || {}).forEach(([axis, level]) => {
      const value = String(level ?? "").trim();
      if (!value) return;
      if (!levels.has(axis)) levels.set(axis, new Map());
      const counts = levels.get(axis);
      counts.set(value, (counts.get(value) || 0) + 1);
    }),
  );
  return [...levels.entries()]
    .filter(([, counts]) => counts.size > 1)
    .map(([axis, counts]) => ({
      axis,
      levels: [...counts.entries()].sort((a, b) => b[1] - a[1] || a[0].localeCompare(b[0])),
    }))
    .sort((a, b) => a.axis.localeCompare(b.axis));
};

const onAxes = (scenario, picked) =>
  Object.entries(picked).every(([axis, level]) => {
    if (!level) return true;
    const held = String((scenario?.coverage || {})[axis] ?? "").trim();
    return !held || held === level;
  });

// Searched over what is on screen plus the situation, because a person looking for "refund" is as
// likely to remember the wording of the task as the name it was filed under.
const found = (scenario, query) => {
  const needle = query.trim().toLowerCase();
  if (!needle) return true;
  return [
    scenario.name,
    scenario.use_case,
    scenario.branch,
    scenario.tests,
    scenario.instruction,
    scenario.persona?.name,
  ]
    .filter(Boolean)
    .some((value) => String(value).toLowerCase().includes(needle));
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

export default function ScenarioSuite({ scenarios, jobId, editable, scenarioEditing, onChanged }) {
  const { enqueueSnackbar } = useSnackbar();
  const [selected, setSelected] = useState(() => new Set());
  const [editing, setEditing] = useState(null);
  const [busy, setBusy] = useState(false);
  const [waiting, setWaiting] = useState(false);

  const [chosen, setChosen] = useState(() => new Set());
  const [query, setQuery] = useState("");
  const [picked, setPicked] = useState({});

  const axes = useMemo(() => axesOf(scenarios), [scenarios]);

  // Every keyword in the suite, most used first, so the row reads as the suite's own vocabulary.
  // Keywords are written onto each scenario at generation, so this list is the suite's, not a fixed
  // vocabulary somebody has to maintain. The count is on the chip because it is the only honest
  // measure of whether a filter is worth clicking.
  const keywords = useMemo(() => {
    const seen = new Map();
    scenarios.forEach((one) =>
      (one.persona?.keywords || []).forEach((word) => seen.set(word, (seen.get(word) || 0) + 1)),
    );
    return [...seen.entries()].sort((a, b) => b[1] - a[1] || a[0].localeCompare(b[0]));
  }, [scenarios]);

  // Leads with the keywords that narrow the suite; a one-row chip is a note, not a filter.
  const WORTH_CLICKING = 16;
  const [allKeywords, setAllKeywords] = useState(false);
  const visibleKeywords = useMemo(() => {
    if (allKeywords || keywords.length <= WORTH_CLICKING) return keywords;
    const filtering = keywords.filter(([, count]) => count > 1);
    const head = (filtering.length >= WORTH_CLICKING ? filtering : keywords).slice(
      0,
      WORTH_CLICKING,
    );
    // A chosen keyword never hides, or clearing it becomes impossible.
    return [...head, ...keywords.filter(([word]) => chosen.has(word) && !head.some(([one]) => one === word))];
  }, [keywords, allKeywords, chosen]);

  const shown = useMemo(
    () =>
      scenarios.filter(
        (one) => matches(one, chosen) && found(one, query) && onAxes(one, picked),
      ),
    [scenarios, chosen, query, picked],
  );
  const allSelected = shown.length > 0 && shown.every((one) => selected.has(one.name));
  const someSelected = !allSelected && shown.some((one) => selected.has(one.name));
  const toggleAll = () =>
    setSelected((prev) => {
      const next = new Set(prev);
      shown.forEach((one) => (allSelected ? next.delete(one.name) : next.add(one.name)));
      return next;
    });
  const groups = useMemo(() => byUseCase(shown), [shown]);

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

  const dropOne = (name) => send([{ op: "drop", scenario: name }], { rework: false });

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
      { op: "set_field", scenario: target, field: "max_turns", value: form.max_turns },
      {
        op: "set_field",
        scenario: target,
        field: "background_noise",
        // The form names a place, and off is the absence of one. The harness stores either a place
        // or false, so it goes over the wire the way it is stored.
        value: form.background_noise === "off" ? false : form.background_noise,
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

  // The number belongs to the scenario, not to the row it happens to be drawn on. Numbering the
  // rendered rows renumbered the suite from 1 every time a filter narrowed it, so the same
  // scenario answered to a different number depending on what else was on screen.
  const numbers = useMemo(
    () => new Map(scenarios.map((one, index) => [one.name, index + 1])),
    [scenarios],
  );

  if (!scenarios.length) {
    return (
      <Typography variant="body2" color="text.secondary">
        No scenarios yet.
      </Typography>
    );
  }

  return (
    <Stack spacing={1.5} sx={{ minWidth: 0 }}>
      {waiting && (
        <Typography variant="caption" color="text.secondary">
          The harness is re-checking a scenario, rewriting its setup and checks where the change
          matters and proving it again. This takes a minute or two.
        </Typography>
      )}

      <Stack direction="row" alignItems="center" spacing={1}>
        <TextField
          size="small"
          value={query}
          onChange={(event) => setQuery(event.target.value)}
          placeholder="Search scenarios by name, task or use case"
          InputProps={{
            sx: { typography: "s2" },
            startAdornment: (
              <Box sx={{ pr: 0.75, pl: 0.25, display: "flex", color: "text.subtitle" }}>
                <Iconify icon="solar:magnifer-linear" width={14} />
              </Box>
            ),
          }}
          sx={{ maxWidth: 380, flex: 1 }}
        />
        <Typography
          sx={{
            typography: "s3",
            color: "text.subtitle",
            fontVariantNumeric: "tabular-nums",
            whiteSpace: "nowrap",
          }}
        >
          {shown.length} of {scenarios.length}
        </Typography>
      </Stack>

      {axes.length > 0 && (
        <Stack direction="row" gap={1} flexWrap="wrap" alignItems="center">
          {axes.map(({ axis, levels }) => (
            <TextField
              key={axis}
              select
              size="small"
              label={String(axis).replace(/[_-]+/g, " ")}
              value={picked[axis] || ""}
              onChange={(event) =>
                setPicked((prev) => ({ ...prev, [axis]: event.target.value }))
              }
              sx={{ minWidth: 190 }}
            >
              <MenuItem value="">
                Any {String(axis).replace(/[_-]+/g, " ")}
              </MenuItem>
              {levels.map(([level, count]) => (
                <MenuItem key={level} value={level}>
                  {level} ({count})
                </MenuItem>
              ))}
            </TextField>
          ))}
          {Object.values(picked).some(Boolean) && (
            <Chip
              label="Any cell"
              size="small"
              variant="outlined"
              onClick={() => setPicked({})}
              onDelete={() => setPicked({})}
              sx={{ fontSize: "11px", height: 26, cursor: "pointer" }}
            />
          )}
        </Stack>
      )}

      {keywords.length > 0 && (
        <Stack direction="row" gap={0.5} flexWrap="wrap" alignItems="center">
          {visibleKeywords.map(([word, count]) => {
            const on = chosen.has(word);
            return (
              <Chip
                key={word}
                icon={<Iconify icon="solar:tag-linear" width={14} />}
                label={`${word} ${count}`}
                size="small"
                variant={on ? "filled" : "outlined"}
                color={on ? "primary" : "default"}
                onClick={() =>
                  setChosen((prev) => {
                    const next = new Set(prev);
                    if (next.has(word)) next.delete(word);
                    else next.add(word);
                    return next;
                  })
                }
                sx={{ fontSize: "11px", height: 26, cursor: "pointer" }}
              />
            );
          })}
          {keywords.length > visibleKeywords.length && (
            <Chip
              label={`${keywords.length - visibleKeywords.length} more`}
              size="small"
              variant="outlined"
              onClick={() => setAllKeywords(true)}
              sx={{ fontSize: "11px", height: 26, cursor: "pointer" }}
            />
          )}
          {allKeywords && keywords.length > WORTH_CLICKING && (
            <Chip
              label="Show fewer"
              size="small"
              variant="outlined"
              onClick={() => setAllKeywords(false)}
              sx={{ fontSize: "11px", height: 26, cursor: "pointer" }}
            />
          )}
          {chosen.size > 0 && (
            <Chip
              label="Clear"
              size="small"
              variant="outlined"
              onClick={() => setChosen(new Set())}
              onDelete={() => setChosen(new Set())}
              sx={{ fontSize: "11px", height: 26, cursor: "pointer" }}
            />
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

      <TableContainer sx={{ width: "100%", maxWidth: "100%", overflowX: "auto" }}>
        <Table size="small" sx={{ minWidth: 1000 }}>
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
                        width: 96,
                        minWidth: 96,
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
            {!shown.length && (
              <TableRow>
                <TableCell colSpan={COLUMNS.length} sx={{ py: 4, textAlign: "center" }}>
                  <Typography sx={{ typography: "s2", color: "text.secondary" }}>
                    No scenario matches that.
                  </Typography>
                  <Button
                    size="small"
                    onClick={() => {
                      setQuery("");
                      setChosen(new Set());
                    }}
                  >
                    Clear the filters
                  </Button>
                </TableCell>
              </TableRow>
            )}
            {groups.map(({ useCase, rows }) => {
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
                      </Stack>
                    </TableCell>
                  </TableRow>

                  {rows.map((scenario) => {
                    const persona = scenario.persona || {};
                    const who = [persona.gender, persona.age_group, persona.location]
                      .filter(Boolean)
                      .join(" \u00b7 ");
                    return (
                      <TableRow hover key={scenario.name}>
                        <TableCell padding="checkbox" sx={{ pl: 1.5, verticalAlign: "top" }}>
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
                          {numbers.get(scenario.name)}
                        </TableCell>
                        <TableCell sx={{ maxWidth: 280, verticalAlign: "top" }}>
                          <Typography noWrap sx={{ typography: "s2", fontWeight: 600 }}>
                            {readable(scenario.name)}
                          </Typography>
                          <Tooltip title={scenario.branch || ""}>
                            <Typography noWrap sx={{ typography: "s3", color: "text.subtitle" }}>
                              {scenario.branch}
                            </Typography>
                          </Tooltip>
                        </TableCell>
                        <TableCell sx={{ maxWidth: 200, verticalAlign: "top" }}>
                          <Typography noWrap sx={{ typography: "s2" }}>
                            {persona.name}
                          </Typography>
                          <Typography noWrap sx={{ typography: "s3", color: "text.subtitle" }}>
                            {who}
                          </Typography>
                        </TableCell>
                        <TableCell sx={{ maxWidth: 220, verticalAlign: "top" }}>
                          <Levers scenario={scenario} persona={persona} />
                        </TableCell>
                        <TableCell sx={{ maxWidth: 320, verticalAlign: "top" }}>
                          <Clamped text={scenario.instruction} />
                        </TableCell>
                        <TableCell sx={{ maxWidth: 260, verticalAlign: "top" }}>
                          <SubGoals names={scenario.sub_goals} />
                        </TableCell>
                        <TableCell sx={{ maxWidth: 320, verticalAlign: "top" }}>
                          <Clamped text={scenario.tests} />
                        </TableCell>
                        <TableCell
                          align="right"
                          sx={{
                            whiteSpace: "nowrap",
                            verticalAlign: "top",
                            position: "sticky",
                            right: 0,
                            zIndex: 1,
                            width: 96,
                            minWidth: 96,
                            // A sticky cell needs its own opaque ground to hide the columns sliding
                            // under it, which loses the row hover tint. Painting the same overlay
                            // back on keeps the pinned column part of the row rather than a patch.
                            bgcolor: "background.paper",
                            boxShadow: (theme) =>
                              `-8px 0 12px -6px ${alpha(
                                theme.palette.common.black,
                                theme.palette.mode === "dark" ? 0.45 : 0.08,
                              )}`,
                            transition: "background-image 120ms ease",
                            ".MuiTableRow-hover:hover &": {
                              backgroundImage: (theme) =>
                                `linear-gradient(${theme.palette.action.hover}, ${theme.palette.action.hover})`,
                            },
                          }}
                        >
                          <Tooltip
                            arrow
                            title={editable ? "Edit scenario" : lockedReason}
                          >
                            <span>
                              <IconButton
                                size="small"
                                disabled={!editable}
                                onClick={() => setEditing(scenario)}
                              >
                                <Iconify
                                  icon="solar:pen-new-square-linear"
                                  width={15}
                                  sx={{ color: "text.subtitle" }}
                                />
                              </IconButton>
                            </span>
                          </Tooltip>
                          <Tooltip
                            arrow
                            title={editable ? "Remove from this suite" : lockedReason}
                          >
                            <span>
                              <IconButton
                                size="small"
                                disabled={!editable || busy}
                                onClick={() => dropOne(scenario.name)}
                              >
                                <Iconify
                                  icon="solar:trash-bin-trash-linear"
                                  width={15}
                                  sx={{ color: "text.subtitle" }}
                                />
                              </IconButton>
                            </span>
                          </Tooltip>
                        </TableCell>
                      </TableRow>
                    );
                  })}
                </React.Fragment>
              );
            })}
          </TableBody>
        </Table>
      </TableContainer>

      <Drawer anchor="right" open={Boolean(editing)} onClose={() => setEditing(null)}>
        <Box sx={{ width: "100vw", maxWidth: 620, height: "100%" }}>
          {editing && (
            <ScenarioEditForm
              scenario={editing}
              editableFields={scenarioEditing?.editable_fields}
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

// Numbered rather than chipped, because sub-goals are an ordered set of things the agent has to
// reach and a row of chips throws that order away. Three, then a count, so one scenario with nine
// of them cannot make every other row tall.
// The four things a voice suite is graded on: who is calling, in what accent and language, over
// what noise, and whether the call is an attack. Each is already on the scenario; none of it was
// on screen, so a suite looked like a list of tasks rather than a spread of conditions.
function Levers({ scenario, persona }) {
  const coverage = scenario.coverage || {};
  const overlay = String(coverage.overlay || "").trim();
  const noise =
    typeof scenario.background_noise === "string"
      ? scenario.background_noise.trim()
      : scenario.background_noise
        ? "present"
        : "";
  const spoken = (persona.languages || []).filter((one) => one && one !== "English");
  const chips = [
    persona.accent && { key: `a-${persona.accent}`, label: persona.accent, tone: "default" },
    spoken.length && { key: `l-${spoken[0]}`, label: spoken[0], tone: "default" },
    noise && { key: `n-${noise}`, label: readable(noise), tone: "default" },
    overlay &&
      overlay !== "none" && {
        key: `o-${overlay}`,
        label: readable(overlay),
        tone: "adversarial",
      },
  ].filter(Boolean);

  if (!chips.length) {
    return <Typography sx={{ typography: "s3", color: "text.subtitle" }}>&mdash;</Typography>;
  }
  const intensity = String(coverage.overlay_intensity || "").trim();
  return (
    <Stack direction="row" spacing={0.5} sx={{ flexWrap: "wrap", gap: 0.5 }}>
      {chips.map((chip) => (
        <Tooltip
          key={chip.key}
          title={
            chip.tone === "adversarial" && intensity && intensity !== "absent"
              ? `${chip.label} \u00b7 ${intensity}`
              : ""
          }
        >
          <Chip
            size="small"
            label={chip.label}
            sx={{
              height: 20,
              typography: "s3",
              ...(chip.tone === "adversarial"
                ? { bgcolor: "warning.lighter", color: "warning.darker" }
                : { bgcolor: "action.hover", color: "text.secondary" }),
            }}
          />
        </Tooltip>
      ))}
    </Stack>
  );
}

Levers.propTypes = {
  scenario: PropTypes.object,
  persona: PropTypes.object,
};

function SubGoals({ names }) {
  const list = names || [];
  if (!list.length) {
    return <Typography sx={{ typography: "s3", color: "text.subtitle" }}>&mdash;</Typography>;
  }
  const all = list.map((name, index) => `${index + 1}. ${readable(name)}`).join("\n");
  return (
    <Tooltip title={all}>
      <Stack spacing={0.375}>
        {list.slice(0, 3).map((name, index) => (
          <Stack key={name} direction="row" spacing={0.75} alignItems="flex-start">
            <Typography
              sx={{
                typography: "s3",
                color: "text.subtitle",
                fontVariantNumeric: "tabular-nums",
                flexShrink: 0,
                mt: "1px",
              }}
            >
              {index + 1}.
            </Typography>
            <Typography noWrap sx={{ typography: "s3", color: "text.secondary", minWidth: 0 }}>
              {readable(name)}
            </Typography>
          </Stack>
        ))}
        {list.length > 3 && (
          <Typography sx={{ typography: "s3", color: "text.subtitle", pl: 1.75 }}>
            + {list.length - 3} more
          </Typography>
        )}
      </Stack>
    </Tooltip>
  );
}

SubGoals.propTypes = { names: PropTypes.arrayOf(PropTypes.string) };

// Two lines, then ellipsis, with the whole value on hover. A situation can run to a paragraph and
// a table that lets one row grow to five lines stops being scannable.
function Clamped({ text }) {
  const value = String(text || "");
  return (
    <Tooltip title={value}>
      <Typography
        sx={{
          typography: "s2",
          color: "text.secondary",
          lineHeight: 1.45,
          display: "-webkit-box",
          WebkitLineClamp: 3,
          WebkitBoxOrient: "vertical",
          overflow: "hidden",
          wordBreak: "break-word",
        }}
      >
        {value || "\u2014"}
      </Typography>
    </Tooltip>
  );
}

Clamped.propTypes = { text: PropTypes.string };

ScenarioSuite.propTypes = {
  scenarios: PropTypes.arrayOf(PropTypes.object),
  jobId: PropTypes.string,
  // A read-only suite is one with no job behind it, so there is nothing to save an edit to.
  editable: PropTypes.bool,
  scenarioEditing: PropTypes.shape({
    editable_fields: PropTypes.arrayOf(PropTypes.string),
    applied_without_rework: PropTypes.arrayOf(PropTypes.string),
  }),
  onChanged: PropTypes.func,
};
