import PropTypes from "prop-types";
import { useState } from "react";
import {
  Alert,
  Autocomplete,
  Box,
  Button,
  Checkbox,
  Collapse,
  FormControlLabel,
  MenuItem,
  Stack,
  TextField,
  Typography,
} from "@mui/material";
import { ConfirmDialog } from "src/components/custom-dialog";
import { useActorCheck } from "src/api/simulate-environments/versions";
import SideDrawer from "../../components/SideDrawer";
import ActorFormFooter from "./ActorFormFooter";
import ActorKnowledgeFields from "./ActorKnowledgeFields";
import ActorLoudness from "./ActorLoudness";
import { ACTORS_COPY } from "./actors.constants";
import {
  KEPT,
  PROBLEM_FIELDS,
  changesOf,
  fallbackActs,
  fallbackKey,
  hasEdits,
  initialFields,
  parsedArguments,
  whereProblem,
  words,
} from "./actorUtils";

const C = ACTORS_COPY.worldActors;
const CHANNEL_LABELS = ACTORS_COPY.callActors.channels;

// A value the record starts from stays listed even when the options don't have it, so a select never goes blank on it.
const withStart = (options, start, label = start) =>
  start && !options.some(([id]) => id === start)
    ? [...options, [start, label]]
    : options;

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
  const F = C.form;
  const existing = Boolean(actor?.id);
  const [fields, setFields] = useState({});
  const [more, setMore] = useState(false);
  const initial = initialFields(actor);
  const readerOf = (holder) => (name) =>
    name in holder ? holder[name] : initial[name];
  const value = readerOf(fields);
  // What a save would send for the given fields; null while a JSON field cannot be read yet, its own field says why.
  const requestOf = (holder) => {
    const read = readerOf(holder);
    if (read("mode") === "conditional" && whereProblem(read("triggerWhere")))
      return null;
    if (
      fallbackActs(read("fallback")) &&
      parsedArguments(read("fallback_arguments")) === null
    )
      return null;
    if (existing)
      return {
        op: "replace",
        actorId: actor.id,
        changes: changesOf(actor, holder),
      };
    const id = String(read("id")).trim();
    return {
      op: "create",
      changes: changesOf(actor, holder),
      ...(id ? { id } : {}),
    };
  };
  const put = (name, next) =>
    setFields((current) => ({ ...current, [name]: next }));
  const set = (name) => (event) => put(name, event.target.value);
  const changed = hasEdits(actor, fields);
  // Nothing to send while the form is closed.
  const request = actor ? requestOf(fields) : null;
  // The server's verdict on exactly what the form holds, asked on open, as typing pauses and when the cast is read
  // again; none while it is being asked.
  const check = useActorCheck(envId, request);
  const { verdict } = check;
  const problems = verdict ? verdict.problems : [];
  // Escape, the close button and a click outside can all be accidents; Cancel is not, so it never asks.
  const [leaving, setLeaving] = useState(false);
  const leave = () => {
    // A save in flight lands whatever happens here, so nothing can be discarded yet.
    if (busy) return;
    if (changed) setLeaving(true);
    else onClose();
  };
  const actions = context.actions;
  const actionIds = actions.map((one) => one.action);
  const others = context.others.filter((one) => one.id !== actor?.id);
  const nameOf = (id) =>
    context.others.find((one) => one.id === id)?.name || id;

  const channel = value("channel");
  // The chosen way of joining, with what it allows: its trigger kinds, whether it has a loudness, what it requires.
  const chosen = context.channelOptions.find(
    (one) => (one.channel === null ? "" : one.channel) === channel,
  );
  const needsRole = Boolean(chosen?.required_fields.includes("role"));
  // How they join cannot change while scenarios include them; the server says so, with why.
  const channelLock = existing
    ? actor.locked_fields.find((one) => one.field === "channel")
    : null;
  const helper = (name, otherwise) => fieldProblem[name] || otherwise;
  const text = (name, label, extra = {}) => (
    <TextField
      size="small"
      label={label}
      value={value(name)}
      onChange={set(name)}
      error={Boolean(fieldProblem[name])}
      {...extra}
      helperText={helper(name, extra.helperText)}
    />
  );
  const many = (name, label, helperText) =>
    text(name, label, { multiline: true, minRows: 2, helperText });
  const choice = (name, label, options, { startLabel, ...extra } = {}) => (
    <TextField
      select
      size="small"
      label={label}
      value={value(name)}
      onChange={set(name)}
      SelectProps={{ displayEmpty: true }}
      error={Boolean(fieldProblem[name])}
      {...extra}
      helperText={helper(name, extra.helperText)}
    >
      {withStart(options, initial[name], startLabel).map(([id, label]) => (
        <MenuItem key={id || "empty"} value={id}>
          {label}
        </MenuItem>
      ))}
    </TextField>
  );
  const permitted = value("permitted");
  // Actions this actor holds that the world no longer has, as the cast marks them.
  const unknownActions = existing
    ? actor.permissions
        .filter((one) => !one.in_world && permitted.includes(one.action))
        .map((one) => one.action)
    : [];
  // An action only one other actor holds is theirs alone: the server names each, and giving it here is confirmed
  // before saving. The confirmation covers exactly the actions it was given for.
  const conflicts = verdict ? verdict.conflicts : [];
  const conflictKey = conflicts
    .map((one) => one.action)
    .sort()
    .join("|");
  const [confirmedFor, setConfirmedFor] = useState("");
  const shareConfirmed = conflicts.length > 0 && confirmedFor === conflictKey;
  const shareUnconfirmed = conflicts.length > 0 && !shareConfirmed;
  const describe = (id) =>
    actions.find((one) => one.action === id)?.description || "";
  const timingKind = value("timing");
  const capName = channel ? "max_lines" : "max_turns";
  const fallbackOptions = [
    ...Object.entries(F.fallbacks).map(([id, label]) => [
      fallbackKey({ kind: id, value: id }),
      label,
    ]),
    ...permitted.map((id) => [
      fallbackKey({ kind: "action", value: id }),
      F.takesAction(id),
    ]),
    ...others.map((one) => [
      fallbackKey({ kind: "actor", value: one.id }),
      F.answeredBy(one.name),
    ]),
  ];
  const argumentsWrong =
    fallbackActs(value("fallback")) &&
    parsedArguments(value("fallback_arguments")) === null
      ? F.fallbackArgumentsWrong
      : "";
  const where =
    value("mode") === "conditional" ? whereProblem(value("triggerWhere")) : "";
  // The fields on screen now: a server problem shows under one of them, every other in the list above Save.
  const shown = new Set([
    "id",
    "name",
    "role",
    "channel",
    "kind",
    "facts",
    "files",
    "records",
    "permitted",
    "play",
    "mode",
    "timeout",
    "fallback",
    capName,
    ...(timingKind === "delayed" ? ["seconds"] : []),
    ...(fallbackActs(value("fallback")) ? ["fallback_arguments"] : []),
    ...(context.actorModels.length > 0 ? ["model"] : []),
  ]);
  const fieldOf = (one) =>
    shown.has(PROBLEM_FIELDS[one.field]) ? PROBLEM_FIELDS[one.field] : null;
  const fieldProblem = Object.fromEntries(
    problems.filter(fieldOf).map((one) => [fieldOf(one), one.message]),
  );
  const otherProblems = problems.filter((one) => !fieldOf(one));
  const sideLabel = C.sides[actor?.side]?.label;

  return (
    <SideDrawer
      open={Boolean(actor)}
      onClose={leave}
      width={{ xs: "100%", sm: 520 }}
    >
      {actor && (
        <Stack sx={{ height: "100%", minHeight: 0 }}>
          <Box
            sx={{
              px: 2.5,
              py: 2,
              // Clears the drawer's own close button.
              pr: 6,
              flexShrink: 0,
              borderBottom: "1px solid",
              borderColor: "divider",
            }}
          >
            <Typography noWrap sx={{ typography: "s1", fontWeight: 600 }}>
              {existing ? F.editTitle(actor.name) : F.createTitle}
            </Typography>
            {!existing && sideLabel && (
              <Typography sx={{ typography: "s3", color: "text.secondary" }}>
                {F.createSubtitle(sideLabel)}
              </Typography>
            )}
          </Box>
          {/* Above the scrolling fields, so a refused save shows wherever the form is scrolled to. */}
          {error && (
            <Alert severity="error" sx={{ mx: 2.5, mt: 1.5, flexShrink: 0 }}>
              {error}
            </Alert>
          )}
          {offline && (
            <Alert severity="info" sx={{ mx: 2.5, mt: 1.5, flexShrink: 0 }}>
              {C.offline}
            </Alert>
          )}
          {conflict && (
            <Alert severity="warning" sx={{ mx: 2.5, mt: 1.5, flexShrink: 0 }}>
              {conflict === "removed" ? F.removedElsewhere : F.changedElsewhere}
            </Alert>
          )}
          <Stack
            spacing={2}
            sx={{ p: 2.5, flex: 1, minHeight: 0, overflowY: "auto" }}
          >
            {text("name", F.name, { helperText: F.nameHelp })}
            {text("id", F.id, {
              // A new actor's id is the one the server would give it until someone types their own.
              value: existing
                ? actor.id
                : "id" in fields
                  ? fields.id
                  : verdict && verdict.suggested_id !== null
                    ? verdict.suggested_id
                    : "",
              helperText: existing ? F.idFixed : F.idHelp(context.idHint),
              disabled: existing,
            })}
            {text("role", needsRole ? F.roleRequired : F.role, {
              helperText: F.roleHelp,
            })}
            <>
              {choice(
                "channel",
                F.channel,
                context.channelOptions.map((one) =>
                  one.channel === null
                    ? ["", F.noChannel]
                    : [one.channel, CHANNEL_LABELS[one.channel] || one.channel],
                ),
                {
                  disabled: Boolean(channelLock),
                  helperText: channelLock
                    ? channelLock.reason
                    : chosen?.description,
                  startLabel: initial.channel
                    ? CHANNEL_LABELS[initial.channel] || initial.channel
                    : F.noChannel,
                },
              )}
              {channel && (
                <>
                  <Typography sx={{ typography: "s2", fontWeight: 600 }}>
                    {F.voice}
                  </Typography>
                  <Stack direction="row" spacing={1}>
                    {choice("gender", F.gender, Object.entries(F.genders), {
                      fullWidth: true,
                    })}
                    {choice(
                      "age_group",
                      F.ageGroup,
                      Object.entries(F.ageGroups),
                      {
                        fullWidth: true,
                      },
                    )}
                    {choice(
                      "accent",
                      F.accent,
                      F.accents.map((one) => [one, one || F.anyAccent]),
                      { fullWidth: true },
                    )}
                  </Stack>
                  <Typography
                    sx={{ typography: "s3", color: "text.secondary" }}
                  >
                    {F.voiceHelp}
                  </Typography>
                  {chosen?.loudness && context.loudnessRange && (
                    <ActorLoudness
                      value={value("loudness_ratio")}
                      automatic={context.loudnessRange}
                      onChange={(next) => put("loudness_ratio", next)}
                    />
                  )}
                </>
              )}

              {choice("kind", F.kind, Object.entries(F.kinds))}
              {many("facts", F.facts, F.factsHelp)}
              <ActorKnowledgeFields
                files={value("files")}
                records={value("records")}
                sources={context.knowledgeSources}
                upload={context.upload}
                problems={fieldProblem}
                onChange={put}
              />
              <Stack direction="row" spacing={1}>
                {choice(
                  "timing",
                  F.answers,
                  [
                    ["immediate", F.immediately],
                    ["delayed", F.afterDelay],
                  ],
                  { sx: { flex: 1 } },
                )}
                {timingKind === "delayed" &&
                  text("seconds", F.seconds, {
                    type: "number",
                    sx: { width: 140 },
                    inputProps: { min: 0 },
                  })}
              </Stack>
              <Autocomplete
                multiple
                size="small"
                options={actionIds}
                value={permitted}
                disabled={actions.length === 0 && permitted.length === 0}
                onChange={(_, next) => put("permitted", next)}
                getOptionLabel={(id) => id}
                renderOption={(props, id) => (
                  <li {...props} key={id}>
                    <Box>
                      <Typography sx={{ typography: "s2" }}>{id}</Typography>
                      <Typography
                        sx={{ typography: "s3", color: "text.secondary" }}
                      >
                        {describe(id)}
                      </Typography>
                    </Box>
                  </li>
                )}
                renderInput={(params) => (
                  <TextField
                    {...params}
                    label={F.permitted}
                    error={
                      unknownActions.length > 0 ||
                      Boolean(fieldProblem.permitted)
                    }
                    helperText={helper(
                      "permitted",
                      unknownActions.length
                        ? F.permittedUnknown(unknownActions)
                        : actions.length
                          ? F.permittedHelp
                          : F.noWorldActions,
                    )}
                  />
                )}
              />
              {conflicts.length > 0 && (
                <Alert
                  severity="warning"
                  data-testid="actor-permission-conflict"
                >
                  {conflicts.map((one) => (
                    <Typography key={one.action} sx={{ typography: "s2" }}>
                      {F.permittedConflict(one.action, one.holder_name)}
                    </Typography>
                  ))}
                  <FormControlLabel
                    control={
                      <Checkbox
                        size="small"
                        checked={shareConfirmed}
                        onChange={(event) =>
                          setConfirmedFor(
                            event.target.checked ? conflictKey : "",
                          )
                        }
                      />
                    }
                    label={F.permittedShare}
                  />
                </Alert>
              )}
              {choice("play", F.play, Object.entries(F.plays), {
                helperText: F.playHelp,
              })}
              {choice(
                "mode",
                F.trigger,
                (chosen ? chosen.trigger_kinds : []).map((mode) => [
                  mode,
                  F.modes[mode] || words(mode),
                ]),
                {
                  helperText: F.triggerHelp,
                  // A stored trigger the form cannot make reads as the server says it runs.
                  startLabel:
                    initial.mode === KEPT
                      ? actor.when_label
                      : F.modes[initial.mode] || words(initial.mode),
                },
              )}
              {value("mode") === "on_tool" &&
                choice(
                  "triggerTool",
                  F.triggerTool,
                  actionIds.map((id) => [id, id]),
                  { startLabel: initial.triggerTool },
                )}
              {value("mode") === "on_keyword" &&
                text("triggerWords", F.triggerWords, {
                  helperText: F.triggerWordsHelp,
                })}
              {value("mode") === "mid_journey" &&
                text("triggerTurn", F.triggerTurn, {
                  type: "number",
                  inputProps: { min: 1 },
                })}
              {value("mode") === "after_seconds" &&
                text("triggerSeconds", F.triggerSeconds, {
                  type: "number",
                  inputProps: { min: 0 },
                })}
              {value("mode") === "conditional" && (
                <>
                  {choice(
                    "triggerCollection",
                    F.triggerCollection,
                    context.knowledgeSources.records.map((one) => [
                      one.collection,
                      one.collection,
                    ]),
                    { startLabel: initial.triggerCollection },
                  )}
                  {text("triggerWhere", F.triggerWhere, {
                    error: Boolean(where),
                    helperText: where || F.triggerWhereHelp,
                  })}
                </>
              )}
              {text("timeout", F.timeout, {
                type: "number",
                helperText: F.timeoutHelp,
                inputProps: { min: 0 },
              })}
              {choice("fallback", F.fallback, fallbackOptions, {
                // A stored fallback no longer on offer reads as the server classified it.
                startLabel: existing ? actor.fallback.label : undefined,
              })}
              {fallbackActs(value("fallback")) &&
                text("fallback_arguments", F.fallbackArguments, {
                  error: Boolean(argumentsWrong),
                  helperText: argumentsWrong || F.fallbackArgumentsHelp,
                })}
              {context.actorModels?.length > 0 &&
                choice(
                  "model",
                  F.model,
                  [
                    ["", F.environmentModel],
                    ...context.actorModels.map((one) => [one, one]),
                  ],
                  { helperText: F.modelHelp, startLabel: initial.model },
                )}
              {text(capName, channel ? F.maxLines : F.maxTurns, {
                type: "number",
                helperText: F.noLimitHelp,
                inputProps: { min: 1, step: 1 },
              })}
              {/* Kept on the record and editable, but a run plays an actor from its scenario's actions and does not read these yet. */}
              <Button
                size="small"
                sx={{ alignSelf: "flex-start" }}
                onClick={() => setMore((shown) => !shown)}
              >
                {more ? F.lessDetails : F.moreDetails}
              </Button>
              <Collapse in={more} sx={{ flexShrink: 0 }}>
                <Stack spacing={2}>
                  <Typography
                    sx={{ typography: "s3", color: "text.secondary" }}
                  >
                    {F.moreDetailsHelp}
                  </Typography>
                  {(others.length > 0 || initial.parent) &&
                    choice(
                      "parent",
                      F.parent,
                      [
                        ["", F.nobody],
                        ...others.map((one) => [one.id, one.name]),
                      ],
                      {
                        helperText: F.parentHelp,
                        startLabel: nameOf(initial.parent),
                      },
                    )}
                  {text("goal", F.goal, {
                    multiline: true,
                    minRows: 2,
                    helperText: F.goalHelp,
                  })}
                  {many("may_agree", F.mayAgree)}
                  {many("private", F.private)}
                  {many("escalate_when", F.escalateWhen)}
                  {many("stop_when", F.stopWhen)}
                  {text("style", F.style, { helperText: F.styleHelp })}
                </Stack>
              </Collapse>
            </>
          </Stack>
          <ActorFormFooter
            problems={otherProblems}
            overBy={verdict ? verdict.over_by : 0}
            check={check}
            checking={changed && check.pending}
            busy={busy}
            // Saved only once the server has checked exactly this edit and found nothing to fix.
            canSave={
              !busy &&
              conflict !== "removed" &&
              (!existing || changed) &&
              Boolean(verdict) &&
              problems.length === 0 &&
              verdict.over_by === 0 &&
              !shareUnconfirmed
            }
            onCancel={onClose}
            onSave={() =>
              onSave(
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
  // The cast's typed actor; `{side}` for a new one, null while the form is closed.
  actor: PropTypes.object,
  context: PropTypes.shape({
    actions: PropTypes.arrayOf(
      PropTypes.shape({
        action: PropTypes.string,
        tool: PropTypes.string,
        description: PropTypes.string,
      }),
    ).isRequired,
    // One per way of joining, `channel: null` for acting in the world only.
    channelOptions: PropTypes.arrayOf(
      PropTypes.shape({
        channel: PropTypes.string,
        description: PropTypes.string,
        loudness: PropTypes.bool,
        trigger_kinds: PropTypes.arrayOf(PropTypes.string),
        required_fields: PropTypes.arrayOf(PropTypes.string),
      }),
    ).isRequired,
    // The range a call's loudness is drawn from when none is set.
    loudnessRange: PropTypes.arrayOf(PropTypes.number),
    idHint: PropTypes.string,
    actorModels: PropTypes.arrayOf(PropTypes.string),
    upload: PropTypes.func,
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
  // The draft moved under this form: its record is the fresh one with the typed fields kept, or it is gone.
  conflict: PropTypes.oneOf(["changed", "removed"]),
  onClose: PropTypes.func,
  // (request, shareActions): the request a save sends, and the conflicting actions confirmed as shared.
  onSave: PropTypes.func,
};
