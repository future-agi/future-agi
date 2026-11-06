import PropTypes from "prop-types";
import { useState } from "react";
import {
  Alert,
  Autocomplete,
  Box,
  Button,
  Collapse,
  FormControlLabel,
  MenuItem,
  Slider,
  Stack,
  Switch,
  TextField,
  Typography,
} from "@mui/material";
import { ConfirmDialog } from "src/components/custom-dialog";
import SideDrawer from "../../components/SideDrawer";
import { refusalText } from "../evals/refusalText";
import { ACTORS_COPY } from "./actors.constants";
import {
  KEPT_TRIGGER,
  actorId,
  hasEdits,
  idFrom,
  idRule,
  idRuleText,
  initialFields,
  modesFor,
  recordKey,
  toRecord,
  triggerProblem,
} from "./actorUtils";

const C = ACTORS_COPY.worldActors;
const CHANNEL_LABELS = ACTORS_COPY.callActors.channels;

// How loud a voice beside the caller is: automatic (each call picks within the release's range) or a fixed share.
function Loudness({ value, automatic, onChange }) {
  const F = C.form;
  const fixed = typeof value === "number";
  const [low, high] = automatic;
  const percent = (ratio) => `${Math.round(ratio * 100)}%`;
  return (
    <Box>
      <Typography sx={{ typography: "s2", fontWeight: 600 }}>
        {F.loudness}
      </Typography>
      <FormControlLabel
        control={
          <Switch
            checked={!fixed}
            onChange={(event) =>
              onChange(
                event.target.checked
                  ? null
                  : Math.round(((low + high) / 2) * 20) / 20,
              )
            }
          />
        }
        label={F.loudnessAutomatic(percent(low), percent(high))}
      />
      {fixed && (
        <Stack direction="row" spacing={2} alignItems="center" sx={{ px: 1 }}>
          <Slider
            aria-label={F.loudness}
            value={value}
            min={0.05}
            max={1}
            step={0.05}
            valueLabelDisplay="auto"
            valueLabelFormat={percent}
            onChange={(_, next) => onChange(next)}
          />
          <Typography sx={{ typography: "s2", minWidth: 120 }}>
            {F.loudnessFixed(percent(value))}
          </Typography>
        </Stack>
      )}
    </Box>
  );
}

Loudness.propTypes = {
  value: PropTypes.number,
  automatic: PropTypes.arrayOf(PropTypes.number).isRequired,
  onChange: PropTypes.func,
};

// A value the record starts from stays listed even when the options don't have it, so a select never goes blank on it.
const withStart = (options, start, label = start) =>
  start && !options.some(([id]) => id === start)
    ? [...options, [start, label]]
    : options;

export default function ActorForm({
  actor,
  context,
  busy,
  error,
  conflict,
  onClose,
  onSave,
}) {
  const F = C.form;
  const existing = Boolean(actor?.id);
  const [fields, setFields] = useState({});
  const [more, setMore] = useState(false);
  const [uploading, setUploading] = useState(false);
  const [uploadError, setUploadError] = useState("");
  const initial = initialFields(actor);
  const value = (name) => (name in fields ? fields[name] : initial[name]);
  const put = (name, next) =>
    setFields((current) => ({ ...current, [name]: next }));
  const set = (name) => (event) => put(name, event.target.value);
  const changed = hasEdits(actor, fields);
  // Escape, the close button and a click outside can all be accidents; Cancel is not, so it never asks.
  const [leaving, setLeaving] = useState(false);
  const leave = () => {
    // A save in flight lands whatever happens here, so nothing can be discarded yet.
    if (busy) return;
    if (changed) setLeaving(true);
    else onClose();
  };
  const activation = actor?.activation || {};
  const actions = context.actions;
  const actionIds = actions.map((one) => one.action);
  const others = context.others.filter((one) => one.id !== actor?.id);
  const nameOf = (id) =>
    context.others.find((one) => one.id === id)?.name || id;

  // A new actor's id follows its name until someone types an id of their own; a saved id never changes.
  const madeId = existing
    ? actor.id
    : idFrom(value("name"), new Set(context.others.map((one) => one.id)));
  // An emptied id falls back to the one made from the name.
  const typedId = actorId(actor, fields, context);
  // Before a name is typed the fallback id isn't anyone's id yet, so nothing shows.
  const suggestedId = value("name").trim() ? madeId : "";
  const takenBy = existing
    ? null
    : context.others.find((one) => one.id === typedId);
  const rule = idRule(context.records);
  const ruleText = idRuleText(context.records);
  const idInvalid = !existing && Boolean(rule) && !rule.test(typedId);
  const nameMissing = !value("name").trim();
  // What the chosen way of coming in still needs, by field: shown under the field and keeping Save off.
  const comesIn = triggerProblem(value);
  const comesInIncomplete = Object.keys(comesIn).length > 0;
  // The record schema this actor is checked against, as its environment's rules declare it.
  const schema = context.records["actors.json"] || {};
  // An actor who speaks in the conversation needs a role when the sealed schema says so (its `then`).
  const needsRole =
    (schema.required || []).includes("role") ||
    (Boolean(value("channel")) &&
      (schema.then?.required || []).includes("role"));
  const roleMissing = needsRole && !value("role").trim();
  // The channels with a loudness, as the rules give them.
  const loud = (one) => (context.loudness.channels || []).includes(one);

  // A field's length limit is the one its record's schema declares, when it declares one.
  const limit = (name) => schema.properties?.[name]?.maxLength;
  const bounded = (name, helperText) => {
    const max = limit(name);
    return {
      inputProps: { maxLength: max },
      helperText:
        max && String(value(name)).length >= max ? F.atLimit(max) : helperText,
    };
  };
  const text = (name, label, extra = {}) => (
    <TextField
      size="small"
      label={label}
      value={value(name)}
      onChange={set(name)}
      {...extra}
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
      {...extra}
    >
      {withStart(options, initial[name], startLabel).map(([id, words]) => (
        <MenuItem key={id || "empty"} value={id}>
          {words}
        </MenuItem>
      ))}
    </TextField>
  );
  const permitted = value("permitted");
  const unknownActions = permitted.filter((one) => !actionIds.includes(one));
  const describe = (id) =>
    actions.find((one) => one.action === id)?.description || "";
  const channel = value("channel");
  const timingKind = value("timing");

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
          </Box>
          {/* Above the scrolling fields, so a refused save shows wherever the form is scrolled to. */}
          {error && (
            <Alert severity="error" sx={{ mx: 2.5, mt: 1.5, flexShrink: 0 }}>
              {error}
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
            {text("name", F.name, bounded("name", F.nameHelp))}
            {text("id", F.id, {
              value: existing
                ? actor.id
                : "id" in fields
                  ? fields.id
                  : suggestedId,
              helperText: existing
                ? F.idFixed
                : takenBy
                  ? F.idTaken(takenBy.name)
                  : idInvalid
                    ? F.idInvalid(ruleText)
                    : F.idHelp(ruleText),
              error: Boolean(takenBy) || idInvalid,
              disabled: existing,
              placeholder: suggestedId,
            })}
            {text(
              "role",
              needsRole ? F.roleRequired : F.role,
              bounded("role", F.roleHelp),
            )}
            <>
              {choice(
                "channel",
                F.channel,
                [
                  ["", F.noChannel],
                  ...Object.keys(context.channels).map((id) => [
                    id,
                    CHANNEL_LABELS[id] || id,
                  ]),
                ],
                {
                  helperText: channel
                    ? context.channels[channel]
                    : F.noChannelHelp,
                  startLabel: initial.channel
                    ? CHANNEL_LABELS[initial.channel]
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
                  {loud(channel) && context.loudness.automatic && (
                    <Loudness
                      value={value("loudness_ratio")}
                      automatic={context.loudness.automatic}
                      onChange={(next) => put("loudness_ratio", next)}
                    />
                  )}
                </>
              )}

              {choice("kind", F.kind, Object.entries(F.kinds))}
              {many("facts", F.facts, F.factsHelp)}
              <Autocomplete
                multiple
                size="small"
                options={context.knowledgeSources?.files || []}
                value={value("files")}
                onChange={(_, next) => put("files", next)}
                renderInput={(params) => (
                  <TextField
                    {...params}
                    label={F.files}
                    helperText={
                      context.knowledgeSources?.files?.length
                        ? F.filesHelp
                        : F.noFiles
                    }
                  />
                )}
              />
              {context.upload && (
                <Box>
                  <Button
                    size="small"
                    component="label"
                    disabled={uploading}
                    sx={{ alignSelf: "flex-start" }}
                  >
                    {uploading ? F.uploading : F.upload}
                    <input
                      hidden
                      type="file"
                      accept=".md,.txt,.json,.csv"
                      onChange={async (event) => {
                        const file = event.target.files?.[0];
                        event.target.value = "";
                        if (!file) return;
                        setUploading(true);
                        setUploadError("");
                        try {
                          const path = await context.upload(file);
                          if (path)
                            put("files", [
                              ...new Set([...value("files"), path]),
                            ]);
                        } catch (failure) {
                          setUploadError(refusalText(failure, F.uploadFailed));
                        } finally {
                          setUploading(false);
                        }
                      }}
                    />
                  </Button>
                  <Typography
                    sx={{
                      typography: "s3",
                      color: uploadError ? "error.main" : "text.secondary",
                    }}
                  >
                    {uploadError || F.uploadHelp}
                  </Typography>
                </Box>
              )}
              <Autocomplete
                multiple
                size="small"
                options={(context.knowledgeSources?.records || []).map(
                  recordKey,
                )}
                value={value("recordsRead")}
                onChange={(_, next) => put("recordsRead", next)}
                renderInput={(params) => (
                  <TextField
                    {...params}
                    label={F.recordsRead}
                    helperText={
                      context.knowledgeSources?.records?.length
                        ? F.recordsHelp
                        : F.noRecords
                    }
                  />
                )}
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
                    error={unknownActions.length > 0}
                    helperText={
                      unknownActions.length
                        ? F.permittedUnknown(unknownActions)
                        : actions.length
                          ? F.permittedHelp
                          : F.noWorldActions
                    }
                  />
                )}
              />
              {choice("play", F.play, Object.entries(F.plays), {
                helperText: F.playHelp,
              })}
              {choice(
                "mode",
                F.trigger,
                modesFor(channel).map((mode) => [mode, F.modes[mode]]),
                {
                  helperText: F.triggerHelp,
                  startLabel:
                    initial.mode === KEPT_TRIGGER
                      ? C.comesInLabel(activation.trigger)
                      : F.modes[initial.mode],
                },
              )}
              {value("mode") === "on_tool" &&
                choice(
                  "triggerTool",
                  F.triggerTool,
                  actionIds.map((id) => [id, id]),
                  {
                    startLabel: initial.triggerTool,
                    error: Boolean(comesIn.triggerTool),
                    helperText: comesIn.triggerTool,
                  },
                )}
              {value("mode") === "on_keyword" &&
                text("triggerWords", F.triggerWords, {
                  error: Boolean(comesIn.triggerWords),
                  helperText: comesIn.triggerWords || F.triggerWordsHelp,
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
                    (context.knowledgeSources?.records || []).map((one) => [
                      one.collection,
                      one.collection,
                    ]),
                    {
                      startLabel: initial.triggerCollection,
                      error: Boolean(comesIn.triggerCollection),
                      helperText: comesIn.triggerCollection,
                    },
                  )}
                  {text("triggerWhere", F.triggerWhere, {
                    error: Boolean(comesIn.triggerWhere),
                    helperText: comesIn.triggerWhere || F.triggerWhereHelp,
                  })}
                </>
              )}
              {text("timeout", F.timeout, {
                type: "number",
                helperText: F.timeoutHelp,
                inputProps: { min: 0 },
              })}
              {choice(
                "fallback",
                F.fallback,
                [
                  ...Object.entries(F.fallbacks),
                  ...permitted.map((id) => [id, F.takesAction(id)]),
                  ...others.map((one) => [one.id, F.answeredBy(one.name)]),
                ],
                {
                  startLabel: permitted.includes(initial.fallback)
                    ? F.takesAction(initial.fallback)
                    : F.answeredBy(nameOf(initial.fallback)),
                },
              )}
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
              {text(
                channel ? "max_lines" : "max_turns",
                channel ? F.maxLines : F.maxTurns,
                {
                  type: "number",
                  helperText: F.noLimitHelp,
                  inputProps: { min: 1 },
                },
              )}
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
          <Stack
            direction="row"
            spacing={1}
            justifyContent="flex-end"
            sx={{
              px: 2.5,
              py: 2,
              flexShrink: 0,
              borderTop: "1px solid",
              borderColor: "divider",
            }}
          >
            <Button onClick={onClose} disabled={busy}>
              {F.cancel}
            </Button>
            <Button
              variant="contained"
              color="primary"
              disabled={
                busy ||
                conflict === "removed" ||
                (existing && !changed) ||
                Boolean(takenBy) ||
                idInvalid ||
                nameMissing ||
                roleMissing ||
                comesInIncomplete
              }
              onClick={() => onSave(toRecord(actor, fields, context))}
            >
              {F.save}
            </Button>
          </Stack>
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
  actor: PropTypes.object,
  context: PropTypes.shape({
    actions: PropTypes.arrayOf(
      PropTypes.shape({
        action: PropTypes.string,
        tool: PropTypes.string,
        description: PropTypes.string,
      }),
    ),
    records: PropTypes.object,
    channels: PropTypes.object,
    loudness: PropTypes.object,
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
    }),
    others: PropTypes.array,
  }).isRequired,
  busy: PropTypes.bool,
  error: PropTypes.string,
  // The draft moved under this form: its record is the fresh one with the typed fields kept, or it is gone.
  conflict: PropTypes.oneOf(["changed", "removed"]),
  onClose: PropTypes.func,
  onSave: PropTypes.func,
};
