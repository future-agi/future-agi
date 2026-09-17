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
// Every scenario is proved before it is kept: the world is set up, a known-good run has to pass, and
// the checks have to fail when nothing is done. That proof pins the caller's identity, the task, the
// setup and the checks, so none of them is offered here. The drawer holds what a person may change
// and nothing else: a control that cannot be used is worse than no control, and a row explaining
// what you may not do is worse still.
//
//   Every agent      passes when, branch, keywords
//   Conversational   personality, communication style, accent, language, profession, location
//   Voice            max turns, background noise
const readable = (name) => String(name || "").replace(/[_-]+/g, " ").trim();

// These lists carry a lowercase value and a display label. A scenario's persona is written with the
// label casing, so matching on the label is what makes an existing accent or language select itself
// instead of coming up blank.
const pick = (options) =>
  (options || []).map((one) => (typeof one === "string" ? one : one.label ?? one.value));

// Where the call is made from. The voice runtime maps each of these to a real ambience clip, so
// these are the settings that actually reach a run; anything else would be a label over silence.
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

export default function ScenarioEditForm({ scenario, busy, onCancel, onSave }) {
  const initial = useMemo(() => draftOf(scenario), [scenario]);
  const [form, setForm] = useState(initial);
  const set = (key) => (value) => setForm((prev) => ({ ...prev, [key]: value }));
  const conversational = Boolean(scenario.persona);
  // A save re-checks the scenario, and a change the harness judges consequential costs a model call
  // and a fresh proof. Saving an untouched form would pay that for nothing.
  const dirty = JSON.stringify(form) !== JSON.stringify(initial);

  return (
    <Stack sx={{ height: "100%" }}>
      <Stack
        direction="row"
        alignItems="center"
        spacing={2}
        sx={{ px: 2.5, py: 2, borderBottom: "1px solid", borderColor: "divider", flexShrink: 0 }}
      >
        <Box flex={1} minWidth={0}>
          <Typography noWrap sx={{ typography: "m2", fontWeight: 600 }}>
            {readable(scenario.name)}
          </Typography>
          <Typography noWrap sx={{ typography: "s2", color: "text.subtitle" }}>
            {scenario.use_case}
          </Typography>
        </Box>
        <IconButton size="small" onClick={onCancel}>
          <Iconify icon="solar:close-circle-linear" width={18} sx={{ color: "text.subtitle" }} />
        </IconButton>
      </Stack>

      <Stack spacing={2.5} sx={{ flex: 1, overflow: "auto", p: 2.5 }}>
        <SectionHeader
          title="Scenario"
          hint="None of this is part of what the scenario was proved against, so it saves straight to the suite."
        />

        <TextField
          size="small"
          label="Passes when"
          multiline
          minRows={2}
          value={form.tests}
          onChange={(event) => set("tests")(event.target.value)}
          helperText="What a pass looks like. Shown in results."
          InputProps={{ sx: { typography: "s2" } }}
        />
        <TextField
          size="small"
          label="Branch"
          value={form.branch}
          onChange={(event) => set("branch")(event.target.value)}
          helperText="What makes this one different from its siblings in the same use case."
          InputProps={{ sx: { typography: "s2" } }}
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
              helperText="Filters the suite. Never reaches a run."
            />
          )}
        />

        {conversational && (
          <>
            <SectionHeader
              title="Caller"
              hint="How the caller comes across. An agent decides nothing from how somebody sounds, so none of this reaches the world or the checks."
            />

            <Stack direction={{ xs: "column", sm: "row" }} spacing={2}>
              <TextField
                select
                fullWidth
                size="small"
                label="Personality"
                value={form.personality}
                onChange={(event) => set("personality")(event.target.value)}
                InputProps={{ sx: { typography: "s2" } }}
              >
                {PersonalityOptions.map((one) => (
                  <MenuItem
                    key={one.value ?? one}
                    value={one.value ?? one}
                    sx={{ typography: "s2" }}
                  >
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
                InputProps={{ sx: { typography: "s2" } }}
              >
                {CommunicationStyleOptions.map((one) => (
                  <MenuItem
                    key={one.value ?? one}
                    value={one.value ?? one}
                    sx={{ typography: "s2" }}
                  >
                    {one.label ?? one}
                  </MenuItem>
                ))}
              </TextField>
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
            This scenario was proved before it was kept: the world was set up, a known-good run had
            to pass, and the checks had to fail when nothing was done. Saving re-checks it against
            that same proof.
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
