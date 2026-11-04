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
import { ACTORS_COPY } from "./actors.constants";
import { idFrom, idRule, lines, speaks } from "./actorUtils";

const C = ACTORS_COPY.worldActors;
const CHANNEL_LABELS = ACTORS_COPY.subActors.channels;

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

const CONSTRAINTS = ["may_agree", "private", "escalate_when", "stop_when"];
// The persona fields a sub-actor's voice is picked from.
const VOICE_FIELDS = ["gender", "age_group", "accent"];

export default function ActorForm({
  actor,
  context,
  busy,
  error,
  onClose,
  onSave,
}) {
  const F = C.form;
  const existing = Boolean(actor?.id);
  const callerSide = speaks(actor);
  const [fields, setFields] = useState({});
  const [more, setMore] = useState(false);
  const value = (name, fallback = "") =>
    name in fields ? fields[name] : fallback;
  const set = (name) => (event) =>
    setFields((current) => ({ ...current, [name]: event.target.value }));
  const put = (name, next) =>
    setFields((current) => ({ ...current, [name]: next }));
  const close = () => {
    setFields({});
    onClose();
  };
  // Escape, the close button and a click outside can all be accidents; Cancel is not, so it never asks.
  const [leaving, setLeaving] = useState(false);
  const leave = () => {
    if (Object.keys(fields).length > 0) setLeaving(true);
    else close();
  };
  const constraints = actor?.constraints || {};
  const activation = actor?.activation || {};
  const timing = actor?.behaviour?.response_timing || {};
  const knowledge = actor?.knowledge || [];
  const facts = knowledge.filter((one) => one?.kind === "facts");
  const actions = context.actions;
  const actionIds = actions.map((one) => one.action);
  const others = context.others.filter(
    (one) => one.id !== actor?.id && !speaks(one),
  );

  // A new actor's id follows its name until someone types an id of their own; a saved id never changes.
  const madeId = existing
    ? actor.id
    : idFrom(value("name", ""), new Set(context.others.map((one) => one.id)));
  // An emptied id falls back to the one made from the name.
  const typedId = existing ? actor.id : value("id", "").trim() || madeId;
  const takenBy = existing
    ? null
    : context.others.find((one) => one.id === typedId);
  const rule = idRule(context.records);
  const idInvalid = !existing && Boolean(rule) && !rule.test(typedId);
  const nameMissing = !value("name", actor?.name || "").trim();
  // The record schema this actor is checked against, as its environment's rules declare it.
  const schema =
    context.records[callerSide ? "sub_actors.json" : "actors.json"] || {};
  const needsRole = (schema.required || []).includes("role");
  const roleMissing = needsRole && !value("role", actor?.role || "").trim();
  // The channels with a loudness, as the rules give them.
  const loud = (one) => (context.loudness.channels || []).includes(one);
  const save = () => {
    const base = {
      ...actor,
      id: typedId,
      name: value("name", actor?.name || ""),
      role: value("role", actor?.role || ""),
    };
    if (callerSide) {
      const channel = value("channel", actor.channel);
      const loudness = value("loudness_ratio", actor.loudness_ratio ?? null);
      const persona = { ...(actor.persona || {}) };
      for (const name of VOICE_FIELDS) {
        const chosen = value(name, persona[name] || "");
        if (chosen) persona[name] = chosen;
        else delete persona[name];
      }
      onSave({
        ...base,
        side: "caller",
        channel,
        persona,
        loudness_ratio: loud(channel) ? loudness : null,
      });
      return;
    }
    const tool = value("tool", activation.trigger?.tool || "");
    // Only an edit to "Comes in" changes the trigger; any other kind (delay, contact, chance) is kept as it was.
    const trigger =
      "tool" in fields || !activation.trigger
        ? tool
          ? { kind: "on_tool", tool }
          : { kind: "always" }
        : activation.trigger;
    const kind = value("timing", timing.kind || "immediate");
    const parent = value("parent", actor?.parent || "");
    const wait = value("timeout", activation.timeout_seconds ?? 0);
    onSave({
      ...base,
      kind: value("kind", actor?.kind || "person"),
      parent: parent || null,
      goal: value("goal", actor?.goal || ""),
      constraints: {
        ...constraints,
        ...Object.fromEntries(
          CONSTRAINTS.map((name) => [
            name,
            lines(value(name, (constraints[name] || []).join("\n"))),
          ]),
        ),
      },
      knowledge: [
        ...knowledge.filter((one) => one?.kind !== "facts"),
        ...lines(value("facts", facts.map((one) => one.text).join("\n"))).map(
          (text) => ({ kind: "facts", text }),
        ),
      ],
      behaviour: {
        ...(actor?.behaviour || {}),
        style: value("style", actor?.behaviour?.style || ""),
        response_timing:
          kind === "delayed"
            ? {
                kind,
                seconds: Number(value("seconds", timing.seconds ?? 0)) || 0,
              }
            : { kind: "immediate", seconds: 0 },
      },
      permitted_actions: value("permitted", actor?.permitted_actions || []),
      activation: {
        ...activation,
        trigger,
        timeout_seconds: Number(wait) || 0,
        fallback: value("fallback", activation.fallback || "silent"),
      },
    });
  };

  // A field's length limit is the one its record's schema declares, when it declares one.
  const limit = (name) => schema.properties?.[name]?.maxLength;
  const bounded = (name, initial, helperText) => {
    const max = limit(name);
    return {
      inputProps: { maxLength: max },
      helperText:
        max && String(value(name, initial)).length >= max
          ? F.atLimit(max)
          : helperText,
    };
  };
  const text = (name, label, initial, extra = {}) => (
    <TextField
      size="small"
      label={label}
      value={value(name, initial)}
      onChange={set(name)}
      {...extra}
    />
  );
  const many = (name, label, initial, helperText) =>
    text(name, label, (initial || []).join("\n"), {
      multiline: true,
      minRows: 2,
      helperText,
    });
  const permitted = value("permitted", actor?.permitted_actions || []);
  const unknownActions = permitted.filter((one) => !actionIds.includes(one));
  const describe = (id) =>
    actions.find((one) => one.action === id)?.description || "";
  const channel = value("channel", actor?.channel);
  const timingKind = value("timing", timing.kind || "immediate");

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
              {existing
                ? F.editTitle(actor.name)
                : callerSide
                  ? F.createCallerTitle
                  : F.createTitle}
            </Typography>
          </Box>
          {/* Above the scrolling fields, so a refused save shows wherever the form is scrolled to. */}
          {error && (
            <Alert severity="error" sx={{ mx: 2.5, mt: 1.5, flexShrink: 0 }}>
              {error}
            </Alert>
          )}
          <Stack
            spacing={2}
            sx={{ p: 2.5, flex: 1, minHeight: 0, overflowY: "auto" }}
          >
            {text(
              "name",
              F.name,
              actor.name || "",
              bounded("name", actor.name || "", F.nameHelp),
            )}
            {text("id", F.id, madeId, {
              helperText: existing
                ? F.idFixed
                : takenBy
                  ? F.idTaken(takenBy.name)
                  : idInvalid
                    ? F.idInvalid
                    : F.idHelp,
              error: Boolean(takenBy) || idInvalid,
              disabled: existing,
              placeholder: madeId,
              InputLabelProps: { shrink: true },
            })}
            {text(
              "role",
              needsRole ? F.roleRequired : F.role,
              actor.role || "",
              bounded("role", actor.role || "", F.roleHelp),
            )}
            {callerSide ? (
              <>
                <TextField
                  select
                  size="small"
                  label={F.channel}
                  value={channel}
                  onChange={set("channel")}
                  helperText={context.channels[channel]}
                >
                  {Object.keys(context.channels).map((id) => (
                    <MenuItem key={id} value={id}>
                      {CHANNEL_LABELS[id] || id}
                    </MenuItem>
                  ))}
                </TextField>
                <Typography sx={{ typography: "s2", fontWeight: 600 }}>
                  {F.voice}
                </Typography>
                <Stack direction="row" spacing={1}>
                  {[
                    ["gender", F.gender, Object.entries(F.genders)],
                    ["age_group", F.ageGroup, Object.entries(F.ageGroups)],
                    [
                      "accent",
                      F.accent,
                      F.accents.map((one) => [one, one || F.anyAccent]),
                    ],
                  ].map(([name, label, options]) => {
                    const current = value(name, actor.persona?.[name] || "");
                    const shown = options.some(([id]) => id === current)
                      ? options
                      : [...options, [current, current]];
                    return (
                      <TextField
                        key={name}
                        select
                        fullWidth
                        size="small"
                        label={label}
                        value={current}
                        onChange={set(name)}
                      >
                        {shown.map(([id, text]) => (
                          <MenuItem key={id || "any"} value={id}>
                            {text}
                          </MenuItem>
                        ))}
                      </TextField>
                    );
                  })}
                </Stack>
                <Typography sx={{ typography: "s3", color: "text.secondary" }}>
                  {F.voiceHelp}
                </Typography>
                {loud(channel) && context.loudness.automatic && (
                  <Loudness
                    value={value(
                      "loudness_ratio",
                      actor.loudness_ratio ?? null,
                    )}
                    automatic={context.loudness.automatic}
                    onChange={(next) => put("loudness_ratio", next)}
                  />
                )}
              </>
            ) : (
              <>
                <TextField
                  select
                  size="small"
                  label={F.kind}
                  value={value("kind", actor.kind || "person")}
                  onChange={set("kind")}
                >
                  {Object.entries(F.kinds).map(([id, label]) => (
                    <MenuItem key={id} value={id}>
                      {label}
                    </MenuItem>
                  ))}
                </TextField>
                {many(
                  "facts",
                  F.facts,
                  facts.map((one) => one.text),
                  F.factsHelp,
                )}
                <Stack direction="row" spacing={1}>
                  <TextField
                    select
                    size="small"
                    label={F.answers}
                    sx={{ flex: 1 }}
                    value={timingKind}
                    onChange={set("timing")}
                  >
                    <MenuItem value="immediate">{F.immediately}</MenuItem>
                    <MenuItem value="delayed">{F.afterDelay}</MenuItem>
                  </TextField>
                  {timingKind === "delayed" &&
                    text("seconds", F.seconds, timing.seconds ?? 0, {
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
                <TextField
                  select
                  size="small"
                  label={F.trigger}
                  value={value("tool", activation.trigger?.tool || "")}
                  onChange={set("tool")}
                  helperText={F.triggerHelp}
                >
                  <MenuItem value="">{C.atStart}</MenuItem>
                  {[
                    ...new Set([
                      ...actionIds,
                      ...(activation.trigger?.tool
                        ? [activation.trigger.tool]
                        : []),
                    ]),
                  ].map((id) => (
                    <MenuItem key={id} value={id}>
                      {C.whenCalled(id)}
                    </MenuItem>
                  ))}
                </TextField>
                {text("timeout", F.timeout, activation.timeout_seconds ?? 0, {
                  type: "number",
                  helperText: F.timeoutHelp,
                  inputProps: { min: 0 },
                })}
                <TextField
                  select
                  size="small"
                  label={F.fallback}
                  value={value("fallback", activation.fallback || "silent")}
                  onChange={set("fallback")}
                >
                  {Object.entries(F.fallbacks).map(([id, label]) => (
                    <MenuItem key={id} value={id}>
                      {label}
                    </MenuItem>
                  ))}
                  {others.map((one) => (
                    <MenuItem key={one.id} value={one.id}>
                      {F.answeredBy(one.name)}
                    </MenuItem>
                  ))}
                </TextField>
                {/* Kept on the record and editable, but a run plays an actor from its scenario's actions and does not read these yet. */}
                <Button
                  size="small"
                  sx={{ alignSelf: "flex-start" }}
                  onClick={() => setMore((shown) => !shown)}
                >
                  {more ? F.lessDetails : F.moreDetails}
                </Button>
                <Collapse in={more}>
                  <Stack spacing={2}>
                    <Typography
                      sx={{ typography: "s3", color: "text.secondary" }}
                    >
                      {F.moreDetailsHelp}
                    </Typography>
                    {others.length > 0 && (
                      <TextField
                        select
                        size="small"
                        label={F.parent}
                        value={value("parent", actor.parent || "")}
                        onChange={set("parent")}
                        helperText={F.parentHelp}
                      >
                        <MenuItem value="">{F.nobody}</MenuItem>
                        {others.map((one) => (
                          <MenuItem key={one.id} value={one.id}>
                            {one.name}
                          </MenuItem>
                        ))}
                      </TextField>
                    )}
                    {text("goal", F.goal, actor.goal || "", {
                      multiline: true,
                      minRows: 2,
                      helperText: F.goalHelp,
                    })}
                    {many("may_agree", F.mayAgree, constraints.may_agree)}
                    {many("private", F.private, constraints.private)}
                    {many(
                      "escalate_when",
                      F.escalateWhen,
                      constraints.escalate_when,
                    )}
                    {many("stop_when", F.stopWhen, constraints.stop_when)}
                    {text("style", F.style, actor.behaviour?.style || "", {
                      helperText: F.styleHelp,
                    })}
                  </Stack>
                </Collapse>
              </>
            )}
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
            <Button onClick={close}>{F.cancel}</Button>
            <Button
              variant="contained"
              disabled={
                busy ||
                Boolean(takenBy) ||
                idInvalid ||
                nameMissing ||
                roleMissing
              }
              onClick={save}
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
              close();
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
    others: PropTypes.array,
  }).isRequired,
  busy: PropTypes.bool,
  error: PropTypes.string,
  onClose: PropTypes.func,
  onSave: PropTypes.func,
};
