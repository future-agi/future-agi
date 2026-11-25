import PropTypes from "prop-types";
import ActorField from "./ActorField";
import { ACTORS_COPY } from "./actors.constants";
import { ACTOR_FORM_SHAPE, OPTIONS_SHAPE } from "./actors.shapes";
import {
  KEPT,
  fallbackActs,
  fallbackKey,
  parsedArguments,
  whereProblem,
  words,
} from "./actorUtils";

const F = ACTORS_COPY.worldActors.form;

// The trigger kinds the chosen way of joining offers and the cast's labels for them; only the inputs the picked kind
// takes (`triggerKeys`) show.
export default function ActorTriggerFields({
  form,
  actor,
  triggerOptions,
  triggerKeys,
  actionIds,
  collections,
  fallbackWords,
  others,
}) {
  const { initial } = form;
  const takes = (key) => triggerKeys.includes(key);
  // A stored kind the chosen way of joining no longer offers reads by its own name.
  const startModeLabel =
    initial.mode === KEPT
      ? actor.when_label
      : triggerOptions.find((one) => one.value === initial.mode)?.label ||
        words(initial.mode);
  const fallbackOptions = [
    ...fallbackWords.map((one) => ({
      value: fallbackKey({ kind: one.value, value: one.value }),
      label: one.label,
    })),
    ...form.value("permitted").map((id) => ({
      value: fallbackKey({ kind: "action", value: id }),
      label: F.takesAction(id),
    })),
    ...others.map((one) => ({
      value: fallbackKey({ kind: "actor", value: one.id }),
      label: F.answeredBy(one.name || one.id),
    })),
  ];
  const acts = fallbackActs(form.value("fallback"));
  const argumentsWrong =
    acts && parsedArguments(form.value("fallback_arguments")) === null
      ? F.fallbackArgumentsWrong
      : "";
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
      {field("timeout", F.timeout, {
        type: "number",
        helperText: F.timeoutHelp,
        inputProps: { min: 0 },
      })}
      {field("fallback", F.fallback, {
        options: fallbackOptions,
        startLabel: actor.fallback.label,
      })}
      {acts &&
        field("fallback_arguments", F.fallbackArguments, {
          error: Boolean(argumentsWrong || form.problems.fallback_arguments),
          helperText: argumentsWrong || F.fallbackArgumentsHelp,
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
  // Fallbacks that are neither an action nor an actor.
  fallbackWords: OPTIONS_SHAPE.isRequired,
  others: PropTypes.arrayOf(
    PropTypes.shape({ id: PropTypes.string, name: PropTypes.string }),
  ).isRequired,
};
