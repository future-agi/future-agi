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
const readable = (name) => String(name || "").replace(/[_-]+/g, " ").trim();

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
        <LockedRow
          label="Caller"
          value={[persona.name, persona.age_group].filter(Boolean).join(", ")}
          prompt={`change the caller in ${scenario.name} to a different person`}
        />
        <LockedRow
          label="Opens with"
          value={persona.initial_message}
          prompt={`reword the opening line in ${scenario.name}`}
        />
        <LockedRow
          label="What the caller wants"
          value={scenario.instruction}
          prompt={`change what the caller wants in ${scenario.name}`}
        />
        <LockedRow
          label="What is measured"
          value={(scenario.sub_goals || []).map(readable).join(", ")}
          prompt={`change what is measured in ${scenario.name}`}
        />
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

// A field the scenario is proved around, shown rather than hidden. Seeing the value and being told
// how to change it is more use than an empty space where a control might have been, and the prompt
// is the exact thing to say once the chat is wired.
function LockedRow({ label, value, prompt }) {
  return (
    <Box
      sx={{
        p: 1.5,
        borderRadius: 1.25,
        border: "1px solid",
        borderColor: "divider",
        bgcolor: "background.neutral",
      }}
    >
      <Stack direction="row" alignItems="flex-start" spacing={1}>
        <Iconify
          icon="solar:lock-keyhole-minimalistic-linear"
          width={13}
          sx={{ color: "text.subtitle", mt: "3px", flexShrink: 0 }}
        />
        <Box flex={1} minWidth={0}>
          <Typography
            sx={{
              typography: "s3",
              fontWeight: 700,
              color: "text.secondary",
              mb: 0.375,
              textTransform: "uppercase",
              letterSpacing: 0.4,
            }}
          >
            {label}
          </Typography>
          <Typography
            sx={{
              typography: "s2",
              display: "-webkit-box",
              WebkitLineClamp: 3,
              WebkitBoxOrient: "vertical",
              overflow: "hidden",
            }}
          >
            {value || "\u2014"}
          </Typography>
          <Stack direction="row" alignItems="center" spacing={0.75} sx={{ mt: 1 }}>
            <Iconify
              icon="solar:chat-round-line-linear"
              width={13}
              sx={{ color: "text.subtitle" }}
            />
            <Typography
              noWrap
              sx={{
                typography: "s3",
                fontFamily: "ui-monospace, Menlo, monospace",
                color: "text.subtitle",
                minWidth: 0,
              }}
            >
              {prompt}
            </Typography>
          </Stack>
        </Box>
      </Stack>
    </Box>
  );
}

LockedRow.propTypes = {
  label: PropTypes.string,
  value: PropTypes.string,
  prompt: PropTypes.string,
};
