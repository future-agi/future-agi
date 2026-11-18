import PropTypes from "prop-types";
import { Stack, Typography } from "@mui/material";
import ActorField from "./ActorField";
import ActorLoudness from "./ActorLoudness";
import { ACTORS_COPY } from "./actors.constants";
import { ACTOR_FORM_SHAPE, OPTIONS_SHAPE } from "./actors.shapes";

const F = ACTORS_COPY.worldActors.form;

export default function ActorVoiceFields({ form, options, loudness }) {
  // Empty is "Any": a voice is chosen for them.
  const voice = (name, label) => (
    <ActorField
      form={form}
      name={name}
      label={label}
      options={[{ value: "", label: F.anyVoice }, ...options[name]]}
      fullWidth
    />
  );
  return (
    <>
      <Typography sx={{ typography: "s2", fontWeight: 600 }}>
        {F.voice}
      </Typography>
      <Stack direction="row" spacing={1}>
        {voice("gender", F.gender)}
        {voice("age_group", F.ageGroup)}
        {voice("accent", F.accent)}
      </Stack>
      <Typography sx={{ typography: "s3", color: "text.secondary" }}>
        {F.voiceHelp}
      </Typography>
      {loudness && (
        <ActorLoudness
          value={form.value("loudness_ratio")}
          bounds={loudness}
          problem={form.problems.loudness_ratio}
          onChange={(next) => form.put("loudness_ratio", next)}
        />
      )}
    </>
  );
}

ActorVoiceFields.propTypes = {
  form: ACTOR_FORM_SHAPE.isRequired,
  options: PropTypes.shape({
    gender: OPTIONS_SHAPE.isRequired,
    age_group: OPTIONS_SHAPE.isRequired,
    accent: OPTIONS_SHAPE.isRequired,
  }).isRequired,
  // Null when the chosen channel has no loudness.
  loudness: PropTypes.object,
};
