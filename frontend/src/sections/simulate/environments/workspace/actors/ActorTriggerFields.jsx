import PropTypes from "prop-types";
import ActorField from "./ActorField";
import { ACTORS_COPY } from "./actors.constants";
import { ACTOR_FORM_SHAPE, OPTIONS_SHAPE } from "./actors.shapes";
import { KEPT, presetOf, presetsFor, whereProblem, words } from "./actorUtils";

const F = ACTORS_COPY.worldActors.form;

// Comes in is a join preset over the trigger kinds the chosen way of joining offers; a preset covering several asks
// which one. Only the inputs the picked kind takes (`triggerKeys`) show. Waits and the fallback are in
// ActorFallbackFields.
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
  const presets = presetsFor(triggerOptions);
  const mode = form.value("mode");
  const current = presets.find((one) => one.id === presetOf(mode, presets));
  const several = Boolean(current) && current.kinds.length > 1;
  // Picking a preset keeps the way already picked inside it, else the stored one, else its first.
  const choosePreset = (id) => {
    if (id === KEPT) {
      form.put("mode", KEPT);
      return;
    }
    const next = presets.find((one) => one.id === id);
    if (!next || next.kinds.includes(mode)) return;
    form.put(
      "mode",
      next.kinds.includes(initial.mode) ? initial.mode : next.kinds[0],
    );
  };
  const presetForm = {
    ...form,
    value: (name) =>
      name === "preset" ? presetOf(mode, presets) : form.value(name),
    set: () => (event) => choosePreset(event.target.value),
    initial: { ...initial, preset: presetOf(initial.mode, presets) },
    // With a second select, a problem with how they come in shows under that one.
    problems: several
      ? form.problems
      : { ...form.problems, preset: form.problems.mode },
  };
  const field = (name, label, extra = {}) => (
    <ActorField form={form} name={name} label={label} {...extra} />
  );
  return (
    <>
      <ActorField
        form={presetForm}
        name="preset"
        label={F.trigger}
        options={presets.map((one) => ({
          value: one.id,
          label: one.label || F.presets[one.id],
        }))}
        helperText={F.triggerHelp}
        startLabel={startModeLabel}
      />
      {several && (
        <ActorField
          // The stored way is listed here only when it belongs to this preset.
          form={{
            ...form,
            initial: {
              ...initial,
              mode: current.kinds.includes(initial.mode) ? initial.mode : "",
            },
          }}
          name="mode"
          label={F.presetWay}
          options={triggerOptions.filter((one) =>
            current.kinds.includes(one.value),
          )}
        />
      )}
      {takes("p") &&
        field("triggerChance", F.triggerChance, {
          required: true,
          type: "number",
          helperText: F.triggerChanceHelp,
          inputProps: { min: 1, max: 100, step: 1 },
        })}
      {takes("tool") &&
        field("triggerTool", F.triggerTool, {
          required: true,
          options: actionIds.map((id) => ({ value: id, label: id })),
          startLabel: initial.triggerTool,
        })}
      {takes("on_call") &&
        field("triggerCall", F.triggerCall, {
          type: "number",
          helperText: F.triggerCallHelp,
          inputProps: { min: 1, step: 1 },
        })}
      {takes("words") &&
        field("triggerWords", F.triggerWords, {
          required: true,
          helperText: F.triggerWordsHelp,
        })}
      {takes("turn") &&
        field("triggerTurn", F.triggerTurn, {
          required: true,
          type: "number",
          inputProps: { min: 1 },
        })}
      {takes("seconds") &&
        field("triggerSeconds", F.triggerSeconds, {
          required: true,
          type: "number",
          inputProps: { min: 0 },
        })}
      {takes("collection") &&
        field("triggerCollection", F.triggerCollection, {
          required: true,
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
