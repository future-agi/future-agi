import {
  Autocomplete,
  Box,
  Button,
  Chip,
  IconButton,
  MenuItem,
  Slider,
  Stack,
  TextField,
  ToggleButton,
  ToggleButtonGroup,
  Typography,
} from "@mui/material";
import { alpha } from "@mui/material/styles";
import PropTypes from "prop-types";
import React, { useMemo, useState } from "react";

import Iconify from "src/components/iconify";
import {
  AccentOptions,
  CommunicationStyleOptions,
  LanguageOptions,
  LocationOptions,
  PersonalityOptions,
  ProfessionOptions,
} from "src/sections/persona/PersonaCreateEdit/common";

// Edit one scenario.
//
// The same fields the studio design shows, in the same sections and the same order. What differs is
// which of them accept a change: a scenario is proved before it is kept, the world is set up, a
// known-good run has to pass and the checks have to fail when nothing is done, and that proof pins
// the caller's identity, the task and the checks. Those fields are shown read-only rather than left
// out, because seeing what a scenario holds is most of why anyone opens it.
const readable = (name) => String(name || "").replace(/[_-]+/g, " ").trim();

// These lists carry a lowercase value and a display label. A scenario's persona is written with the
// label casing, so matching on the label is what makes an existing accent or language select itself
// instead of coming up blank.
const pick = (options) =>
  (options || []).map((one) => (typeof one === "string" ? one : one.label ?? one.value));

// Where the call is made from. The voice runtime maps each of these to a real ambience clip, so
// these are the settings that actually reach a run.
const NOISE = ["off", "home", "office", "retail", "street", "vehicle", "transit", "outdoors"];

const noiseOf = (value) => {
  if (!value) return "off";
  return typeof value === "string" ? value : "home";
};

const draftOf = (scenario) => {
  const persona = scenario.persona || {};
  return {
    tests: scenario.tests || "",
    branch: scenario.branch || "",
    keywords: persona.keywords || [],
    personality: persona.personality || "",
    communication_style: persona.communication_style || "",
    accent: persona.accent || "",
    languages: persona.languages || [],
    occupation: persona.occupation || "",
    location: persona.location || "",
    max_turns: scenario.max_turns || 10,
    background_noise: noiseOf(scenario.background_noise),
  };
};

export default function ScenarioEditForm({ scenario, busy, onCancel, onSave, editableFields }) {
  const persona = scenario.persona || {};
  const initial = useMemo(() => draftOf(scenario), [scenario]);
  const [form, setForm] = useState(initial);
  const set = (key) => (value) => setForm((prev) => ({ ...prev, [key]: value }));
  const conversational = Boolean(scenario.persona);
  // A save re-checks the scenario, and a change the harness judges consequential costs a model call
  // and a fresh proof. Saving an untouched form would pay that for nothing.
  const dirty = JSON.stringify(form) !== JSON.stringify(initial);

  // Absent list means nothing is editable.
  const editable = (field) => (editableFields || []).includes(field);

  return (
    <Stack sx={{ height: "100%" }}>
      <Stack
        direction="row"
        alignItems="center"
        spacing={2}
        sx={{ px: 2.5, py: 2, borderBottom: "1px solid", borderColor: "divider", flexShrink: 0 }}
      >
        <Box flex={1} minWidth={0}>
          <Typography sx={{ typography: "m2", fontWeight: 600 }}>Edit scenario</Typography>
          <Typography noWrap sx={{ typography: "s2", color: "text.subtitle" }}>
            {readable(scenario.name)}
          </Typography>
        </Box>
        <IconButton size="small" onClick={onCancel}>
          <Iconify icon="solar:close-circle-linear" width={18} sx={{ color: "text.subtitle" }} />
        </IconButton>
      </Stack>

      <Stack spacing={2.5} sx={{ flex: 1, overflow: "auto", p: 2.5 }}>
        <SectionHeader
          title="Directly editable"
          hint="These parts do not touch the proof, so they save straight to the suite."
        />

        <Field
          label="Name"
          value={scenario.name}
          readOnly
          mono
          help="Read-only. It is the folder this scenario lives in and how results refer to it."
        />
        <Field
          label="Use case"
          value={scenario.use_case}
          readOnly
          help="Read-only. It comes from the agent's contract and groups this row with its siblings."
        />
        <Field
          label="Branch"
          value={scenario.branch}
          readOnly
          help="Read-only. The suite is checked for two scenarios sharing a use case and branch."
        />
        <Field
          label="Passes when"
          value={editable("tests") ? form.tests : scenario.tests}
          onChange={editable("tests") ? set("tests") : undefined}
          readOnly={!editable("tests")}
          rows={2}
          help="What a pass looks like. Shown in results."
        />
        <Autocomplete
          multiple
          freeSolo
          size="small"
          options={[]}
          value={form.keywords}
          onChange={(event, value) => set("keywords")(value)}
          renderTags={(value, getTagProps) =>
            value.map((option, index) => (
              <Chip size="small" label={option} {...getTagProps({ index })} key={option} />
            ))
          }
          renderInput={(params) => (
            <TextField
              {...params}
              label="Keywords"
              helperText="How you find this scenario in a large suite. Not part of the call."
            />
          )}
        />

        {conversational && (
          <>
            <SectionHeader
              title="Persona"
              hint="Who is on the other end of the run. The world is seeded around this person, so changing it here alone would leave the two disagreeing."
            />

            <Field
              label="Name"
              value={persona.name}
              readOnly
              help="Read-only. This name is written into the world the scenario was proved against."
            />
            <Stack direction={{ xs: "column", sm: "row" }} spacing={2}>
              <Field label="Age group" value={persona.age_group} readOnly fullWidth />
              <Field label="Gender" value={persona.gender} readOnly fullWidth />
            </Stack>
            <Field
              label="Opens with"
              value={persona.initial_message}
              readOnly
              rows={2}
              help="Read-only. On an adversarial scenario the opening line is the test."
            />
            <Field
              label="What the caller wants"
              value={scenario.instruction}
              readOnly
              rows={3}
              help="Read-only. The checks were written against this."
            />
            <Field
              label="What is measured"
              value={(scenario.sub_goals || []).map(readable).join(", ")}
              readOnly
              help="Read-only. Named entries of a catalogue shared across the suite, so results roll up."
            />

            <SectionHeader
              title="Caller"
              hint="How the caller comes across. An agent decides nothing from how somebody sounds, so none of this reaches the world or the checks."
            />

            <Stack direction={{ xs: "column", sm: "row" }} spacing={2}>
              <Choice
                label="Personality"
                value={form.personality}
                onChange={set("personality")}
                options={PersonalityOptions}
              />
              <Choice
                label="Communication style"
                value={form.communication_style}
                onChange={set("communication_style")}
                options={CommunicationStyleOptions}
              />
            </Stack>

            <Stack direction={{ xs: "column", sm: "row" }} spacing={2}>
              <Autocomplete
                fullWidth
                size="small"
                options={pick(AccentOptions)}
                value={form.accent}
                onChange={(event, value) => set("accent")(value || "")}
                renderInput={(params) => <TextField {...params} label="Accent" />}
              />
              <Autocomplete
                multiple
                fullWidth
                size="small"
                options={pick(LanguageOptions)}
                value={form.languages}
                onChange={(event, value) => set("languages")(value)}
                renderInput={(params) => <TextField {...params} label="Language" />}
              />
            </Stack>

            <Stack direction={{ xs: "column", sm: "row" }} spacing={2}>
              <Autocomplete
                freeSolo
                fullWidth
                size="small"
                options={pick(ProfessionOptions)}
                value={form.occupation}
                onChange={(event, value) => set("occupation")(value || "")}
                renderInput={(params) => <TextField {...params} label="Profession" />}
              />
              <Autocomplete
                freeSolo
                fullWidth
                size="small"
                options={pick(LocationOptions)}
                value={form.location}
                onChange={(event, value) => set("location")(value || "")}
                renderInput={(params) => <TextField {...params} label="Location" />}
              />
            </Stack>

            <SectionHeader
              title="Call constraints"
              hint="Every scenario carries defaults. Overriding them here is safe."
            />

            <Box>
              <Stack direction="row" alignItems="center" spacing={1}>
                <Typography sx={{ typography: "s2", fontWeight: 600, flex: 1 }}>
                  Max turns
                </Typography>
                <Typography sx={{ typography: "s2", fontVariantNumeric: "tabular-nums" }}>
                  {form.max_turns}
                </Typography>
              </Stack>
              <Slider
                size="small"
                min={2}
                max={40}
                value={form.max_turns}
                onChange={(event, value) => set("max_turns")(value)}
              />
              <Typography sx={{ typography: "s3", color: "text.subtitle" }}>
                How long the call may go before it is called off.
              </Typography>
            </Box>

            <Box>
              <Typography sx={{ typography: "s2", fontWeight: 600, mb: 0.75 }}>
                Background noise
              </Typography>
              <ToggleButtonGroup
                size="small"
                exclusive
                value={form.background_noise}
                onChange={(event, value) => value && set("background_noise")(value)}
                sx={{
                  flexWrap: "wrap",
                  gap: 0.5,
                  "& .MuiToggleButton-root": {
                    typography: "s2",
                    fontWeight: 600,
                    textTransform: "none",
                    px: 1.5,
                    py: 0.375,
                    color: "text.secondary",
                    border: "1px solid",
                    borderColor: "divider",
                    borderRadius: 1,
                    "&.Mui-selected": {
                      bgcolor: (theme) => alpha(theme.palette.primary.main, 0.12),
                      color: "primary.main",
                      borderColor: "primary.main",
                    },
                  },
                }}
              >
                {NOISE.map((one) => (
                  <ToggleButton key={one} value={one}>
                    {one}
                  </ToggleButton>
                ))}
              </ToggleButtonGroup>
              <Typography sx={{ typography: "s3", color: "text.subtitle", mt: 0.75 }}>
                Where the call is made from. Each of these is a real ambience the run plays.
              </Typography>
            </Box>
          </>
        )}

        <Stack
          direction="row"
          spacing={1.25}
          alignItems="flex-start"
          sx={{
            p: 1.75,
            borderRadius: 1.25,
            border: "1px solid",
            borderColor: (theme) => alpha(theme.palette.primary.main, 0.24),
            bgcolor: (theme) => alpha(theme.palette.primary.main, 0.06),
          }}
        >
          <Iconify
            icon="solar:shield-check-linear"
            width={15}
            sx={{ color: "primary.main", flexShrink: 0, mt: "1px" }}
          />
          <Typography sx={{ typography: "s3", color: "text.secondary" }}>
            A read-only field is one this scenario was proved against: the world was set up, a
            known-good run had to pass, and the checks had to fail when nothing was done. Saving
            re-checks the scenario against that same proof.
          </Typography>
        </Stack>
      </Stack>

      <Stack
        direction="row"
        justifyContent="flex-end"
        spacing={1}
        sx={{ px: 2.5, py: 1.75, borderTop: "1px solid", borderColor: "divider", flexShrink: 0 }}
      >
        <Button
          onClick={onCancel}
          disabled={busy}
          sx={{ typography: "s2", fontWeight: 600, color: "text.secondary" }}
        >
          Cancel
        </Button>
        <Button
          variant="contained"
          size="small"
          disabled={busy || !dirty}
          onClick={() => onSave(form)}
          sx={{ typography: "s2", fontWeight: 700 }}
        >
          Save scenario
        </Button>
      </Stack>
    </Stack>
  );
}

ScenarioEditForm.propTypes = {
  scenario: PropTypes.object.isRequired,
  busy: PropTypes.bool,
  onCancel: PropTypes.func.isRequired,
  onSave: PropTypes.func.isRequired,
  editableFields: PropTypes.arrayOf(PropTypes.string),
};

function SectionHeader({ title, hint }) {
  return (
    <Box>
      <Typography
        sx={{
          typography: "s3",
          fontWeight: 700,
          color: "text.primary",
          textTransform: "uppercase",
          letterSpacing: 0.6,
          mb: 0.375,
        }}
      >
        {title}
      </Typography>
      <Typography sx={{ typography: "s3", color: "text.subtitle" }}>{hint}</Typography>
    </Box>
  );
}

SectionHeader.propTypes = { title: PropTypes.string, hint: PropTypes.string };

// One text field, whether or not it accepts a change. A read-only field is the same control as the
// one above it rather than a different kind of thing, which is what keeps the panel one form.
function Field({ label, value, onChange, readOnly, rows, mono, help, fullWidth = true }) {
  return (
    <TextField
      size="small"
      label={label}
      value={value || ""}
      disabled={readOnly}
      fullWidth={fullWidth}
      multiline={Boolean(rows)}
      minRows={rows}
      helperText={help}
      onChange={onChange ? (event) => onChange(event.target.value) : undefined}
      InputProps={{
        sx: {
          typography: "s2",
          ...(mono && { fontFamily: "ui-monospace, Menlo, monospace" }),
        },
      }}
      sx={{
        "& .MuiInputBase-input.Mui-disabled, & .MuiInputBase-inputMultiline.Mui-disabled": {
          WebkitTextFillColor: (theme) => theme.palette.text.secondary,
        },
      }}
    />
  );
}

Field.propTypes = {
  label: PropTypes.string,
  value: PropTypes.string,
  onChange: PropTypes.func,
  readOnly: PropTypes.bool,
  rows: PropTypes.number,
  mono: PropTypes.bool,
  help: PropTypes.string,
  fullWidth: PropTypes.bool,
};

function Choice({ label, value, onChange, options }) {
  return (
    <TextField
      select
      fullWidth
      size="small"
      label={label}
      value={value}
      onChange={(event) => onChange(event.target.value)}
      InputProps={{ sx: { typography: "s2" } }}
    >
      {options.map((one) => (
        <MenuItem key={one.value ?? one} value={one.value ?? one} sx={{ typography: "s2" }}>
          {one.label ?? one}
        </MenuItem>
      ))}
    </TextField>
  );
}

Choice.propTypes = {
  label: PropTypes.string,
  value: PropTypes.string,
  onChange: PropTypes.func,
  options: PropTypes.array,
};
