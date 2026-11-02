import PropTypes from "prop-types";
import { useCallback, useEffect, useMemo, useState } from "react";
import { alpha } from "@mui/material/styles";
import {
  Autocomplete,
  Box,
  Stack,
  Typography,
  Button,
  TextField,
  Slider,
  ToggleButton,
  ToggleButtonGroup,
} from "@mui/material";

import SideDrawer from "../../components/SideDrawer";
import { BUILD_TONES } from "../../buildEnvironment/buildTones";
import { injectComposerScaffold } from "../../buildEnvironment/console/composerScaffoldBus";
import { SCENARIO_SHAPE } from "./scenarios.shapes";
import {
  EDITOR_COPY,
  PERSONA_FIELDS,
  noiseKey,
  noiseValue,
  normaliseSubTasks,
  personaTypeFor,
  scaffoldTextFor,
} from "./scenarioEditor.constants";
import SectionHeader from "./editor/SectionHeader";
import LibraryPersonaActions from "./editor/LibraryPersonaActions";
import SubGoalsEditor from "./editor/SubGoalsEditor";
import MustPassCard from "./editor/MustPassCard";
import BuilderSection from "./editor/BuilderSection";
import VersionPreview from "./editor/VersionPreview";
import { libraryPersonaPatch } from "./editor/libraryPersona";

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
const READ_ONLY_HINT =
  "Proved, so not editable: this was verified when the scenario was generated.";
const REPROOF_HINT = "Editing this re-proves the scenario.";

// The sub-goals as the editor holds them: each with a stable key and a mark.
// The API sends no marks, so every sub-goal opens as critical.
const goalsOf = (row) =>
  normaliseSubTasks(row?.subTasks).map((st) => ({
    key: st.id,
    text: st.label,
    mark: "critical",
  }));

export default function ScenarioEditor({
  open,
  onClose,
  row,
  onSave,
  scenarioEditing,
  noiseOptions = [],
  levelLabels = {},
  env,
}) {
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

  const canEdit = useCallback(
    (field) => editableFields.includes(field),
    [editableFields],
  );
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
    const asList = (v) => (Array.isArray(v) ? v.join(", ") : v ?? "");
    return {
      name: row.name ?? "",
      useCase: row.useCase ?? "",
      branch: row.conversationBranch ?? "",
      instruction: row.situation ?? "",
      tests: row.expected ?? "",
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
  // Sub-goals and the must-pass mark have no API writes yet: they live in their
  // own state, so they can never reach the save below.
  const [subGoals, setSubGoals] = useState(() => goalsOf(row));
  const [mustPass, setMustPass] = useState(!!row?.critical);
  // What the last library pick filled, said once under the persona buttons.
  const [pickNote, setPickNote] = useState(null);
  useEffect(() => {
    if (base) setDraft(base);
    setSubGoals(goalsOf(row));
    setMustPass(!!row?.critical);
    setPickNote(null);
  }, [base, row]);

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

    if (canEdit("tests") && draft.tests !== base.tests)
      setField("tests", draft.tests);
    if (
      canEdit("max_turns") &&
      String(draft.maxTurns) !== String(base.maxTurns)
    ) {
      setField("max_turns", Number(draft.maxTurns) || 0);
    }
    if (canEdit("keywords") && draft.keywords !== base.keywords) {
      setField(
        "keywords",
        String(draft.keywords || "")
          .split(",")
          .map((k) => k.trim())
          .filter(Boolean),
      );
    }
    if (
      canEdit("background_noise") &&
      draft.backgroundNoise !== base.backgroundNoise
    ) {
      setField("background_noise", noiseValue(draft.backgroundNoise));
    }

    // Persona: one set_persona op carrying only the changed, writable fields.
    const persona = {};
    for (const { key, server, list } of PERSONA_FIELDS) {
      if (!canEditPersona(server)) continue;
      const dv = draft.persona?.[key];
      const bv = base.persona?.[key];
      if (dv === bv) continue;
      persona[server] = list
        ? String(dv || "")
            .split(",")
            .map((s) => s.trim())
            .filter(Boolean)
        : dv;
      changedFields.push(server);
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
  const setPersona = (k) => (v) =>
    setDraft((d) => ({ ...d, persona: { ...(d.persona || {}), [k]: v } }));

  const save = () => {
    if (!changes.length) return;
    onSave({ changes, rework });
    onClose();
  };

  // A library pick fills the persona fields this scenario can write; the
  // existing diff then sends them as one set_persona.
  const pickLibraryPersona = (libraryPersona) => {
    const { patch, filled } = libraryPersonaPatch(
      libraryPersona,
      canEditPersona,
    );
    setDraft((d) => ({ ...d, persona: { ...(d.persona || {}), ...patch } }));
    setPickNote(
      filled.length
        ? `Filled from ${libraryPersona.name}: ${filled.join(", ")}. Name, age group and gender stay as they are.`
        : `${libraryPersona.name} has no values for the fields this scenario can change.`,
    );
  };

  const goalTexts = subGoals.map((g) => g.text);
  const sendToBuilder = (text) => {
    injectComposerScaffold(text);
    onClose();
  };

  return (
    <SideDrawer open={open} onClose={onClose} width={640}>
      <Stack sx={{ height: "100%" }}>
        <Box
          sx={{
            px: 2.5,
            py: 2,
            pr: 6,
            borderBottom: "1px solid",
            borderColor: "divider",
            flexShrink: 0,
          }}
        >
          <Typography
            sx={{ typography: "m2", fontWeight: "fontWeightSemiBold" }}
          >
            {EDITOR_COPY.title}
          </Typography>
          <Typography sx={{ typography: "s2", color: "text.subtitle" }}>
            {EDITOR_COPY.subtitle}
          </Typography>
        </Box>

        <Stack spacing={2.5} sx={{ flex: 1, overflow: "auto", p: 2.5 }}>
          <SectionHeader title={EDITOR_COPY.scenarioSection} />
          <TextField
            size="small"
            label="Name"
            value={draft.name || ""}
            disabled
            helperText={READ_ONLY_HINT}
            InputProps={{
              sx: { ...fieldSx, fontFamily: "ui-monospace, Menlo, monospace" },
            }}
          />
          <TextField
            size="small"
            label="Use case"
            value={draft.useCase || ""}
            disabled
            helperText={READ_ONLY_HINT}
            InputProps={{ sx: fieldSx }}
          />
          <TextField
            size="small"
            label="Branch"
            value={draft.branch || ""}
            disabled
            helperText={READ_ONLY_HINT}
            InputProps={{ sx: fieldSx }}
          />
          <TextField
            size="small"
            label="Passes when"
            multiline
            minRows={2}
            value={draft.tests || ""}
            disabled={!canEdit("tests")}
            onChange={(e) => set("tests")(e.target.value)}
            helperText={
              canEdit("tests")
                ? "What a pass looks like. Evals grade against this."
                : READ_ONLY_HINT
            }
            InputProps={{ sx: fieldSx }}
          />
          <MustPassCard checked={mustPass} onChange={setMustPass} />

          <SubGoalsEditor goals={subGoals} onChange={setSubGoals} />

          <SectionHeader
            title={EDITOR_COPY.personaSection}
            hint={EDITOR_COPY.personaHint}
          />
          <LibraryPersonaActions
            canPick={PERSONA_FIELDS.some((f) => canEditPersona(f.server))}
            canSave={!!(row._raw?.persona || row.persona)}
            personaType={personaTypeFor(env?.surface)}
            note={pickNote}
            onPick={pickLibraryPersona}
          />
          <Stack direction={{ xs: "column", sm: "row" }} spacing={2}>
            <TextField
              size="small"
              label="Name"
              fullWidth
              value={draft.persona?.name || ""}
              disabled
              helperText={READ_ONLY_HINT}
              InputProps={{ sx: fieldSx }}
            />
            <TextField
              size="small"
              label="Age group"
              fullWidth
              value={draft.persona?.ageGroup || ""}
              disabled
              helperText={READ_ONLY_HINT}
              InputProps={{ sx: fieldSx }}
            />
            <TextField
              size="small"
              label="Gender"
              fullWidth
              value={draft.persona?.gender || ""}
              disabled
              helperText={READ_ONLY_HINT}
              InputProps={{ sx: fieldSx }}
            />
          </Stack>
          {PERSONA_FIELDS.filter(
            ({ server }) => server !== "accent" || canEditPersona("accent"),
          ).map(({ key, server, label, list }) => (
            <PersonaField
              key={server}
              label={label}
              k={key}
              serverKey={server}
              draft={draft}
              onChange={setPersona}
              canEditPersona={canEditPersona}
              reworkFields={reworkFields}
              choices={scenarioEditing?.persona_choices?.[server] ?? []}
              multiple={!!list}
            />
          ))}
          <TextField
            size="small"
            label="Keywords"
            value={draft.keywords || ""}
            disabled={!canEdit("keywords")}
            onChange={(e) => set("keywords")(e.target.value)}
            helperText={
              canEdit("keywords")
                ? "Comma-separated, how the scenario is found."
                : READ_ONLY_HINT
            }
            InputProps={{ sx: fieldSx }}
          />

          {(canEdit("max_turns") || canEdit("background_noise")) && (
            <>
              <SectionHeader
                title={EDITOR_COPY.constraintsSection}
                hint="Editing these re-proves the scenario."
              />
              {canEdit("max_turns") && (
                <Box>
                  <Stack direction="row" alignItems="center" spacing={1}>
                    <Typography
                      sx={{
                        typography: "s2",
                        fontWeight: "fontWeightSemiBold",
                        flex: 1,
                      }}
                    >
                      Max turns
                    </Typography>
                    <Typography
                      sx={{
                        typography: "s2",
                        fontVariantNumeric: "tabular-nums",
                      }}
                    >
                      {draft.maxTurns ? `~${draft.maxTurns}` : "Default"}
                    </Typography>
                  </Stack>
                  <Slider
                    size="small"
                    min={2}
                    max={20}
                    value={Number(draft.maxTurns) || 0}
                    onChange={(_, v) => set("maxTurns")(v)}
                  />
                  <Typography sx={{ typography: "s3", color: "text.subtitle" }}>
                    How long the run is allowed to go before it is called off.{" "}
                    {REPROOF_HINT}
                  </Typography>
                </Box>
              )}
              {canEdit("background_noise") && (
                <Box>
                  <Typography
                    sx={{
                      typography: "s2",
                      fontWeight: "fontWeightSemiBold",
                      mb: 0.75,
                    }}
                  >
                    Background noise
                  </Typography>
                  <ToggleButtonGroup
                    size="small"
                    exclusive
                    value={draft.backgroundNoise || ""}
                    onChange={(_, v) => v && set("backgroundNoise")(v)}
                    sx={{
                      display: "grid",
                      gridTemplateColumns: "repeat(4, minmax(0, 1fr))",
                      gap: 0.75,
                      "& .MuiToggleButtonGroup-grouped": {
                        m: 0,
                        border: "1px solid",
                        borderRadius: 1,
                      },
                      "& .MuiToggleButton-root": {
                        typography: "s2",
                        fontWeight: "fontWeightSemiBold",
                        textTransform: "none",
                        px: 1.5,
                        py: 0.375,
                        color: "text.secondary",
                        borderColor: "divider",
                        borderRadius: 1,
                        "&.Mui-selected": selectedToggleSx,
                      },
                    }}
                  >
                    {noiseChoices.map((n) => (
                      <ToggleButton key={n} value={n}>
                        {levelLabels[n] ?? n}
                      </ToggleButton>
                    ))}
                  </ToggleButtonGroup>
                </Box>
              )}
            </>
          )}

          <BuilderSection
            name={draft.name || row.name || ""}
            instruction={draft.instruction}
            checks={
              goalTexts.length
                ? goalTexts.join(" · ")
                : "Graded against the passes-when line"
            }
            onSend={sendToBuilder}
            onSendScenario={() =>
              sendToBuilder(scaffoldTextFor(draft, goalTexts))
            }
          />
        </Stack>

        {changes.length > 0 && <VersionPreview rework={rework} />}
        <Stack
          direction="row"
          justifyContent="flex-end"
          spacing={1}
          sx={{
            px: 2.5,
            py: 1.75,
            borderTop: "1px solid",
            borderColor: "divider",
            flexShrink: 0,
          }}
        >
          <Button
            onClick={onClose}
            sx={{
              typography: "s2",
              fontWeight: "fontWeightSemiBold",
              color: "text.secondary",
            }}
          >
            {EDITOR_COPY.cancel}
          </Button>
          <Button
            variant="contained"
            color="primary"
            size="small"
            disabled={!changes.length}
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
  // Receives { changes, rework }: the amend ops for the changed writable fields.
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
  // The environment; its surface picks the persona library's type (voice or
  // chat). Optional: without it, Create persona asks for the type.
  env: PropTypes.shape({ id: PropTypes.string, surface: PropTypes.string }),
};

// One persona field, gated by the server's persona_fields, choosing from the
// server's persona_choices. The current value stays selectable even when the
// server does not list it.
function PersonaField({
  label,
  k,
  serverKey,
  draft,
  onChange,
  canEditPersona,
  reworkFields,
  choices,
  multiple,
}) {
  const editable = canEditPersona(serverKey);
  const reproof = reworkFields.includes(serverKey);
  const raw = draft.persona?.[k] || "";
  const value = multiple
    ? String(raw)
        .split(",")
        .map((one) => one.trim())
        .filter(Boolean)
    : raw || null;
  const held = multiple ? value : value ? [value] : [];
  const options = [...choices, ...held.filter((one) => !choices.includes(one))];
  return (
    <Autocomplete
      size="small"
      multiple={multiple}
      options={options}
      value={value}
      disabled={!editable}
      disableClearable={!multiple}
      onChange={(_, next) =>
        onChange(k)(multiple ? next.join(", ") : next || "")
      }
      renderInput={(params) => (
        <TextField
          {...params}
          label={label}
          helperText={
            editable ? (reproof ? REPROOF_HINT : undefined) : READ_ONLY_HINT
          }
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
