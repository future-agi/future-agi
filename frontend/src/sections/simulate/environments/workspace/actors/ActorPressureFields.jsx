import { Typography } from "@mui/material";
import ActorField from "./ActorField";
import { ACTORS_COPY } from "./actors.constants";
import { ACTOR_FORM_SHAPE } from "./actors.shapes";

const F = ACTORS_COPY.worldActors.form;

// What the actor pushes for and when they give up: `goal` and `stop_when`.
export default function ActorPressureFields({ form }) {
  return (
    <>
      <Typography sx={{ typography: "s2", fontWeight: 600 }}>
        {F.pressure}
      </Typography>
      <Typography sx={{ typography: "s3", color: "text.secondary" }}>
        {F.pressureHelp}
      </Typography>
      <ActorField
        form={form}
        name="goal"
        label={F.goal}
        multiline
        minRows={2}
        helperText={F.goalHelp}
      />
      <ActorField
        form={form}
        name="stop_when"
        label={F.stopWhen}
        multiline
        minRows={2}
      />
    </>
  );
}

ActorPressureFields.propTypes = {
  form: ACTOR_FORM_SHAPE.isRequired,
};
