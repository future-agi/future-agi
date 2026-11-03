import PropTypes from "prop-types";
import { useState } from "react";
import {
  Alert,
  Autocomplete,
  Box,
  Button,
  Collapse,
  MenuItem,
  Stack,
  TextField,
  Typography,
} from "@mui/material";
import {
  useActors,
  useActorView,
  useEditActor,
} from "src/api/simulate-environments/versions";
import SideDrawer from "../../components/SideDrawer";
import SectionCard from "../../components/SectionCard";
import { genderLabel } from "../scenarios/personaLabels";
import { CONTRACT_COPY } from "./contract.constants";

const C = CONTRACT_COPY.worldActors;
const CHANNEL_LABELS = CONTRACT_COPY.subActors.channels;
const lines = (value) =>
  (value || "")
    .split("\n")
    .map((one) => one.trim())
    .filter(Boolean);
const words = (key) => key.replace(/_/g, " ");
// A view value as a person reads it: lists joined, objects as "field: value" pairs, never raw JSON. A typed entry
// reads as its content (a fact's text), and a `kind` tag as its value alone.
const readable = (value) => {
  if (value == null) return "";
  if (Array.isArray(value))
    return value.map(readable).filter(Boolean).join("; ");
  if (typeof value === "object") {
    if (typeof value.text === "string") return value.text;
    return Object.entries(value)
      .map(([key, one]) => [key, readable(one)])
      .filter(([, one]) => one)
      .map(([key, one]) => (key === "kind" ? one : `${words(key)}: ${one}`))
      .join(" · ");
  }
  return String(value);
};
// `never_sees` names: grader material (`scenario.*`, `file.*`) and other actors' private fields (`<id>.<field>`).
const hidden = (names) => {
  const grader = [];
  const others = {};
  for (const name of names || []) {
    const [head, ...rest] = name.split(".");
    const field = rest.join(".");
    // A file keeps its own name; a field reads as words.
    if (head === "file") grader.push(field);
    else if (head === "scenario") grader.push(words(field));
    else (others[head] ||= []).push(words(field));
  }
  return { grader, others };
};
// A sub-actor speaks on a call channel; a world actor has none.
const speaks = (actor) => Boolean(actor?.channel);

/**
 * The environment's cast, read from and edited in its draft: people on the caller's side who
 * speak on the call, and people and systems in the world who act through its actions. A saved
 * version changes only when the draft is saved from the Save bar. Each row opens what that actor
 * sees and never sees.
 */
export default function WorldActorsPanel({
  envId,
  locked = false,
  callerSide = false,
}) {
  const { data } = useActors(envId);
  const edit = useEditActor(envId);
  const [editing, setEditing] = useState(null);
  const cast = data?.actors || [];
  const caller = cast.filter(speaks);
  const world = cast.filter((one) => !speaks(one) && one.side !== "caller");
  const revision = data?.revision ?? 0;
  const context = {
    actions: data?.actions || [],
    channels: data?.channels || {},
    others: cast,
  };

  const remove = (actorId) => edit.mutate({ op: "delete", actorId, revision });
  const rows = (group) =>
    group.map((actor) => (
      <ActorRow
        key={actor.id}
        envId={envId}
        actor={actor}
        locked={locked}
        onEdit={() => setEditing(actor)}
        onRemove={() => remove(actor.id)}
      />
    ));

  return (
    <SectionCard title={C.title(cast.length)} subtitle={C.subtitle}>
      <Stack spacing={1.5} sx={{ px: 2.5, py: 1.5 }}>
        {data?.source === "draft" && <Alert severity="info">{C.unsaved}</Alert>}
        {edit.error && !editing && (
          <Alert severity="error">{edit.error.message}</Alert>
        )}
        {(callerSide || caller.length > 0) && (
          <Group
            title={C.callerSide}
            about={C.callerSideAbout}
            empty={C.callerSideEmpty}
            locked={locked}
            addLabel={C.addCallerSide}
            onAdd={() => setEditing({ channel: "audio_mix" })}
          >
            {rows(caller)}
          </Group>
        )}
        <Group
          title={C.worldSide}
          about={C.worldSideAbout}
          empty={C.empty}
          locked={locked}
          addLabel={C.add}
          onAdd={() => setEditing({})}
        >
          {rows(world)}
        </Group>
      </Stack>
      <ActorForm
        key={
          editing
            ? editing.id || `new-${editing.channel ? "caller" : "world"}`
            : "closed"
        }
        actor={editing}
        context={context}
        busy={edit.isPending}
        error={editing ? edit.error?.message : null}
        onClose={() => {
          setEditing(null);
          edit.reset();
        }}
        onSave={(actor) =>
          edit.mutate(
            {
              op: editing?.id ? "replace" : "create",
              actor,
              actorId: editing?.id,
              revision,
            },
            { onSuccess: () => setEditing(null) },
          )
        }
      />
    </SectionCard>
  );
}

WorldActorsPanel.propTypes = {
  envId: PropTypes.string.isRequired,
  locked: PropTypes.bool,
  // The environment can have people on the caller's side (a voice call).
  callerSide: PropTypes.bool,
};

function Group({ title, about, empty, locked, addLabel, onAdd, children }) {
  const count = Array.isArray(children) ? children.length : 0;
  return (
    <Stack spacing={1}>
      <Box>
        <Typography sx={{ typography: "s1", fontWeight: 700 }}>
          {title}
        </Typography>
        <Typography sx={{ typography: "s3", color: "text.secondary" }}>
          {about}
        </Typography>
      </Box>
      {count === 0 && (
        <Typography sx={{ typography: "s2", color: "text.secondary" }}>
          {empty}
        </Typography>
      )}
      {children}
      {!locked && (
        <Box>
          <Button size="small" variant="outlined" onClick={onAdd}>
            {addLabel}
          </Button>
        </Box>
      )}
    </Stack>
  );
}

Group.propTypes = {
  title: PropTypes.string,
  about: PropTypes.string,
  empty: PropTypes.string,
  locked: PropTypes.bool,
  addLabel: PropTypes.string,
  onAdd: PropTypes.func,
  children: PropTypes.node,
};

function summary(actor) {
  if (speaks(actor))
    return [
      CHANNEL_LABELS[actor.channel] || actor.channel,
      genderLabel(actor.persona?.gender),
      actor.persona?.age_group,
      actor.persona?.accent,
      CONTRACT_COPY.subActors.scenarios(actor.scenarios ?? 0),
    ]
      .filter(Boolean)
      .join(" · ");
  const permitted = actor.permitted_actions || [];
  const tool = actor.activation?.trigger?.tool;
  const fallback = actor.activation?.fallback || "silent";
  return [
    `${C.mayDo}: ${permitted.length ? permitted.join(", ") : C.nothing}`,
    `${C.comesIn}: ${tool ? C.whenCalled(tool) : C.atStart}`,
    `${C.ifNoAnswer}: ${C.form.fallbackLabel(fallback)}`,
  ].join(" · ");
}

function ActorRow({ envId, actor, locked, onEdit, onRemove }) {
  const [open, setOpen] = useState(false);
  const { data: view } = useActorView(envId, actor.id, null, {
    enabled: open,
  });
  const shown = hidden(view?.never_sees);
  const quiet = {
    typography: "s3",
    color: "text.secondary",
    wordBreak: "break-word",
  };
  return (
    <Box
      sx={{
        border: "1px solid",
        borderColor: "divider",
        borderRadius: 1,
        p: 1.5,
      }}
    >
      <Stack direction="row" spacing={1} alignItems="flex-start">
        <Box
          flex={1}
          minWidth={0}
          onClick={() => setOpen((o) => !o)}
          sx={{ cursor: "pointer" }}
        >
          <Typography sx={{ typography: "s1", fontWeight: 600 }}>
            {actor.name}
            {actor.role ? (
              <Box
                component="span"
                sx={{ color: "text.secondary", fontWeight: 400 }}
              >{` · ${actor.role}`}</Box>
            ) : null}
          </Typography>
          {actor.goal ? (
            <Typography
              sx={{ typography: "s2", color: "text.secondary" }}
            >{`${C.wants}: ${actor.goal}`}</Typography>
          ) : null}
          <Typography
            sx={{
              typography: "s2",
              color: "text.secondary",
              wordBreak: "break-word",
            }}
          >
            {summary(actor)}
          </Typography>
        </Box>
        {!locked && (
          <>
            <Button size="small" onClick={onEdit}>
              {C.edit}
            </Button>
            <Button
              size="small"
              color="error"
              onClick={() => {
                // Closed first, so the removed actor's view is not read again.
                setOpen(false);
                onRemove();
              }}
            >
              {C.remove}
            </Button>
          </>
        )}
      </Stack>
      <Collapse in={open} unmountOnExit>
        {view && (
          <Stack spacing={0.75} sx={{ mt: 1.25 }}>
            <Typography sx={{ typography: "s2", fontWeight: 600 }}>
              {C.sees}
            </Typography>
            {view.sees.map((one) => (
              <Typography key={one.input} sx={quiet}>
                {`${C.inputs[one.input] || one.input}: ${readable(one.value)}`}
              </Typography>
            ))}
            <Typography sx={{ typography: "s2", fontWeight: 600 }}>
              {C.neverSees}
            </Typography>
            {shown.grader.length > 0 && (
              <Typography
                sx={quiet}
              >{`${C.graderMaterial}: ${shown.grader.join(", ")}`}</Typography>
            )}
            {Object.entries(shown.others).map(([id, fields]) => (
              <Typography
                key={id}
                sx={quiet}
              >{`${C.othersPrivate(id)} ${fields.join(", ")}`}</Typography>
            ))}
          </Stack>
        )}
      </Collapse>
    </Box>
  );
}

ActorRow.propTypes = {
  envId: PropTypes.string.isRequired,
  actor: PropTypes.object.isRequired,
  locked: PropTypes.bool,
  onEdit: PropTypes.func,
  onRemove: PropTypes.func,
};

const CONSTRAINTS = ["may_agree", "private", "escalate_when", "stop_when"];

function ActorForm({ actor, context, busy, error, onClose, onSave }) {
  const F = C.form;
  const existing = Boolean(actor?.id);
  const callerSide = speaks(actor);
  const [fields, setFields] = useState({});
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

  const save = () => {
    const base = {
      ...actor,
      id: value("id", actor?.id || ""),
      name: value("name", actor?.name || ""),
      role: value("role", actor?.role || ""),
    };
    if (callerSide) {
      const channel = value("channel", actor.channel);
      const loudness = value("loudness_ratio", actor.loudness_ratio ?? "");
      onSave({
        ...base,
        side: "caller",
        channel,
        loudness_ratio:
          channel === "audio_mix" && loudness !== "" ? Number(loudness) : null,
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
  const describe = (id) =>
    actions.find((one) => one.action === id)?.description || "";
  const channel = value("channel", actor?.channel);
  const timingKind = value("timing", timing.kind || "immediate");

  return (
    <SideDrawer
      open={Boolean(actor)}
      onClose={close}
      width={{ xs: "100%", sm: 520 }}
    >
      {actor && (
        <Stack spacing={2} sx={{ p: 2.5 }}>
          <Typography sx={{ typography: "s1", fontWeight: 700 }}>
            {existing
              ? F.editTitle(actor.name)
              : callerSide
                ? F.createCallerTitle
                : F.createTitle}
          </Typography>
          {error && <Alert severity="error">{error}</Alert>}
          {text("id", F.id, actor.id || "", {
            helperText: F.idHelp,
            disabled: existing,
          })}
          {text("name", F.name, actor.name || "", { helperText: F.nameHelp })}
          {text("role", F.role, actor.role || "", { helperText: F.roleHelp })}
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
              {channel === "audio_mix" &&
                text("loudness_ratio", F.loudness, actor.loudness_ratio ?? "", {
                  type: "number",
                  helperText: F.loudnessHelp,
                  inputProps: { min: 0.05, max: 1, step: 0.05 },
                })}
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
              {text("goal", F.goal, actor.goal || "", {
                multiline: true,
                minRows: 2,
                helperText: F.goalHelp,
              })}
              {many("may_agree", F.mayAgree, constraints.may_agree)}
              {many("private", F.private, constraints.private)}
              {many("escalate_when", F.escalateWhen, constraints.escalate_when)}
              {many("stop_when", F.stopWhen, constraints.stop_when)}
              {many(
                "facts",
                F.facts,
                facts.map((one) => one.text),
                F.factsHelp,
              )}
              {text("style", F.style, actor.behaviour?.style || "", {
                helperText: F.styleHelp,
              })}
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
                    helperText={
                      actions.length ? F.permittedHelp : F.noWorldActions
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
            </>
          )}
          <Stack direction="row" spacing={1} justifyContent="flex-end">
            <Button onClick={close}>{F.cancel}</Button>
            <Button variant="contained" disabled={busy} onClick={save}>
              {F.save}
            </Button>
          </Stack>
        </Stack>
      )}
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
    channels: PropTypes.object,
    others: PropTypes.array,
  }).isRequired,
  busy: PropTypes.bool,
  error: PropTypes.string,
  onClose: PropTypes.func,
  onSave: PropTypes.func,
};
