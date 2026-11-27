import PropTypes from "prop-types";
import {
  Button,
  Collapse,
  Fade,
  Stack,
  Typography,
  useMediaQuery,
} from "@mui/material";
import ActorFallbackFields from "./ActorFallbackFields";
import ActorField from "./ActorField";
import ActorRecordsField from "./ActorRecordsField";
import { ACTORS_COPY } from "./actors.constants";
import { ACTOR_FORM_SHAPE } from "./actors.shapes";

const F = ACTORS_COPY.worldActors.form;

// Working settings most actors keep at their defaults, folded until asked for.
export default function ActorAdvancedFields({
  form,
  open,
  onToggle,
  actor,
  others,
  recordSources,
  fieldOptions,
  actorModels,
  capField,
}) {
  const field = (name, label, extra = {}) => (
    <ActorField form={form} name={name} label={label} {...extra} />
  );
  const many = (name, label) =>
    field(name, label, { multiline: true, minRows: 2 });
  const startParent = form.initial.parent;
  const still = useMediaQuery("(prefers-reduced-motion: reduce)");
  return (
    <>
      {/* The small text button's 8px inset, pulled back so its label starts where the fields do. */}
      <Button
        size="small"
        sx={{ alignSelf: "flex-start", ml: -1 }}
        onClick={onToggle}
      >
        {open ? F.lessAdvanced : F.advanced}
      </Button>
      {/* The fields fade in while the space opens, so nothing pops in. */}
      <Collapse in={open} timeout={still ? 0 : "auto"} sx={{ flexShrink: 0 }}>
        <Fade in={open} timeout={still ? 0 : undefined}>
          <Stack spacing={2}>
            <ActorRecordsField
              records={form.value("records")}
              sources={recordSources}
              problem={form.problems.records}
              onChange={(next) => form.put("records", next)}
            />
            <Stack direction="row" spacing={1}>
              {field("timing", F.answers, {
                options: fieldOptions.response_timing,
                sx: { flex: 1 },
              })}
              {form.value("timing") === "delayed" &&
                field("seconds", F.seconds, {
                  required: true,
                  type: "number",
                  sx: { width: 140 },
                  inputProps: { min: 0 },
                })}
            </Stack>
            {field("play", F.play, {
              options: fieldOptions.play,
              helperText: F.playHelp,
            })}
            <ActorFallbackFields
              form={form}
              actor={actor}
              fallbackWords={fieldOptions.fallback_word}
              others={others}
            />
            {actorModels.length > 0 &&
              field("model", F.model, {
                options: [
                  { value: "", label: F.environmentModel },
                  ...actorModels.map((one) => ({ value: one, label: one })),
                ],
                helperText: F.modelHelp,
                startLabel: form.initial.model,
              })}
            {capField &&
              field(capField, F.caps[capField], {
                type: "number",
                helperText: F.noLimitHelp,
                inputProps: { min: 1, step: 1 },
              })}
            <Typography sx={{ typography: "s3", color: "text.secondary" }}>
              {F.moreDetailsHelp}
            </Typography>
            {(others.length > 0 || startParent) &&
              field("parent", F.parent, {
                options: [
                  { value: "", label: F.nobody },
                  ...others.map((one) => ({
                    value: one.id,
                    label: one.name || one.id,
                  })),
                ],
                helperText: F.parentHelp,
                startLabel: startParent,
              })}
            {many("may_agree", F.mayAgree)}
            {many("private", F.private)}
            {many("escalate_when", F.escalateWhen)}
          </Stack>
        </Fade>
      </Collapse>
    </>
  );
}

ActorAdvancedFields.propTypes = {
  form: ACTOR_FORM_SHAPE.isRequired,
  open: PropTypes.bool.isRequired,
  onToggle: PropTypes.func.isRequired,
  // The cast's actor, or its `new_actor` for a new one.
  actor: PropTypes.object.isRequired,
  others: PropTypes.arrayOf(
    PropTypes.shape({ id: PropTypes.string, name: PropTypes.string }),
  ).isRequired,
  recordSources: PropTypes.arrayOf(
    PropTypes.shape({ store: PropTypes.string, collection: PropTypes.string }),
  ).isRequired,
  fieldOptions: PropTypes.object.isRequired,
  actorModels: PropTypes.arrayOf(PropTypes.string).isRequired,
  // The chosen way of joining's own cap, if it has one.
  capField: PropTypes.oneOf(["max_turns", "max_lines"]),
};
