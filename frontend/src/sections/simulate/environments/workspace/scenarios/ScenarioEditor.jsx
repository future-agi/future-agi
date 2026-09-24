import PropTypes from "prop-types";
import { useEffect, useMemo, useState } from "react";
import { alpha } from "@mui/material/styles";
import {
  Box, Stack, Typography, Button, TextField, Slider,
  MenuItem, Select, InputLabel, FormControl, ToggleButton, ToggleButtonGroup,
} from "@mui/material";

import { subTasksFor } from "src/api/simulate-environments/_fixtures/contract";
import SideDrawer from "../../components/SideDrawer";
import { BUILD_TONES } from "../../buildEnvironment/buildTones";
import { ENV_SHAPE, SCENARIO_SHAPE } from "./scenarios.shapes";
import {
  EDITOR_COPY, CONVERSATIONAL_SURFACES, VOICE_ONLY_SURFACES,
  TONE_OPTIONS, STYLE_OPTIONS, ACCENT_OPTIONS, LANGUAGE_OPTIONS, NOISE_OPTIONS,
  deriveCaller, deriveNoise, subTasksToText, textToSubTasks,
} from "./scenarioEditor.constants";

const fieldSx = { typography: "s2" };
const selectedToggleSx = (t) => ({
  bgcolor: alpha(BUILD_TONES.accent, t.palette.mode === "dark" ? 0.16 : 0.09),
  color: BUILD_TONES.accent,
  borderColor: BUILD_TONES.accent,
});

// Edit one scenario, in place, on this environment's copy. The parts here are
// the ones safe to change directly — name, use case, branch, task, passes-when,
// sub-goals, the persona, and (on conversational / voice surfaces) the caller
// and call constraints. Saving writes the whole draft back through `onSave`.
export default function ScenarioEditor({ open, onClose, row, env, onSave }) {
  // The normalised opening state, once per row. Caller / noise / sub-goals are
  // filled from the row's own data (sub-goals through the same derivation the
  // table renders), so the drawer opens populated and `dirty` compares against
  // this, not the raw row — otherwise Save would be enabled before any edit.
  const base = useMemo(() => {
    if (!row) return null;
    const subTasks = row.subTasks?.length ? row.subTasks : subTasksFor(row, env);
    return {
      ...row,
      caller: row.caller || deriveCaller(row.persona),
      backgroundNoise: row.backgroundNoise || deriveNoise(row.persona),
      subGoalsText: subTasksToText(subTasks),
    };
  }, [row, env]);

  const [draft, setDraft] = useState(base || {});
  useEffect(() => { if (base) setDraft(base); }, [base]);

  if (!row) return null;

  const set = (k) => (v) => setDraft((d) => ({ ...d, [k]: v }));
  const setCaller = (k) => (v) => setDraft((d) => ({ ...d, caller: { ...(d.caller || {}), [k]: v } }));
  const setPersona = (k) => (v) => setDraft((d) => ({ ...d, persona: { ...(d.persona || {}), [k]: v } }));

  const isConversational = CONVERSATIONAL_SURFACES.includes(env?.surface);
  const isVoice = VOICE_ONLY_SURFACES.includes(env?.surface);
  const dirty = JSON.stringify(draft) !== JSON.stringify(base);

  const save = () => {
    const { subGoalsText, ...rest } = draft;
    // Only rewrite sub-goals if they were actually touched; an untouched row
    // keeps its original value rather than growing a copy of derived data.
    if (subGoalsText !== base.subGoalsText) rest.subTasks = textToSubTasks(subGoalsText);
    else if (row.subTasks !== undefined) rest.subTasks = row.subTasks;
    else delete rest.subTasks;
    onSave(rest);
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
            size="small" label="Name" value={draft.name || ""}
            onChange={(e) => set("name")(e.target.value)}
            helperText="Kebab-case identifier — e.g. polite-senior-verify-identity."
            InputProps={{ sx: { ...fieldSx, fontFamily: "ui-monospace, Menlo, monospace" } }}
          />
          <TextField
            size="small" label="Use case" value={draft.useCase || ""}
            onChange={(e) => set("useCase")(e.target.value)}
            helperText="The sentence describing what this group of scenarios tests."
            InputProps={{ sx: fieldSx }}
          />
          <TextField
            size="small" label="Branch" value={draft.branchCategory || ""}
            onChange={(e) => set("branchCategory")(e.target.value)}
            helperText="What makes this one different from its siblings."
            InputProps={{ sx: fieldSx }}
          />
          <TextField
            size="small" label="What the caller wants" multiline minRows={2}
            value={draft.task || ""}
            onChange={(e) => set("task")(e.target.value)}
            helperText="The task the agent has to complete in this run."
            InputProps={{ sx: fieldSx }}
          />
          <TextField
            size="small" label="Passes when" multiline minRows={2}
            value={draft.expected || ""}
            onChange={(e) => set("expected")(e.target.value)}
            helperText="What a pass looks like. Evals grade against this."
            InputProps={{ sx: fieldSx }}
          />
          <TextField
            size="small" label="Sub-goals" multiline minRows={3}
            value={draft.subGoalsText || ""}
            onChange={(e) => set("subGoalsText")(e.target.value)}
            helperText="One per line — the steps the runner watches for."
            InputProps={{ sx: fieldSx }}
          />

          <SectionHeader title={EDITOR_COPY.personaSection} hint="Who is on the other end of the run." />
          <TextField
            size="small" label="Name" value={draft.persona?.name || ""}
            onChange={(e) => setPersona("name")(e.target.value)}
            helperText="Display name for the caller — e.g. The Polite Senior Caller."
            InputProps={{ sx: fieldSx }}
          />
          <Stack direction={{ xs: "column", sm: "row" }} spacing={2}>
            <TextField
              size="small" label="Age group" fullWidth
              value={draft.persona?.ageGroup || draft.persona?.age || ""}
              onChange={(e) => setPersona("ageGroup")(e.target.value)}
              helperText="Range — e.g. 60-70."
              InputProps={{ sx: fieldSx }}
            />
            <TextField
              size="small" label="Voice" fullWidth value={draft.persona?.voice || ""}
              onChange={(e) => setPersona("voice")(e.target.value)}
              helperText="Accent + gender — e.g. US female."
              InputProps={{ sx: fieldSx }}
            />
          </Stack>
          <TextField
            size="small" label="Traits"
            value={Array.isArray(draft.persona?.traits) ? draft.persona.traits.join(", ") : (draft.persona?.traits || "")}
            onChange={(e) => setPersona("traits")(e.target.value.split(",").map((t) => t.trim()).filter(Boolean))}
            helperText="Comma-separated — e.g. polite, elderly, hard of hearing."
            InputProps={{ sx: fieldSx }}
          />

          {isConversational && (
            <>
              <SectionHeader title={EDITOR_COPY.callerSection} hint="How the caller comes across. Voice / chat surfaces only." />
              <Stack direction={{ xs: "column", sm: "row" }} spacing={2}>
                <PickField label="Tone" value={draft.caller?.tone || "neutral"} options={TONE_OPTIONS} onChange={setCaller("tone")} />
                <PickField label="Style" value={draft.caller?.style || "casual"} options={STYLE_OPTIONS} onChange={setCaller("style")} />
              </Stack>
              <Stack direction={{ xs: "column", sm: "row" }} spacing={2}>
                <PickField label="Accent" value={draft.caller?.accent || "US"} options={ACCENT_OPTIONS} onChange={setCaller("accent")} />
                <PickField label="Language" value={draft.caller?.language || "English"} options={LANGUAGE_OPTIONS} onChange={setCaller("language")} />
              </Stack>
            </>
          )}

          {isVoice && (
            <>
              <SectionHeader title={EDITOR_COPY.constraintsSection} hint="Voice-agent specific — every generated scenario carries defaults." />
              <Box>
                <Stack direction="row" alignItems="center" spacing={1}>
                  <Typography sx={{ typography: "s2", fontWeight: "fontWeightSemiBold", flex: 1 }}>Max turns</Typography>
                  <Typography sx={{ typography: "s2", fontVariantNumeric: "tabular-nums" }}>~{draft.turns || 0}</Typography>
                </Stack>
                <Slider size="small" min={2} max={20} value={draft.turns || 0} onChange={(_, v) => set("turns")(v)} />
                <Typography sx={{ typography: "s3", color: "text.subtitle" }}>
                  How long the run is allowed to go before it is called off.
                </Typography>
              </Box>
              <Box>
                <Typography sx={{ typography: "s2", fontWeight: "fontWeightSemiBold", mb: 0.75 }}>Background noise</Typography>
                <ToggleButtonGroup
                  size="small" exclusive value={draft.backgroundNoise || "none"}
                  onChange={(_, v) => v && set("backgroundNoise")(v)}
                  sx={{
                    "& .MuiToggleButton-root": {
                      typography: "s2", fontWeight: "fontWeightSemiBold", textTransform: "none",
                      px: 1.5, py: 0.375, color: "text.secondary", borderColor: "divider",
                      "&.Mui-selected": selectedToggleSx,
                    },
                  }}
                >
                  {NOISE_OPTIONS.map((n) => <ToggleButton key={n} value={n}>{n}</ToggleButton>)}
                </ToggleButtonGroup>
              </Box>
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
            variant="contained" color="primary" size="small" disabled={!dirty}
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
  env: ENV_SHAPE,
  onSave: PropTypes.func,
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

function PickField({ label, value, options, onChange }) {
  return (
    <FormControl size="small" fullWidth>
      <InputLabel>{label}</InputLabel>
      <Select label={label} value={value} onChange={(e) => onChange(e.target.value)} sx={fieldSx}>
        {options.map((o) => <MenuItem key={o} value={o} sx={fieldSx}>{o}</MenuItem>)}
      </Select>
    </FormControl>
  );
}
PickField.propTypes = {
  label: PropTypes.string,
  value: PropTypes.string,
  options: PropTypes.arrayOf(PropTypes.string),
  onChange: PropTypes.func,
};
