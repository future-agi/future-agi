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

export default function ActorTriggerFields({
  form,
  actor,
  triggerKinds,
  actionIds,
  collections,
  fallbackWords,
  others,
}) {
  const existing = Boolean(actor.id);
  const { initial } = form;
  const mode = form.value("mode");
  const modeLabel = (one) => F.modes[one] || words(one);
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
  const where =
    mode === "conditional" ? whereProblem(form.value("triggerWhere")) : "";
  const field = (name, label, extra = {}) => (
    <ActorField form={form} name={name} label={label} {...extra} />
  );
  return (
    <>
      {field("mode", F.trigger, {
        options: triggerKinds.map((one) => ({
          value: one,
          label: modeLabel(one),
        })),
        helperText: F.triggerHelp,
        startLabel:
          initial.mode === KEPT ? actor.when_label : modeLabel(initial.mode),
      })}
      {mode === "on_tool" &&
        field("triggerTool", F.triggerTool, {
          options: actionIds.map((id) => ({ value: id, label: id })),
          startLabel: initial.triggerTool,
        })}
      {mode === "on_keyword" &&
        field("triggerWords", F.triggerWords, {
          helperText: F.triggerWordsHelp,
        })}
      {mode === "mid_journey" &&
        field("triggerTurn", F.triggerTurn, {
          type: "number",
          inputProps: { min: 1 },
        })}
      {mode === "after_seconds" &&
        field("triggerSeconds", F.triggerSeconds, {
          type: "number",
          inputProps: { min: 0 },
        })}
      {mode === "conditional" && (
        <>
          {field("triggerCollection", F.triggerCollection, {
            options: collections.map((one) => ({ value: one, label: one })),
            startLabel: initial.triggerCollection,
          })}
          {field("triggerWhere", F.triggerWhere, {
            error: Boolean(where),
            helperText: where || F.triggerWhereHelp,
          })}
        </>
      )}
      {field("timeout", F.timeout, {
        type: "number",
        helperText: F.timeoutHelp,
        inputProps: { min: 0 },
      })}
      {field("fallback", F.fallback, {
        options: fallbackOptions,
        startLabel: existing ? actor.fallback.label : undefined,
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
  // `{side}` for a new actor.
  actor: PropTypes.object.isRequired,
  triggerKinds: PropTypes.arrayOf(PropTypes.string).isRequired,
  actionIds: PropTypes.arrayOf(PropTypes.string).isRequired,
  collections: PropTypes.arrayOf(PropTypes.string).isRequired,
  // Fallbacks that are neither an action nor an actor.
  fallbackWords: OPTIONS_SHAPE.isRequired,
  others: PropTypes.arrayOf(
    PropTypes.shape({ id: PropTypes.string, name: PropTypes.string }),
  ).isRequired,
};
