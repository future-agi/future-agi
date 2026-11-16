import PropTypes from "prop-types";
import { Button, Collapse, Stack, Typography } from "@mui/material";
import ActorField from "./ActorField";
import { ACTORS_COPY } from "./actors.constants";
import { ACTOR_FORM_SHAPE } from "./actors.shapes";

const F = ACTORS_COPY.worldActors.form;

// Stored and editable, but a run does not read these fields yet.
export default function ActorMoreDetails({ form, open, onToggle, others }) {
  const many = (name, label) => (
    <ActorField form={form} name={name} label={label} multiline minRows={2} />
  );
  const startParent = form.initial.parent;
  return (
    <>
      <Button size="small" sx={{ alignSelf: "flex-start" }} onClick={onToggle}>
        {open ? F.lessDetails : F.moreDetails}
      </Button>
      <Collapse in={open} sx={{ flexShrink: 0 }}>
        <Stack spacing={2}>
          <Typography sx={{ typography: "s3", color: "text.secondary" }}>
            {F.moreDetailsHelp}
          </Typography>
          {(others.length > 0 || startParent) && (
            <ActorField
              form={form}
              name="parent"
              label={F.parent}
              options={[
                { value: "", label: F.nobody },
                ...others.map((one) => ({
                  value: one.id,
                  label: one.name || one.id,
                })),
              ]}
              helperText={F.parentHelp}
              startLabel={startParent}
            />
          )}
          <ActorField
            form={form}
            name="goal"
            label={F.goal}
            multiline
            minRows={2}
            helperText={F.goalHelp}
          />
          {many("may_agree", F.mayAgree)}
          {many("private", F.private)}
          {many("escalate_when", F.escalateWhen)}
          {many("stop_when", F.stopWhen)}
          <ActorField
            form={form}
            name="style"
            label={F.style}
            helperText={F.styleHelp}
          />
        </Stack>
      </Collapse>
    </>
  );
}

ActorMoreDetails.propTypes = {
  form: ACTOR_FORM_SHAPE.isRequired,
  open: PropTypes.bool.isRequired,
  onToggle: PropTypes.func.isRequired,
  others: PropTypes.arrayOf(
    PropTypes.shape({ id: PropTypes.string, name: PropTypes.string }),
  ).isRequired,
};
