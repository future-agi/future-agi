import PropTypes from "prop-types";
import { useEffect, useRef, useState } from "react";
import { Button, Stack } from "@mui/material";
import { useForm, useWatch } from "react-hook-form";
import FormProvider from "src/components/hook-form";
import { ConfirmDialog } from "src/components/custom-dialog";
import { useActorCheck } from "src/api/simulate-environments/versions";
import SideDrawer from "../../components/SideDrawer";
import ActorActionFields from "./ActorActionFields";
import ActorAdvancedFields from "./ActorAdvancedFields";
import ActorField from "./ActorField";
import ActorFormFooter from "./ActorFormFooter";
import ActorFormHeader from "./ActorFormHeader";
import ActorIdentityFields from "./ActorIdentityFields";
import ActorKnowledgeFields from "./ActorKnowledgeFields";
import ActorPressureFields from "./ActorPressureFields";
import ActorTriggerFields from "./ActorTriggerFields";
import ActorVoiceFields from "./ActorVoiceFields";
import { ACTORS_COPY } from "./actors.constants";
import { OPTIONS_SHAPE } from "./actors.shapes";
import {
  ADVANCED,
  OPTIONAL_TRIGGER_KEYS,
  PROBLEM_FIELDS,
  TRIGGER_INPUTS,
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
  const [advanced, setAdvanced] = useState(false);
  const initial = initialFields(actor);
  const methods = useForm({ defaultValues: initial });
  const fields = useWatch({ control: methods.control });
  const { touchedFields } = methods.formState;
  const value = (name) => (name in fields ? fields[name] : initial[name]);
  // Changed at all, even back to where it started: a cleared id stays cleared rather than taking the suggestion.
  const edited = (name) => Boolean(touchedFields[name]);
  const put = (name, next) =>
    methods.setValue(name, next, { shouldDirty: true, shouldTouch: true });
  // An upload that finds the draft moved hands in the fresh record: it becomes the start, and what was typed stays.
  const opened = useRef(actor);
  useEffect(() => {
    if (opened.current === actor) return;
    opened.current = actor;
    const typed = Object.keys(methods.formState.touchedFields).map((name) => [
      name,
      methods.getValues(name),
    ]);
    methods.reset(initialFields(actor));
    typed.forEach(([name, next]) =>
      methods.setValue(name, next, { shouldDirty: true, shouldTouch: true }),
    );
  }, [actor, methods]);
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
  const changed = hasEdits(actor, fields);
  const request = actor ? requestOf() : null;
  const check = useActorCheck(envId, request);
  const { verdict } = check;
  // A problem shows live only on a field the person changed and filled. Save is the final check: it reveals every
  // problem there is then, and those keep following edits; a field that appears later waits for the next Save.
  const [revealed, setRevealed] = useState(() => new Set());
  const isEmpty = (name) => {
    const typed = value(name);
    return Array.isArray(typed)
      ? typed.length === 0
      : String(typed ?? "").trim() === "";
  };
  const touched = (name) =>
    name === "mode"
      ? ["mode", ...Object.values(TRIGGER_INPUTS)].some(edited)
      : edited(name);
  const filled = (name) =>
    name === "mode"
      ? (triggerKeys[value("mode")] || [])
          .filter((key) => !OPTIONAL_TRIGGER_KEYS.includes(key))
          .every((key) => !isEmpty(TRIGGER_INPUTS[key]))
      : !isEmpty(name);
  const live = (one) => {
    const name = PROBLEM_FIELDS[one.field];
    return Boolean(name) && touched(name) && filled(name);
  };
  // The server names the whole trigger; its problem shows under the first required input left empty, else under
  // the last one typed in (a chance over 100%, call 1.5), since that is the value it judged.
  const triggerInputAt = () => {
    const keys = triggerKeys[value("mode")] || [];
    const empty = keys
      .filter((one) => !OPTIONAL_TRIGGER_KEYS.includes(one))
      .find((one) => isEmpty(TRIGGER_INPUTS[one]));
    const typedIn = keys.filter((one) => !isEmpty(TRIGGER_INPUTS[one])).pop();
    const key = empty || typedIn;
    return key ? TRIGGER_INPUTS[key] : null;
  };
  // Where a problem belongs: its form field, or the line above Save for one with none.
  const keyOf = (one) => {
    const name = PROBLEM_FIELDS[one.field];
    if (name === "mode") return triggerInputAt() || "mode";
    return name || `above:${one.message}`;
  };
  // The server takes a delay with no seconds as no delay, so the form asks for them itself; 0 is no delay.
  const own =
    value("timing") === "delayed" && isEmpty("seconds")
      ? [{ field: "response_timing", message: F.delaySeconds }]
      : [];
  const found = verdict ? [...own, ...verdict.problems] : [];
  const problems = found.filter((one) => live(one) || revealed.has(keyOf(one)));
  const overBy = verdict && revealed.has("over_by") ? verdict.over_by : 0;
  // Escape, the close button and a click outside ask before discarding; Cancel never asks.
  const [leaving, setLeaving] = useState(false);
  const leave = () => {
    // A Save waiting for the check counts as saving: leaving then could drop a save already on its way.
    if (busy || saveAsked) return;
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
  const screenOf = (open) =>
    !actor
      ? new Set()
      : fieldsOnScreen(value, {
          capField,
          loud: Boolean(chosen?.loudness),
          models: context.actorModels.length > 0,
          advanced: open,
        });
  // A problem on a folded field opens the fold and keeps it open, so it shows under its field while being fixed.
  const openShown = screenOf(true);
  const problemInFold =
    (Boolean(actor) &&
      fallbackActs(value("fallback")) &&
      parsedArguments(value("fallback_arguments")) === null) ||
    problems.some((one) => {
      const name = PROBLEM_FIELDS[one.field];
      return ADVANCED.includes(name) && openShown.has(name);
    });
  if (problemInFold && !advanced) setAdvanced(true);
  const foldOpen = advanced || problemInFold;
  const shown = screenOf(foldOpen);
  const fieldOf = (one) => {
    const name = PROBLEM_FIELDS[one.field];
    if (name === "mode") return keyOf(one);
    return shown.has(name) ? name : null;
  };
  const form = {
    value,
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

  // Save is always on. It is the final check: it shows every problem, closes a form with nothing to save, and waits
  // on the button for a check still running.
  const [saveAsked, setSaveAsked] = useState(false);
  // A blocked Save takes the person to the first field it marked, so a problem out of view is never missed.
  const body = useRef(null);
  const [blockedSaves, setBlockedSaves] = useState(0);
  useEffect(() => {
    const marked = blockedSaves
      ? body.current?.querySelector(
          '.MuiInputBase-root.Mui-error, [data-testid="actor-permission-conflict"]',
        )
      : null;
    if (!marked) return undefined;
    const go = () => {
      marked.scrollIntoView?.({ block: "center", behavior: "smooth" });
      marked
        .querySelector(
          '[role="combobox"], textarea, input:not([type="hidden"]):not([aria-hidden="true"])',
        )
        ?.focus({ preventScroll: true });
    };
    // A field in the opening fold has no room to scroll to until the fold has opened.
    const fold = marked.closest(".MuiCollapse-root:not(.MuiCollapse-entered)");
    if (!fold) {
      go();
      return undefined;
    }
    // Its own transition end, else a moment later when motion is reduced and none plays.
    let done = false;
    const once = (event) => {
      if (done || (event && event.target !== fold)) return;
      done = true;
      go();
    };
    fold.addEventListener("transitionend", once);
    const timer = setTimeout(once, 1000);
    return () => {
      fold.removeEventListener("transitionend", once);
      clearTimeout(timer);
    };
  }, [blockedSaves]);
  const submit = () => {
    setRevealed(
      new Set([
        ...found.map(keyOf),
        ...(verdict?.over_by > 0 ? ["over_by"] : []),
      ]),
    );
    if (existing && !changed) {
      onClose();
      return;
    }
    const blocked =
      conflict === "removed" ||
      !verdict ||
      found.length > 0 ||
      verdict.over_by > 0 ||
      (conflicts.length > 0 && !shareConfirmed);
    if (blocked) {
      setBlockedSaves((count) => count + 1);
      return;
    }
    onSave(
      request,
      conflicts.map((one) => one.action),
    );
  };
  const attemptSave = () => {
    if (busy || saveAsked) return;
    if (check.pending) {
      setSaveAsked(true);
      return;
    }
    submit();
  };
  // A Save clicked mid-check runs once the check answers, with the form as it is then.
  const latestSubmit = useRef(submit);
  latestSubmit.current = submit;
  useEffect(() => {
    if (!saveAsked || check.pending) return;
    setSaveAsked(false);
    latestSubmit.current();
  }, [saveAsked, check.pending]);

  return (
    <SideDrawer
      open={Boolean(actor)}
      onClose={leave}
      width={{ xs: "100%", sm: 520 }}
    >
      {actor && (
        <FormProvider
          methods={methods}
          // Enter in a field never saves; Save does.
          onSubmit={(event) => event.preventDefault()}
          style={{ height: "100%" }}
        >
          <Stack sx={{ height: "100%", minHeight: 0 }}>
            <ActorFormHeader
              actor={actor}
              error={error}
              offline={offline}
              conflict={conflict}
            />
            <Stack
              ref={body}
              spacing={2}
              sx={{ p: 2.5, flex: 1, minHeight: 0, overflowY: "auto" }}
            >
              <ActorIdentityFields
                form={form}
                actor={actor}
                idTyped={edited("id")}
                suggestedId={verdict?.suggested_id || ""}
                idHint={context.idHint}
                channelOptions={context.channelOptions}
                chosen={chosen}
                fieldOptions={options}
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
              />
              {field("facts", F.facts, {
                multiline: true,
                minRows: 2,
                helperText: F.factsHelp,
              })}
              <ActorKnowledgeFields
                form={form}
                sources={context.knowledgeSources}
                upload={context.upload}
                uploadRules={context.uploadRules}
              />
              <ActorActionFields
                form={form}
                actions={context.actions}
                unknownActions={unknownActions}
                conflicts={conflicts}
                shareConfirmed={shareConfirmed}
                onShare={(checked) =>
                  setConfirmedFor(checked ? conflictKey : "")
                }
              />
              {value("channel") && (
                <ActorVoiceFields
                  form={form}
                  options={options}
                  loudness={chosen?.loudness ? context.loudness : null}
                />
              )}
              <ActorPressureFields form={form} />
              {field("style", F.style, { helperText: F.styleHelp })}
              <ActorAdvancedFields
                form={form}
                open={foldOpen}
                onToggle={() => setAdvanced(!foldOpen)}
                actor={actor}
                others={others}
                recordSources={context.knowledgeSources.records}
                fieldOptions={options}
                actorModels={context.actorModels}
                capField={capField}
              />
            </Stack>
            <ActorFormFooter
              problems={problems.filter((one) => !fieldOf(one))}
              overBy={overBy}
              check={check}
              busy={busy}
              saving={busy || saveAsked}
              onCancel={onClose}
              onSave={attemptSave}
            />
          </Stack>
        </FormProvider>
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
