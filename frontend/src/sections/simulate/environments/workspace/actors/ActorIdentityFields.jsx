import PropTypes from "prop-types";
import ActorField from "./ActorField";
import { ACTORS_COPY } from "./actors.constants";
import { ACTOR_FORM_SHAPE } from "./actors.shapes";

const F = ACTORS_COPY.worldActors.form;

export default function ActorIdentityFields({
  form,
  actor,
  idTyped,
  suggestedId,
  idHint,
  channelOptions,
  chosen,
  fieldOptions,
}) {
  const existing = Boolean(actor.id);
  const channelLock = existing
    ? actor.locked_fields.find((one) => one.field === "channel")
    : null;
  const startChannel = form.initial.channel;
  const roleRequired = Boolean(chosen?.required_fields.includes("role"));
  return (
    <>
      <ActorField
        form={form}
        name="channel"
        label={F.channel}
        options={channelOptions.map((one) =>
          one.channel === null
            ? { value: "", label: F.noChannel }
            : { value: one.channel, label: one.label },
        )}
        disabled={Boolean(channelLock)}
        helperText={channelLock ? channelLock.reason : chosen?.description}
        // A stored way of joining the cast no longer offers reads by its own name.
        startLabel={startChannel || F.noChannel}
      />
      <ActorField
        form={form}
        name="kind"
        label={F.kind}
        options={fieldOptions.kind}
      />
      <ActorField
        form={form}
        name="role"
        label={roleRequired ? F.roleRequired : F.role}
        helperText={F.roleHelp}
        required={roleRequired}
      />
      <ActorField
        form={form}
        name="name"
        label={F.name}
        helperText={F.nameHelp}
        required
      />
      {existing ? (
        <ActorField
          form={form}
          name="id"
          label={F.id}
          value={actor.id}
          helperText={F.idFixed}
          disabled
        />
      ) : (
        <ActorField
          form={form}
          name="id"
          label={F.id}
          value={idTyped ? form.value("id") : suggestedId}
          placeholder={suggestedId}
          InputLabelProps={{ shrink: true }}
          helperText={F.idHelp(idHint)}
        />
      )}
    </>
  );
}

ActorIdentityFields.propTypes = {
  form: ACTOR_FORM_SHAPE.isRequired,
  // `{side}` for a new actor.
  actor: PropTypes.object.isRequired,
  // True even when the typed id is empty.
  idTyped: PropTypes.bool.isRequired,
  suggestedId: PropTypes.string.isRequired,
  idHint: PropTypes.string,
  channelOptions: PropTypes.arrayOf(PropTypes.object).isRequired,
  chosen: PropTypes.object,
  fieldOptions: PropTypes.object.isRequired,
};
