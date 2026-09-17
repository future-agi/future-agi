import {
  Autocomplete,
  Box,
  Button,
  Chip,
  Divider,
  MenuItem,
  Stack,
  Switch,
  TextField,
  Typography,
} from "@mui/material";
import PropTypes from "prop-types";
import React, { useState } from "react";

import Iconify from "src/components/iconify";
import {
  AccentOptions,
  CommunicationStyleOptions,
  LanguageOptions,
  LocationOptions,
  PersonalityOptions,
  ProfessionOptions,
} from "src/sections/persona/PersonaCreateEdit/common";

// Only what a person may change. A scenario is proved against a world before it is kept, and the
// fields left out here are the ones that proof depends on: the caller's identity is seeded into
// the world, and what they want is what the checks were written against. Showing them disabled
// would read as broken rather than deliberate, so they are absent and explained instead.
// These lists carry a lowercase value and a display label. A scenario's persona is written with
// the label casing, so matching on the label is what makes an existing accent or language select
// itself instead of coming up blank.
const pick = (options) =>
  (options || []).map((one) => (typeof one === "string" ? one : one.label ?? one.value));

export default function ScenarioEditForm({ scenario, busy, onCancel, onSave }) {
  const persona = scenario.persona || {};
  const [form, setForm] = useState({
    tests: scenario.tests || "",
    branch: scenario.branch || "",
    keywords: persona.keywords || [],
    personality: persona.personality || "",
    communication_style: persona.communication_style || "",
    accent: persona.accent || "",
    languages: persona.languages || [],
    occupation: persona.occupation || "",
    location: persona.location || "",
    background_noise: Boolean(scenario.background_noise),
  });
  const set = (key) => (value) => setForm((prev) => ({ ...prev, [key]: value }));
  const conversational = Boolean(scenario.persona);

  return (
    <Stack spacing={2.5} sx={{ mt: 2 }}>
      <Stack spacing={2}>
        <TextField
          fullWidth
          multiline
          minRows={2}
          size="small"
          label="Passes when"
          helperText="What this scenario claims to check. Shown in results."
          value={form.tests}
          onChange={(event) => set("tests")(event.target.value)}
        />
        <TextField
          fullWidth
          size="small"
          label="What makes this one different"
          helperText="Tells it apart from the others in the same use case."
          value={form.branch}
          onChange={(event) => set("branch")(event.target.value)}
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
              helperText="Used to filter the suite. Never affects a run."
            />
          )}
        />
      </Stack>

      {conversational && (
        <>
          <Divider textAlign="left">
            <Typography variant="caption" color="text.secondary">
              The caller
            </Typography>
          </Divider>

          <Stack spacing={2}>
            <TextField
              select
              fullWidth
              size="small"
              label="Personality"
              value={form.personality}
              onChange={(event) => set("personality")(event.target.value)}
            >
              {PersonalityOptions.map((one) => (
                <MenuItem key={one.value ?? one} value={one.value ?? one}>
                  {one.label ?? one}
                </MenuItem>
              ))}
            </TextField>

            <TextField
              select
              fullWidth
              size="small"
              label="Communication style"
              value={form.communication_style}
              onChange={(event) => set("communication_style")(event.target.value)}
            >
              {CommunicationStyleOptions.map((one) => (
                <MenuItem key={one.value ?? one} value={one.value ?? one}>
                  {one.label ?? one}
                </MenuItem>
              ))}
            </TextField>

            <Autocomplete
              size="small"
              options={pick(AccentOptions)}
              value={form.accent}
              onChange={(event, value) => set("accent")(value || "")}
              renderInput={(params) => <TextField {...params} label="Accent" />}
            />

            <Autocomplete
              multiple
              size="small"
              options={pick(LanguageOptions)}
              value={form.languages}
              onChange={(event, value) => set("languages")(value)}
              renderInput={(params) => (
                <TextField
                  {...params}
                  label="Languages"
                  helperText="Set only what this agent supports."
                />
              )}
            />

            <Autocomplete
              freeSolo
              size="small"
              options={pick(ProfessionOptions)}
              value={form.occupation}
              onChange={(event, value) => set("occupation")(value || "")}
              renderInput={(params) => <TextField {...params} label="Profession" />}
            />

            <Autocomplete
              freeSolo
              size="small"
              options={pick(LocationOptions)}
              value={form.location}
              onChange={(event, value) => set("location")(value || "")}
              renderInput={(params) => <TextField {...params} label="Location" />}
            />

            <Stack direction="row" alignItems="center" justifyContent="space-between">
              <Box>
                <Typography variant="body2">Background noise</Typography>
                <Typography variant="caption" color="text.secondary">
                  Tests how the agent copes in real conditions.
                </Typography>
              </Box>
              <Switch
                checked={form.background_noise}
                onChange={(event) => set("background_noise")(event.target.checked)}
              />
            </Stack>
          </Stack>
        </>
      )}

      <Stack spacing={1}>
        <Stack direction="row" spacing={1} alignItems="flex-start">
          <Iconify icon="eva:lock-outline" width={15} sx={{ mt: 0.3, color: "text.disabled" }} />
          <Typography variant="caption" color="text.secondary">
            The caller&apos;s name, age and opening line are fixed. The world is set up around them,
            so changing one here would leave the two disagreeing.
          </Typography>
        </Stack>
        <Stack direction="row" spacing={1} alignItems="flex-start">
          <Iconify
            icon="eva:message-square-outline"
            width={15}
            sx={{ mt: 0.3, color: "text.disabled" }}
          />
          <Typography variant="caption" color="text.secondary">
            To change what the caller wants or what is measured, use the chat. Those need the
            scenario checked against the world again.
          </Typography>
        </Stack>
      </Stack>

      <Stack direction="row" spacing={1} justifyContent="flex-end">
        <Button size="small" onClick={onCancel} disabled={busy}>
          Cancel
        </Button>
        <Button size="small" variant="contained" disabled={busy} onClick={() => onSave(form)}>
          Save
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
};
