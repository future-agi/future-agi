import PropTypes from "prop-types";
import { useCallback, useEffect, useMemo, useState } from "react";
import { alpha } from "@mui/material/styles";
import {
  Autocomplete, Box, Stack, Typography, Button, TextField, Slider,
  ToggleButton, ToggleButtonGroup,
} from "@mui/material";

import SideDrawer from "../../components/SideDrawer";
import { BUILD_TONES } from "../../buildEnvironment/buildTones";
import { SCENARIO_SHAPE } from "./scenarios.shapes";
import { EDITOR_COPY, noiseKey, noiseValue, subTasksToText } from "./scenarioEditor.constants";

const fieldSx = { typography: "s2" };
const selectedToggleSx = (t) => ({
  bgcolor: alpha(BUILD_TONES.accent, t.palette.mode === "dark" ? 0.16 : 0.09),
  color: BUILD_TONES.accent,
  borderColor: BUILD_TONES.accent,
});

// The server names which fields may be written, in `scenario_editing`
// (editable_fields + persona_fields), and which force a re-proof (rework_fields).
// The drawer renders every field but disables the ones the server does not name,
// so the read-only set is the server's answer, never a hardcoded list.
const READ_ONLY_HINT = "Proved, so not editable: this was verified when the scenario was generated.";
const REPROOF_HINT = "Editing this re-proves the scenario.";

export default function ScenarioEditor({ open, onClose, row, onSave, scenarioEditing, noiseOptions = [], levelLabels = {} }) {
  const editableFields = useMemo(
    () => scenarioEditing?.editable_fields ?? [],
    [scenarioEditing],
  );
  // Background-noise choices come from the editing contract, always including the current value.
  const noiseChoices = useMemo(() => {
    const base = noiseOptions;
    const current = noiseKey(row?.backgroundNoise);
    return current && !base.includes(current) ? [...base, current] : base;
  }, [noiseOptions, row?.backgroundNoise]);
  const personaFields = useMemo(
    () => scenarioEditing?.persona_fields ?? [],
    [scenarioEditing],
  );
  const reworkFields = useMemo(
    () => scenarioEditing?.rework_fields ?? [],
    [scenarioEditing],
  );

  const canEdit = useCallback((field) => editableFields.includes(field), [editableFields]);
  const canEditPersona = useCallback(
    (field) => personaFields.includes(field),
    [personaFields],
  );

  // The normalised opening state, seeded from the row (persona values come off
  // `_raw.persona`, since the mapped shape drops keywords/languages). `dirty`
  // and the emitted ops compare against this.
  const base = useMemo(() => {
    if (!row) return null;
    const p = row._raw?.persona || {};
    const asList = (v) => (Array.isArray(v) ? v.join(", ") : (v ?? ""));
    return {
      name: row.name ?? "",
      useCase: row.useCase ?? "",
      branch: row.conversationBranch ?? "",
      instruction: row.situation ?? "",
      tests: row.expected ?? "",
      subGoalsText: subTasksToText(row.subTasks),
      maxTurns: row.maxTurns ?? "",
      backgroundNoise: noiseKey(row.backgroundNoise),
      keywords: asList(row.keywords),
      persona: {
        name: p.name ?? "",
        ageGroup: p.age_group ?? "",
        gender: p.gender ?? "",
        accent: p.accent ?? "",
        communicationStyle: p.communication_style ?? "",
        personality: p.personality ?? "",
        occupation: p.occupation ?? "",
        location: p.location ?? "",
        languages: asList(p.languages),
      },
    };
  }, [row]);

  const [draft, setDraft] = useState(base || {});
  useEffect(() => { if (base) setDraft(base); }, [base]);

  // The amend ops for the fields that actually changed AND are writable, plus
  // whether any of them forces a re-proof. Drives both the Save-enabled state and
  // the emitted body.
  const { changes, rework } = useMemo(() => {
    if (!base) return { changes: [], rework: false };
    const ops = [];
    const changedFields = [];

    const setField = (field, value) => {
      ops.push({ op: "set_field", scenario: base.name, field, value });
      changedFields.push(field);
    };

    if (canEdit("tests") && draft.tests !== base.tests) setField("tests", draft.tests);
    if (canEdit("max_turns") && String(draft.maxTurns) !== String(base.maxTurns)) {
      setField("max_turns", Number(draft.maxTurns) || 0);
    }
    if (canEdit("keywords") && draft.keywords !== base.keywords) {
      setField("keywords", String(draft.keywords || "").split(",").map((k) => k.trim()).filter(Boolean));
    }
    if (canEdit("background_noise") && draft.backgroundNoise !== base.backgroundNoise) {
      setField("background_noise", noiseValue(draft.backgroundNoise));
    }

    // Persona: one set_persona op carrying only the changed, writable fields.
    const PERSONA_MAP = {
      accent: "accent",
      communicationStyle: "communication_style",
      personality: "personality",
      occupation: "occupation",
      location: "location",
      languages: "languages",
    };
    const LIST_KEYS = new Set(["languages"]);
    const persona = {};
    for (const [draftKey, serverKey] of Object.entries(PERSONA_MAP)) {
      if (!canEditPersona(serverKey)) continue;
      const dv = draft.persona?.[draftKey];
      const bv = base.persona?.[draftKey];
      if (dv === bv) continue;
      persona[serverKey] = LIST_KEYS.has(draftKey)
        ? String(dv || "").split(",").map((s) => s.trim()).filter(Boolean)
        : dv;
      changedFields.push(serverKey);
    }
    if (Object.keys(persona).length) {
      ops.push({ op: "set_persona", scenario: base.name, persona });
    }

    return {
      changes: ops,
      rework: changedFields.some((f) => reworkFields.includes(f)),
    };
  }, [draft, base, canEdit, canEditPersona, reworkFields]);

  if (!row) return null;

  const set = (k) => (v) => setDraft((d) => ({ ...d, [k]: v }));
  const setPersona = (k) => (v) => setDraft((d) => ({ ...d, persona: { ...(d.persona || {}), [k]: v } }));

  const save = () => {
    if (!changes.length) return;
    onSave({ changes, rework });
    onClose();
  };

  return (
    <SideDrawer open={open} onClose={onClose} width={620}>
      <Stack sx={{ height: "100%" }}>
        <Box sx={{ px: 2.5, py: 2, borderBottom: "1px solid", borderColor: "divider", flexShrink: 0 }}>
          <Typography sx={{ typography: "m2", fontWeight: "fontWeightSemiBold" }}>
            {EDITOR_COPY.title}
          </Typography>
          <Typography sx={{ typography: "s2", color: "text.subtitle" }}>
            {EDITOR_COPY.subtitle}
          </Typography>
        </Box>

        <Stack spacing={2.5} sx={{ flex: 1, overflow: "auto", p: 2.5 }}>
          <SectionHeader title={EDITOR_COPY.scenarioSection} />
          <TextField
            size="small" label="Name" value={draft.name || ""} disabled
            helperText={READ_ONLY_HINT}
            InputProps={{ sx: { ...fieldSx, fontFamily: "ui-monospace, Menlo, monospace" } }}
          />
          <TextField
            size="small" label="Use case" value={draft.useCase || ""} disabled
            helperText={READ_ONLY_HINT}
            InputProps={{ sx: fieldSx }}
          />
          <TextField
            size="small" label="Branch" value={draft.branch || ""} disabled
            helperText={READ_ONLY_HINT}
            InputProps={{ sx: fieldSx }}
          />
          <TextField
            size="small" label="What the caller wants" multiline minRows={2}
            value={draft.instruction || ""} disabled
            helperText={READ_ONLY_HINT}
            InputProps={{ sx: fieldSx }}
          />
          <TextField
            size="small" label="Passes when" multiline minRows={2}
            value={draft.tests || ""}
            disabled={!canEdit("tests")}
            onChange={(e) => set("tests")(e.target.value)}
            helperText={canEdit("tests")
              ? "What a pass looks like. Evals grade against this."
              : READ_ONLY_HINT}
            InputProps={{ sx: fieldSx }}
          />
          <TextField
            size="small" label="Sub-goals" multiline minRows={3}
            value={draft.subGoalsText || ""} disabled
            helperText={READ_ONLY_HINT}
            InputProps={{ sx: fieldSx }}
          />

          <SectionHeader title={EDITOR_COPY.personaSection} hint="Who is on the other end of the run." />
          <Stack direction={{ xs: "column", sm: "row" }} spacing={2}>
            <TextField
              size="small" label="Name" fullWidth value={draft.persona?.name || ""} disabled
              helperText={READ_ONLY_HINT}
              InputProps={{ sx: fieldSx }}
            />
            <TextField
              size="small" label="Age group" fullWidth value={draft.persona?.ageGroup || ""} disabled
              helperText={READ_ONLY_HINT}
              InputProps={{ sx: fieldSx }}
            />
            <TextField
              size="small" label="Gender" fullWidth value={draft.persona?.gender || ""} disabled
              helperText={READ_ONLY_HINT}
              InputProps={{ sx: fieldSx }}
            />
          </Stack>
          {[
            ["Accent", "accent", "accent"],
            ["Communication style", "communicationStyle", "communication_style"],
            ["Personality", "personality", "personality"],
            ["Occupation", "occupation", "occupation"],
            ["Location", "location", "location"],
            ["Languages", "languages", "languages"],
          ]
            .filter(([, , serverKey]) => serverKey !== "accent" || canEditPersona("accent"))
            .map(([label, k, serverKey]) => (
              <PersonaField
                key={serverKey} label={label} k={k} serverKey={serverKey} draft={draft}
                onChange={setPersona} canEditPersona={canEditPersona} reworkFields={reworkFields}
                choices={scenarioEditing?.persona_choices?.[serverKey] ?? []}
                multiple={serverKey === "languages"}
              />
            ))}
          <TextField
            size="small" label="Keywords" value={draft.keywords || ""}
            disabled={!canEdit("keywords")}
            onChange={(e) => set("keywords")(e.target.value)}
            helperText={canEdit("keywords") ? "Comma-separated, how the scenario is found." : READ_ONLY_HINT}
            InputProps={{ sx: fieldSx }}
          />

          {(canEdit("max_turns") || canEdit("background_noise")) && (
            <>
              <SectionHeader title={EDITOR_COPY.constraintsSection} hint="Editing these re-proves the scenario." />
              {canEdit("max_turns") && (
                <Box>
                  <Stack direction="row" alignItems="center" spacing={1}>
                    <Typography sx={{ typography: "s2", fontWeight: "fontWeightSemiBold", flex: 1 }}>Max turns</Typography>
                    <Typography sx={{ typography: "s2", fontVariantNumeric: "tabular-nums" }}>{draft.maxTurns ? `~${draft.maxTurns}` : "Default"}</Typography>
                  </Stack>
                  <Slider size="small" min={2} max={20} value={Number(draft.maxTurns) || 0} onChange={(_, v) => set("maxTurns")(v)} />
                  <Typography sx={{ typography: "s3", color: "text.subtitle" }}>
                    How long the run is allowed to go before it is called off. {REPROOF_HINT}
                  </Typography>
                </Box>
              )}
              {canEdit("background_noise") && (
                <Box>
                  <Typography sx={{ typography: "s2", fontWeight: "fontWeightSemiBold", mb: 0.75 }}>Background noise</Typography>
                  <ToggleButtonGroup
                    size="small" exclusive value={draft.backgroundNoise || ""}
                    onChange={(_, v) => v && set("backgroundNoise")(v)}
                    sx={{
                      display: "grid", gridTemplateColumns: "repeat(4, minmax(0, 1fr))", gap: 0.75,
                      "& .MuiToggleButtonGroup-grouped": { m: 0, border: "1px solid", borderRadius: 1 },
                      "& .MuiToggleButton-root": {
                        typography: "s2", fontWeight: "fontWeightSemiBold", textTransform: "none",
                        px: 1.5, py: 0.375, color: "text.secondary", borderColor: "divider",
                        borderRadius: 1,
                        "&.Mui-selected": selectedToggleSx,
                      },
                    }}
                  >
                    {noiseChoices.map((n) => <ToggleButton key={n} value={n}>{levelLabels[n] ?? n}</ToggleButton>)}
                  </ToggleButtonGroup>
                </Box>
              )}
            </>
          )}
        </Stack>

        <Stack
          direction="row" justifyContent="flex-end" spacing={1}
          sx={{ px: 2.5, py: 1.75, borderTop: "1px solid", borderColor: "divider", flexShrink: 0 }}
        >
          <Button onClick={onClose} sx={{ typography: "s2", fontWeight: "fontWeightSemiBold", color: "text.secondary" }}>
            {EDITOR_COPY.cancel}
          </Button>
          <Button
            variant="contained" color="primary" size="small" disabled={!changes.length}
            onClick={save}
            sx={{ typography: "s2", fontWeight: "fontWeightBold" }}
          >
            {EDITOR_COPY.save}
          </Button>
        </Stack>
      </Stack>
    </SideDrawer>
  );
}

ScenarioEditor.propTypes = {
  open: PropTypes.bool,
  onClose: PropTypes.func,
  row: SCENARIO_SHAPE,
  // Receives { changes, rework } — the amend ops for the changed writable fields.
  onSave: PropTypes.func,
  // The server's editability block; gates which fields are writable.
  scenarioEditing: PropTypes.shape({
    editable_fields: PropTypes.arrayOf(PropTypes.string),
    persona_fields: PropTypes.arrayOf(PropTypes.string),
    persona_choices: PropTypes.objectOf(PropTypes.arrayOf(PropTypes.string)),
    rework_fields: PropTypes.arrayOf(PropTypes.string),
  }),
  // Background-noise choices from the server field catalogue (values the agent uses).
  noiseOptions: PropTypes.arrayOf(PropTypes.string),
  levelLabels: PropTypes.object,
};

// One persona field, gated by the server's persona_fields, choosing from the
// server's persona_choices. The current value stays selectable even when the
// server does not list it.
function PersonaField({ label, k, serverKey, draft, onChange, canEditPersona, reworkFields, choices, multiple }) {
  const editable = canEditPersona(serverKey);
  const reproof = reworkFields.includes(serverKey);
  const raw = draft.persona?.[k] || "";
  const value = multiple
    ? String(raw).split(",").map((one) => one.trim()).filter(Boolean)
    : raw || null;
  const held = multiple ? value : value ? [value] : [];
  const options = [...choices, ...held.filter((one) => !choices.includes(one))];
  return (
    <Autocomplete
      size="small" multiple={multiple} options={options} value={value}
      disabled={!editable} disableClearable={!multiple}
      onChange={(_, next) => onChange(k)(multiple ? next.join(", ") : next || "")}
      renderInput={(params) => (
        <TextField
          {...params} label={label}
          helperText={editable ? (reproof ? REPROOF_HINT : undefined) : READ_ONLY_HINT}
        />
      )}
    />
  );
}
PersonaField.propTypes = {
  label: PropTypes.string,
  k: PropTypes.string,
  serverKey: PropTypes.string,
  draft: PropTypes.object,
  onChange: PropTypes.func,
  canEditPersona: PropTypes.func,
  reworkFields: PropTypes.arrayOf(PropTypes.string),
  choices: PropTypes.arrayOf(PropTypes.string),
  multiple: PropTypes.bool,
};

function SectionHeader({ title, hint }) {
  return (
    <Box>
      <Typography sx={{ typography: "s3", fontWeight: "fontWeightBold", color: "text.primary", textTransform: "uppercase", letterSpacing: 0.6, mb: 0.375 }}>
        {title}
      </Typography>
      {hint && <Typography sx={{ typography: "s3", color: "text.subtitle" }}>{hint}</Typography>}
    </Box>
  );
}
SectionHeader.propTypes = { title: PropTypes.string, hint: PropTypes.string };
