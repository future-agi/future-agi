import PropTypes from "prop-types";
import ActorField from "./ActorField";
import { ACTORS_COPY } from "./actors.constants";
import { ACTOR_FORM_SHAPE, OPTIONS_SHAPE } from "./actors.shapes";
import { fallbackActs, fallbackKey, parsedArguments } from "./actorUtils";

const F = ACTORS_COPY.worldActors.form;

// How long the world waits for them and what happens when they don't answer.
export default function ActorFallbackFields({
  form,
  actor,
  fallbackWords,
  others,
}) {
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
  const field = (name, label, extra = {}) => (
    <ActorField form={form} name={name} label={label} {...extra} />
  );
  return (
    <>
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

ActorFallbackFields.propTypes = {
  form: ACTOR_FORM_SHAPE.isRequired,
  // The cast's actor, or its `new_actor` for a new one.
  actor: PropTypes.object.isRequired,
  // Fallbacks that are neither an action nor an actor.
  fallbackWords: OPTIONS_SHAPE.isRequired,
  others: PropTypes.arrayOf(
    PropTypes.shape({ id: PropTypes.string, name: PropTypes.string }),
  ).isRequired,
};
