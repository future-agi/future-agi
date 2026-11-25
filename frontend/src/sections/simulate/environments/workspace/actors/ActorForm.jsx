import PropTypes from "prop-types";
import { useState } from "react";
import { Button, Stack } from "@mui/material";
import { ConfirmDialog } from "src/components/custom-dialog";
import { useActorCheck } from "src/api/simulate-environments/versions";
import SideDrawer from "../../components/SideDrawer";
import ActorActionFields from "./ActorActionFields";
import ActorField from "./ActorField";
import ActorFormFooter from "./ActorFormFooter";
import ActorFormHeader from "./ActorFormHeader";
import ActorIdentityFields from "./ActorIdentityFields";
import ActorKnowledgeFields from "./ActorKnowledgeFields";
import ActorMoreDetails from "./ActorMoreDetails";
import ActorTriggerFields from "./ActorTriggerFields";
import { ACTORS_COPY } from "./actors.constants";
import { OPTIONS_SHAPE } from "./actors.shapes";
import {
  PROBLEM_FIELDS,
  changesOf,
  fallbackActs,
  fieldsOnScreen,
  hasEdits,
  initialFields,
  parsedArguments,
  whereProblem,
} from "./actorUtils";

const F = ACTORS_COPY.worldActors.form;

export default function ActorForm({
  envId,
  actor,
  context,
  busy,
  offline,
  error,
  conflict,
  onClose,
  onSave,
}) {
  const existing = Boolean(actor?.id);
  const [fields, setFields] = useState({});
  const [more, setMore] = useState(false);
  const initial = initialFields(actor);
  const readerOf = (holder) => (name) =>
    name in holder ? holder[name] : initial[name];
  const value = readerOf(fields);
  // Null while a JSON field does not parse; that field shows why.
  // The trigger keys each kind takes, as the cast lists them.
  const triggerKeys = context.triggerFields;
  const requestOf = () => {
    if (
      (triggerKeys[value("mode")] || []).includes("where") &&
      whereProblem(value("triggerWhere"))
    )
      return null;
    if (
      fallbackActs(value("fallback")) &&
      parsedArguments(value("fallback_arguments")) === null
    )
      return null;
    const changes = changesOf(actor, fields, triggerKeys);
    if (existing) return { op: "replace", actorId: actor.id, changes };
    const id = String(value("id")).trim();
    return { op: "create", changes, ...(id ? { id } : {}) };
  };
  const put = (name, next) =>
    setFields((current) => ({ ...current, [name]: next }));
  const set = (name) => (event) => put(name, event.target.value);
  const changed = hasEdits(actor, fields);
  const request = actor ? requestOf() : null;
  const check = useActorCheck(envId, request);
  const { verdict } = check;
  // Problems wait until something is edited or Save is tried, so a form opens clean.
  const [tried, setTried] = useState(false);
  const problems = verdict && (tried || changed) ? verdict.problems : [];
  const waiting = Boolean(verdict?.problems.length) && problems.length === 0;
  // Escape, the close button and a click outside ask before discarding; Cancel never asks.
  const [leaving, setLeaving] = useState(false);
  const leave = () => {
    if (busy) return;
    if (changed) setLeaving(true);
    else onClose();
  };
  const others = context.others.filter((one) => one.id !== actor?.id);
  const options = context.fieldOptions;
  const chosen = context.channelOptions.find(
    (one) => (one.channel === null ? "" : one.channel) === value("channel"),
  );
  const capField = chosen?.cap_field;
  // Sharing an action another actor holds alone needs confirming; it covers exactly these actions.
  const conflicts = verdict ? verdict.conflicts : [];
  const conflictKey = conflicts
    .map((one) => one.action)
    .sort()
    .join("|");
  const [confirmedFor, setConfirmedFor] = useState("");
  const shareConfirmed = conflicts.length > 0 && confirmedFor === conflictKey;
  // Nothing is on screen while the form is closed.
  const shown = !actor
    ? new Set()
    : fieldsOnScreen(value, {
        capField,
        loud: Boolean(chosen?.loudness),
        models: context.actorModels.length > 0,
        more,
      });
  const fieldOf = (one) =>
    shown.has(PROBLEM_FIELDS[one.field]) ? PROBLEM_FIELDS[one.field] : null;
  const form = {
    value,
    set,
    put,
    initial,
    problems: Object.fromEntries(
      problems.filter(fieldOf).map((one) => [fieldOf(one), one.message]),
    ),
  };
  const field = (name, label, extra = {}) => (
    <ActorField form={form} name={name} label={label} {...extra} />
  );
  const unknownActions = existing
    ? actor.permissions
        .filter(
          (one) => !one.in_world && value("permitted").includes(one.action),
        )
        .map((one) => one.action)
    : [];

  return (
    <SideDrawer
      open={Boolean(actor)}
      onClose={leave}
      width={{ xs: "100%", sm: 520 }}
    >
      {actor && (
        <Stack sx={{ height: "100%", minHeight: 0 }}>
          <ActorFormHeader
            actor={actor}
            error={error}
            offline={offline}
            conflict={conflict}
          />
          <Stack
            spacing={2}
            sx={{ p: 2.5, flex: 1, minHeight: 0, overflowY: "auto" }}
          >
            <ActorIdentityFields
              form={form}
              actor={actor}
              idTyped={"id" in fields}
              suggestedId={verdict?.suggested_id || ""}
              idHint={context.idHint}
              channelOptions={context.channelOptions}
              chosen={chosen}
              fieldOptions={options}
              loudness={context.loudness}
            />
            {field("kind", F.kind, { options: options.kind })}
            {field("facts", F.facts, {
              multiline: true,
              minRows: 2,
              helperText: F.factsHelp,
            })}
            <ActorKnowledgeFields
              files={value("files")}
              records={value("records")}
              sources={context.knowledgeSources}
              upload={context.upload}
              uploadRules={context.uploadRules}
              problems={form.problems}
              onChange={put}
            />
            <Stack direction="row" spacing={1}>
              {field("timing", F.answers, {
                options: options.response_timing,
                sx: { flex: 1 },
              })}
              {value("timing") === "delayed" &&
                field("seconds", F.seconds, {
                  type: "number",
                  sx: { width: 140 },
                  inputProps: { min: 0 },
                })}
            </Stack>
            <ActorActionFields
              form={form}
              actions={context.actions}
              unknownActions={unknownActions}
              conflicts={conflicts}
              shareConfirmed={shareConfirmed}
              onShare={(checked) => setConfirmedFor(checked ? conflictKey : "")}
              plays={options.play}
            />
            <ActorTriggerFields
              form={form}
              actor={actor}
              triggerOptions={chosen ? chosen.trigger_options : []}
              triggerKeys={triggerKeys[value("mode")] || []}
              actionIds={context.actions.map((one) => one.action)}
              collections={context.knowledgeSources.records.map(
                (one) => one.collection,
              )}
              fallbackWords={options.fallback_word}
              others={others}
            />
            {context.actorModels.length > 0 &&
              field("model", F.model, {
                options: [
                  { value: "", label: F.environmentModel },
                  ...context.actorModels.map((one) => ({
                    value: one,
                    label: one,
                  })),
                ],
                helperText: F.modelHelp,
                startLabel: initial.model,
              })}
            {capField &&
              field(capField, F.caps[capField], {
                type: "number",
                helperText: F.noLimitHelp,
                inputProps: { min: 1, step: 1 },
              })}
            <ActorMoreDetails
              form={form}
              open={more}
              onToggle={() => setMore((open) => !open)}
              others={others}
            />
          </Stack>
          <ActorFormFooter
            problems={problems.filter((one) => !fieldOf(one))}
            overBy={verdict ? verdict.over_by : 0}
            check={check}
            checking={changed && check.pending}
            busy={busy}
            canSave={
              !busy &&
              conflict !== "removed" &&
              (!existing || changed) &&
              Boolean(verdict) &&
              problems.length === 0 &&
              verdict.over_by === 0 &&
              (conflicts.length === 0 || shareConfirmed)
            }
            onCancel={onClose}
            onSave={() =>
              waiting
                ? setTried(true)
                : onSave(
                    request,
                    conflicts.map((one) => one.action),
                  )
            }
          />
        </Stack>
      )}
      <ConfirmDialog
        open={leaving}
        onClose={() => setLeaving(false)}
        title={F.discardTitle}
        content={F.discardBody}
        action={
          <Button
            size="small"
            variant="contained"
            color="error"
            onClick={() => {
              setLeaving(false);
              onClose();
            }}
          >
            {F.discard}
          </Button>
        }
      />
    </SideDrawer>
  );
}

ActorForm.propTypes = {
  envId: PropTypes.string.isRequired,
  // The cast's `new_actor` on its side for a new actor, null while the form is closed.
  actor: PropTypes.object,
  context: PropTypes.shape({
    actions: PropTypes.arrayOf(PropTypes.object).isRequired,
    // `channel: null` is acting in the world only.
    channelOptions: PropTypes.arrayOf(
      PropTypes.shape({
        channel: PropTypes.string,
        label: PropTypes.string,
        description: PropTypes.string,
        loudness: PropTypes.bool,
        trigger_options: OPTIONS_SHAPE,
        required_fields: PropTypes.arrayOf(PropTypes.string),
        cap_field: PropTypes.oneOf(["max_turns", "max_lines"]),
      }),
    ).isRequired,
    // The trigger keys each kind takes: `{kind: [key]}`.
    triggerFields: PropTypes.objectOf(PropTypes.arrayOf(PropTypes.string))
      .isRequired,
    fieldOptions: PropTypes.shape({
      kind: OPTIONS_SHAPE.isRequired,
      play: OPTIONS_SHAPE.isRequired,
      fallback_word: OPTIONS_SHAPE.isRequired,
      gender: OPTIONS_SHAPE.isRequired,
      age_group: OPTIONS_SHAPE.isRequired,
      accent: OPTIONS_SHAPE.isRequired,
      response_timing: OPTIONS_SHAPE.isRequired,
    }).isRequired,
    loudness: PropTypes.object.isRequired,
    idHint: PropTypes.string,
    actorModels: PropTypes.arrayOf(PropTypes.string).isRequired,
    upload: PropTypes.func,
    uploadRules: PropTypes.object.isRequired,
    knowledgeSources: PropTypes.shape({
      files: PropTypes.arrayOf(PropTypes.string),
      records: PropTypes.arrayOf(
        PropTypes.shape({
          store: PropTypes.string,
          collection: PropTypes.string,
        }),
      ),
    }).isRequired,
    others: PropTypes.array.isRequired,
  }).isRequired,
  busy: PropTypes.bool,
  offline: PropTypes.bool,
  error: PropTypes.string,
  conflict: PropTypes.oneOf(["changed", "removed"]),
  onClose: PropTypes.func,
  // (request, shareActions)
  onSave: PropTypes.func,
};
