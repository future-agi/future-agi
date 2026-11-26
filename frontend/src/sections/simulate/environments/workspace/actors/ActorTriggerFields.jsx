import PropTypes from "prop-types";
import ActorField from "./ActorField";
import { ACTORS_COPY } from "./actors.constants";
import { ACTOR_FORM_SHAPE, OPTIONS_SHAPE } from "./actors.shapes";
import { KEPT, whereProblem, words } from "./actorUtils";

const F = ACTORS_COPY.worldActors.form;

// The trigger kinds the chosen way of joining offers and the cast's labels for them; only the inputs the picked kind
// takes (`triggerKeys`) show. Waits and the fallback are in ActorFallbackFields.
export default function ActorTriggerFields({
  form,
  actor,
  triggerOptions,
  triggerKeys,
  actionIds,
  collections,
}) {
  const { initial } = form;
  const takes = (key) => triggerKeys.includes(key);
  // A stored kind the chosen way of joining no longer offers reads by its own name.
  const startModeLabel =
    initial.mode === KEPT
      ? actor.when_label
      : triggerOptions.find((one) => one.value === initial.mode)?.label ||
        words(initial.mode);
  const where = takes("where") ? whereProblem(form.value("triggerWhere")) : "";
  const field = (name, label, extra = {}) => (
    <ActorField form={form} name={name} label={label} {...extra} />
  );
  return (
    <>
      {field("mode", F.trigger, {
        options: triggerOptions,
        helperText: F.triggerHelp,
        startLabel: startModeLabel,
      })}
      {takes("tool") &&
        field("triggerTool", F.triggerTool, {
          options: actionIds.map((id) => ({ value: id, label: id })),
          startLabel: initial.triggerTool,
        })}
      {takes("words") &&
        field("triggerWords", F.triggerWords, {
          helperText: F.triggerWordsHelp,
        })}
      {takes("turn") &&
        field("triggerTurn", F.triggerTurn, {
          type: "number",
          inputProps: { min: 1 },
        })}
      {takes("seconds") &&
        field("triggerSeconds", F.triggerSeconds, {
          type: "number",
          inputProps: { min: 0 },
        })}
      {takes("collection") &&
        field("triggerCollection", F.triggerCollection, {
          options: collections.map((one) => ({ value: one, label: one })),
          startLabel: initial.triggerCollection,
        })}
      {takes("where") &&
        field("triggerWhere", F.triggerWhere, {
          error: Boolean(where),
          helperText: where || F.triggerWhereHelp,
        })}
    </>
  );
}

ActorTriggerFields.propTypes = {
  form: ACTOR_FORM_SHAPE.isRequired,
  // The cast's actor, or its `new_actor` for a new one.
  actor: PropTypes.object.isRequired,
  // The chosen way of joining's `trigger_options`.
  triggerOptions: OPTIONS_SHAPE.isRequired,
  // The trigger keys the picked kind takes, from the cast's `trigger_fields`.
  triggerKeys: PropTypes.arrayOf(PropTypes.string).isRequired,
  actionIds: PropTypes.arrayOf(PropTypes.string).isRequired,
  collections: PropTypes.arrayOf(PropTypes.string).isRequired,
};
