import PropTypes from "prop-types";
import { useMemo, useState } from "react";
import { alpha } from "@mui/material/styles";
import {
  Box,
  Stack,
  Typography,
  Button,
  IconButton,
  Tooltip,
  Collapse,
  TextField,
  MenuItem,
  Chip,
  Switch,
  FormControlLabel,
} from "@mui/material";
import Iconify from "src/components/iconify";
import CopyCommandButton from "../components/CopyCommandButton";
import SideDrawer from "../components/SideDrawer";
import ActorSees from "./ActorSees";
import { effectiveModality } from "../_mock/rlContract";
import { editsLandInPlace } from "../_mock/versions";
import {
  ENTRY_KINDS,
  PRESSURE_KINDS,
  MODALITIES,
  BEHAVIOURS,
  FALLBACKS,
  ACTOR_KINDS,
  LOUDNESS,
  VOICE_GENDERS,
  VOICE_AGES,
  VOICE_ACCENTS,
  NOT_A_WORLD_ACTION,
  SIDES,
  sideOf,
  roleOf,
  callerKindsFor,
  arrivalsFor,
  worldActionsOf,
  actorViewOf,
  actorCli,
} from "../_mock/actors";
import { freeIdFrom, problemsOf, ROLE_MAX } from "../_mock/actorForm";

const MONO = "ui-monospace, Menlo, monospace";

const lines = (text) =>
  String(text || "")
    .split("\n")
    .map((t) => t.trim())
    .filter(Boolean);

/* The form as the actor has it now; a new actor starts on the side it was added from. */
const formOf = (actor, { voiceEnv, surface }) => {
  const side = sideOf(actor);
  const callerKinds = callerKindsFor(voiceEnv ? "voice" : "chat");
  return {
    side,
    kind:
      side === "caller"
        ? callerKinds.find((k) => k.id === actor?.kind)?.id || callerKinds[0].id
        : actor?.kind || "person",
    id: actor?.id || "",
    name: actor?.name || "",
    role: actor?.role || (actor?.id ? roleOf(actor) : ""),
    blurb: actor?.blurb || "",
    pressure: actor?.pressure || "competing",
    entry: actor?.entry || "present",
    traits: (actor?.traits || []).join(", "),
    modalities: actor?.modalities || (surface ? [surface] : []),
    activatedBy: actor?.activatedBy || "",
    trigger: actor?.trigger || "",
    timeoutMin: actor?.timeoutMin ?? "",
    fallback: actor?.fallback || "silent",
    behaviour: actor?.behaviour || "cooperative",
    knowledge: (actor?.knowledge || []).join("\n"),
    actions: [...new Set([...(actor?.actions || []), ...(actor?.tools || [])])],
    sources: actor?.sources || [],
    canApprove: !!actor?.canApprove,
    approvalScope: actor?.approvalScope || "",
    arrival:
      actor?.arrival ||
      (side === "caller" ? (voiceEnv ? "answers" : "first-turn") : ""),
    chancePct: actor?.chancePct ?? 30,
    voiceGender: actor?.voice?.gender || "",
    voiceAge: actor?.voice?.age || "",
    voiceAccent: actor?.voice?.accent || "",
    loudness: actor?.loudness || "auto",
    goal: actor?.goal || "",
    agreesTo: actor?.agreesTo || "",
    keepsPrivate: actor?.keepsPrivate || "",
    handsOverWhen: actor?.handsOverWhen || "",
    doneWhen: actor?.doneWhen || "",
  };
};

/*
  What the world contract doesn't allow this actor (world.md): a write tool
  changes the customer's records and is the agent's to run, so an actor can
  approve it but not hold it; an outside company's agent can't authorise a
  change here; and approving needs something that writes. Shown before
  saving, with the way out; Save stays off until none is left.
*/
const permissionConflictsOf = (form, actions) => {
  const writes = actions.filter((a) => a.effect === "write").map((a) => a.name);
  const who = form.name.trim() || "This actor";
  const out = form.actions
    .filter((t) => writes.includes(t))
    .map((t) => ({
      id: `tool-${t}`,
      text: `${t} changes the customer's records, so only the agent can run it.${form.canApprove ? ` ${who} can approve it instead.` : ""}`,
      fix: "Remove",
      apply: (f) => ({ ...f, actions: f.actions.filter((x) => x !== t) }),
    }));
  if (form.canApprove && form.kind === "external-agent")
    out.push({
      id: "approve-external",
      text: `${who} belongs to another company, so its "approved" can't authorise a change in this world.`,
      fix: "Turn off",
      apply: (f) => ({ ...f, canApprove: false }),
    });
  else if (form.canApprove && writes.length === 0)
    out.push({
      id: "approve-nothing",
      text: "No tool in this world writes anything, so there is nothing to approve.",
      fix: "Turn off",
      apply: (f) => ({ ...f, canApprove: false }),
    });
  return out;
};

/**
 * Add or edit one actor in the environment's draft (TH-8927, PR #3698): name,
 * id, role, knowledge, permitted actions, when they come in, and what they see
 * and never see. Checked as you type; Save stays off until there is a name and
 * a valid, free id.
 */
export default function ActorEditor({
  actor,
  onClose,
  onSave,
  env,
  envState,
  cast,
  takenIds,
}) {
  return (
    <SideDrawer open={!!actor} onClose={onClose} width={480}>
      {/* Keyed so reopening starts from what the environment has now. */}
      {actor && (
        <ActorForm
          key={`${actor.id || `new-${actor.side || "world"}`}-${actor.editedAt || ""}`}
          actor={actor}
          existing={!!actor.id}
          onClose={onClose}
          onSave={onSave}
          env={env}
          envState={envState}
          cast={cast}
          takenIds={takenIds}
        />
      )}
    </SideDrawer>
  );
}
ActorEditor.propTypes = {
  env: PropTypes.object,
  envState: PropTypes.object,
  cast: PropTypes.array,
  takenIds: PropTypes.array,
  actor: PropTypes.object,
  onClose: PropTypes.func,
  onSave: PropTypes.func,
};

function Label({ children }) {
  return (
    <Typography
      sx={{
        typography: "s3",
        fontWeight: 600,
        color: "text.tertiary",
        letterSpacing: 0.4,
        mb: 0.75,
      }}
    >
      {children}
    </Typography>
  );
}
Label.propTypes = { children: PropTypes.node };

function ActorForm({
  actor,
  existing,
  onClose,
  onSave,
  env,
  envState,
  cast = [],
  takenIds = [],
}) {
  const voiceEnv = effectiveModality(env, envState) === "voice";
  const [form, setForm] = useState(() =>
    formOf(actor, { voiceEnv, surface: env?.surface }),
  );
  const [more, setMore] = useState(false);
  /* Until they edit the id by hand, it follows the name. */
  const [idTouched, setIdTouched] = useState(false);
  const [nameTouched, setNameTouched] = useState(false);
  const caller = form.side === "caller";
  const set = (k) => (e) => setForm((f) => ({ ...f, [k]: e.target.value }));
  const toggle = (k, v) =>
    setForm((f) => ({
      ...f,
      [k]: f[k].includes(v) ? f[k].filter((x) => x !== v) : [...f[k], v],
    }));

  const worldActions = useMemo(
    () => worldActionsOf(env, envState),
    [env, envState],
  );
  const known = new Set(worldActions.map((a) => a.name));
  const unknown = form.actions.filter((a) => !known.has(a));
  const sources = (envState?.sources || []).filter(
    (s) => s.inEnv && !s.archived,
  );
  const others = cast.filter((a) => a.id !== actor?.id);
  const kinds = caller
    ? callerKindsFor(voiceEnv ? "voice" : "chat")
    : ACTOR_KINDS.filter((k) => k.id !== "sub-agent");

  const onName = (e) => {
    const name = e.target.value;
    setNameTouched(true);
    setForm((f) => ({
      ...f,
      name,
      ...(!existing && !idTouched && { id: freeIdFrom(name, takenIds) }),
    }));
  };

  const problems = problemsOf(form, { takenIds, checkId: !existing });
  const conflicts = permissionConflictsOf(form, worldActions);

  /* What gets saved — only keys that mean something for this side. */
  const fields = {
    id: form.id,
    side: form.side,
    kind: form.kind,
    name: form.name.trim(),
    role: form.role.trim(),
    blurb: form.blurb.trim(),
    pressure: form.pressure,
    entry: caller
      ? form.arrival === "answers"
        ? "present"
        : "joins"
      : form.entry,
    traits: form.traits
      .split(",")
      .map((t) => t.trim())
      .filter(Boolean),
    modalities: form.modalities,
    activatedBy: caller ? null : form.activatedBy || null,
    trigger: caller ? "" : form.trigger.trim(),
    timeoutMin: form.timeoutMin === "" ? null : Number(form.timeoutMin),
    fallback: form.fallback,
    behaviour: form.behaviour,
    knowledge: lines(form.knowledge),
    sources: form.sources,
    canApprove: !caller && form.canApprove,
    approvalScope: !caller && form.canApprove ? form.approvalScope.trim() : "",
    actions: caller ? [] : form.actions,
    goal: form.goal.trim(),
    agreesTo: form.agreesTo.trim(),
    keepsPrivate: form.keepsPrivate.trim(),
    handsOverWhen: form.handsOverWhen.trim(),
    doneWhen: form.doneWhen.trim(),
    ...(caller && {
      arrival: form.arrival,
      chancePct: form.arrival === "chance" ? Number(form.chancePct) : undefined,
    }),
    ...(caller &&
      voiceEnv && {
        voice: {
          gender: form.voiceGender,
          age: form.voiceAge,
          accent: form.voiceAccent,
        },
        loudness: form.loudness === "auto" ? "" : form.loudness,
      }),
  };
  /* An older actor that never had a role keeps it unset when it is left as the default. */
  if (existing && !actor.role && fields.role === roleOf(actor))
    fields.role = "";
  if (existing) delete fields.id;

  const view = actorViewOf(
    { ...fields, id: actor?.id, name: fields.name || "This actor" },
    { env, envState, cast },
  );
  const dirty =
    JSON.stringify(form) !==
    JSON.stringify(formOf(actor, { voiceEnv, surface: env?.surface }));
  const valid = Object.keys(problems).length === 0 && conflicts.length === 0;
  const inPlace = editsLandInPlace(envState);

  const footnote = conflicts.length
    ? "Resolve the permission conflict to save"
    : problems.name && nameTouched
      ? problems.name
      : dirty && !inPlace
        ? "Stays in the draft until you save the version"
        : "";

  return (
    <Stack sx={{ height: "100%" }}>
      <Stack
        direction="row"
        alignItems="center"
        spacing={2}
        sx={{
          px: 2.5,
          py: 2,
          borderBottom: "1px solid",
          borderColor: "divider",
        }}
      >
        <Box flex={1} minWidth={0}>
          <Typography sx={{ typography: "m2", fontWeight: 600 }}>
            {existing ? actor.name : "Add actor"}
          </Typography>
          <Typography sx={{ typography: "s3", color: "text.tertiary" }}>
            {existing
              ? `${SIDES.find((s) => s.id === form.side).label} · saved in the draft for this environment only`
              : `${SIDES.find((s) => s.id === form.side).label} · added to this environment's draft`}
          </Typography>
        </Box>
        <IconButton size="small" onClick={onClose}>
          <Iconify
            icon="solar:close-circle-linear"
            width={20}
            sx={{ color: "text.tertiary" }}
          />
        </IconButton>
      </Stack>

      <Stack spacing={2.25} sx={{ p: 2.5, flex: 1, overflowY: "auto" }}>
        {!existing && (
          <TextField
            select
            size="small"
            label="Where are they"
            value={form.side}
            onChange={(e) => {
              const side = e.target.value;
              setForm((f) => ({
                ...formOf(
                  { side, name: f.name },
                  { voiceEnv, surface: env?.surface },
                ),
                id: f.id,
                role: f.role,
              }));
            }}
            fullWidth
          >
            {SIDES.map((s) => (
              <MenuItem key={s.id} value={s.id} sx={{ typography: "s2" }}>
                {s.label}
              </MenuItem>
            ))}
          </TextField>
        )}
        <TextField
          select
          size="small"
          label="Kind"
          value={form.kind}
          onChange={(e) => {
            const kind = e.target.value;
            setForm((f) => ({
              ...f,
              kind,
              ...(kind === "approver" && { canApprove: true }),
            }));
          }}
          fullWidth
        >
          {kinds.map((k) => (
            <MenuItem key={k.id} value={k.id} sx={{ display: "block" }}>
              <Typography sx={{ typography: "s2", fontWeight: 600 }}>
                {k.label}
              </Typography>
              <Typography sx={{ typography: "s3", color: "text.tertiary" }}>
                {k.note}
              </Typography>
            </MenuItem>
          ))}
        </TextField>
        <TextField
          size="small"
          label="Name"
          value={form.name}
          onChange={onName}
          onBlur={() => setNameTouched(true)}
          error={nameTouched && !!problems.name}
          helperText={nameTouched ? problems.name : ""}
          inputProps={{ "aria-label": "Name" }}
          fullWidth
        />
        <TextField
          size="small"
          label="Id"
          value={form.id}
          disabled={existing}
          onChange={(e) => {
            setIdTouched(true);
            setForm((f) => ({ ...f, id: e.target.value }));
          }}
          error={!existing && (idTouched || nameTouched) && !!problems.id}
          helperText={
            existing
              ? "Fixed once created"
              : (idTouched || nameTouched) && problems.id
                ? problems.id
                : "Made from the name; lowercase letters, digits, underscores"
          }
          InputProps={{ sx: { fontFamily: MONO } }}
          fullWidth
        />
        <TextField
          size="small"
          label="Role"
          value={form.role}
          onChange={set("role")}
          error={!!problems.role}
          helperText={
            problems.role ||
            (form.role.length > ROLE_MAX - 30
              ? `${form.role.length}/${ROLE_MAX}`
              : "Shown in the transcript — approver, colleague")
          }
          inputProps={{ maxLength: ROLE_MAX + 40 }}
          fullWidth
        />
        <TextField
          size="small"
          label="What they do"
          value={form.blurb}
          onChange={set("blurb")}
          fullWidth
          multiline
          minRows={2}
        />

        {caller ? (
          <>
            <TextField
              select
              size="small"
              label="When they come in"
              value={form.arrival}
              onChange={set("arrival")}
              fullWidth
            >
              {arrivalsFor(voiceEnv).map((a) => (
                <MenuItem key={a.id} value={a.id} sx={{ display: "block" }}>
                  <Typography sx={{ typography: "s2", fontWeight: 600 }}>
                    {a.label}
                  </Typography>
                  <Typography sx={{ typography: "s3", color: "text.tertiary" }}>
                    {a.blurb}
                  </Typography>
                </MenuItem>
              ))}
            </TextField>
            {form.arrival === "chance" && (
              <TextField
                size="small"
                type="number"
                label="Chance on each call (%)"
                value={form.chancePct}
                onChange={set("chancePct")}
                inputProps={{ min: 0, max: 100 }}
                sx={{ width: 220 }}
              />
            )}
          </>
        ) : (
          <>
            <TextField
              select
              size="small"
              label="When they enter"
              value={form.entry}
              onChange={set("entry")}
              fullWidth
            >
              {ENTRY_KINDS.map((k) => (
                <MenuItem key={k.id} value={k.id} sx={{ display: "block" }}>
                  <Typography sx={{ typography: "s2", fontWeight: 600 }}>
                    {k.label}
                  </Typography>
                  <Typography sx={{ typography: "s3", color: "text.tertiary" }}>
                    {k.blurb}
                  </Typography>
                </MenuItem>
              ))}
            </TextField>
            {(form.activatedBy ||
              ["joins", "contacted", "scheduled"].includes(form.entry)) && (
              <TextField
                size="small"
                label="What brings them in"
                value={form.trigger}
                onChange={set("trigger")}
                fullWidth
                placeholder="When an approval request is created"
              />
            )}
          </>
        )}

        <Box>
          <Label>What they know</Label>
          <TextField
            size="small"
            value={form.knowledge}
            onChange={set("knowledge")}
            fullWidth
            multiline
            minRows={2}
            placeholder="One fact per line — the deal, the amount, their own preference"
          />
          {sources.length > 0 && (
            <Stack
              direction="row"
              spacing={0.75}
              sx={{ mt: 1, flexWrap: "wrap", rowGap: 0.75 }}
            >
              {sources.map((src) => {
                const hidden = src.visibility === "evaluator";
                const on = form.sources.includes(src.id);
                return (
                  <Tooltip
                    key={src.id}
                    arrow
                    title={
                      hidden
                        ? "Evaluator-only — an actor never gets the expected answers."
                        : ""
                    }
                  >
                    <span>
                      <Chip
                        size="small"
                        label={src.name}
                        disabled={hidden}
                        onClick={() => toggle("sources", src.id)}
                        sx={{
                          color: on ? "text.primary" : "text.tertiary",
                          border: "1px solid",
                          borderColor: on ? "text.primary" : "divider",
                          bgcolor: "transparent",
                        }}
                      />
                    </span>
                  </Tooltip>
                );
              })}
            </Stack>
          )}
        </Box>

        {!caller && (
          <Box>
            <Label>What they&apos;re allowed to do</Label>
            {worldActions.length === 0 && unknown.length === 0 ? (
              <Typography sx={{ typography: "s3", color: "text.tertiary" }}>
                This environment has no actions yet — add tools on the Tools
                tab.
              </Typography>
            ) : (
              <Stack
                direction="row"
                spacing={0.75}
                sx={{ flexWrap: "wrap", rowGap: 0.75, mb: 1 }}
              >
                {worldActions.map((a) => {
                  const on = form.actions.includes(a.name);
                  const clash =
                    on && conflicts.some((c) => c.id === `tool-${a.name}`);
                  return (
                    <Tooltip key={a.name} arrow title={a.desc}>
                      <Chip
                        size="small"
                        label={a.name}
                        onClick={() => toggle("actions", a.name)}
                        sx={{
                          fontFamily: MONO,
                          color: on ? "text.primary" : "text.tertiary",
                          border: "1px solid",
                          borderColor: clash
                            ? "status.danger.border"
                            : on
                              ? "text.primary"
                              : "divider",
                          bgcolor: "transparent",
                        }}
                      />
                    </Tooltip>
                  );
                })}
                {unknown.map((name) => (
                  <Chip
                    key={name}
                    size="small"
                    label={`${name} — ${NOT_A_WORLD_ACTION}`}
                    onDelete={() => toggle("actions", name)}
                    sx={{
                      fontFamily: MONO,
                      color: "status.danger.fg",
                      border: "1px solid",
                      borderColor: "status.danger.border",
                      bgcolor: "transparent",
                    }}
                  />
                ))}
              </Stack>
            )}
            <FormControlLabel
              control={
                <Switch
                  size="small"
                  checked={form.canApprove}
                  onChange={(e) =>
                    setForm((f) => ({ ...f, canApprove: e.target.checked }))
                  }
                />
              }
              label={
                <Typography sx={{ typography: "s2" }}>
                  Can approve requests
                </Typography>
              }
            />
            {form.canApprove && (
              <Stack spacing={1.25} sx={{ mt: 1 }}>
                <TextField
                  size="small"
                  label="What they can approve"
                  value={form.approvalScope}
                  onChange={set("approvalScope")}
                  fullWidth
                  placeholder="Discounts up to 20%"
                />
                <Stack direction="row" spacing={1}>
                  <TextField
                    size="small"
                    type="number"
                    label="Answers within (min)"
                    value={form.timeoutMin}
                    onChange={set("timeoutMin")}
                    sx={{ width: 180 }}
                  />
                  <TextField
                    select
                    size="small"
                    label="If they don't answer"
                    value={form.fallback}
                    onChange={set("fallback")}
                    fullWidth
                  >
                    {FALLBACKS.map((f) => (
                      <MenuItem
                        key={f.id}
                        value={f.id}
                        sx={{ typography: "s2" }}
                      >
                        {f.label}
                      </MenuItem>
                    ))}
                  </TextField>
                </Stack>
              </Stack>
            )}
            {conflicts.length > 0 && (
              <Stack
                spacing={0.75}
                sx={{
                  mt: 1.25,
                  p: 1.25,
                  borderRadius: 1,
                  border: "1px solid",
                  borderColor: "status.danger.border",
                  bgcolor: "status.danger.bg",
                }}
              >
                <Typography sx={{ typography: "s2", fontWeight: 600 }}>
                  Permission conflict
                </Typography>
                {conflicts.map((c) => (
                  <Stack
                    key={c.id}
                    direction="row"
                    alignItems="flex-start"
                    spacing={1}
                  >
                    <Typography
                      sx={{
                        typography: "s3",
                        color: "text.secondary",
                        flex: 1,
                        minWidth: 0,
                      }}
                    >
                      {c.text}
                    </Typography>
                    <Button
                      size="small"
                      variant="outlined"
                      onClick={() => setForm(c.apply)}
                      sx={{ flexShrink: 0 }}
                    >
                      {c.fix}
                    </Button>
                  </Stack>
                ))}
              </Stack>
            )}
          </Box>
        )}

        {caller && voiceEnv && (
          <Box>
            <Label>Voice</Label>
            <Stack direction="row" spacing={1}>
              {[
                ["voiceGender", "Gender", VOICE_GENDERS],
                ["voiceAge", "Age", VOICE_AGES],
                ["voiceAccent", "Accent", VOICE_ACCENTS],
              ].map(([key, label, options]) => (
                <TextField
                  key={key}
                  select
                  size="small"
                  label={label}
                  value={form[key]}
                  onChange={set(key)}
                  fullWidth
                >
                  <MenuItem value="" sx={{ typography: "s2" }}>
                    Any
                  </MenuItem>
                  {options.map((o) => (
                    <MenuItem key={o} value={o} sx={{ typography: "s2" }}>
                      {o}
                    </MenuItem>
                  ))}
                </TextField>
              ))}
            </Stack>
            <TextField
              select
              size="small"
              label="Loudness"
              value={form.loudness}
              onChange={set("loudness")}
              sx={{ mt: 1.25, width: 220 }}
            >
              {LOUDNESS.map((l) => (
                <MenuItem key={l.id} value={l.id} sx={{ typography: "s2" }}>
                  {l.label}
                </MenuItem>
              ))}
            </TextField>
          </Box>
        )}

        <ActorSees view={view} name={fields.name} />

        <TextField
          select
          size="small"
          label="Pressure"
          value={form.pressure}
          onChange={set("pressure")}
          fullWidth
        >
          {PRESSURE_KINDS.map((k) => (
            <MenuItem key={k.id} value={k.id} sx={{ display: "block" }}>
              <Typography sx={{ typography: "s2", fontWeight: 600 }}>
                {k.label}
              </Typography>
              <Typography sx={{ typography: "s3", color: "text.tertiary" }}>
                {k.blurb}
              </Typography>
            </MenuItem>
          ))}
        </TextField>
        <TextField
          size="small"
          label="Traits"
          value={form.traits}
          onChange={set("traits")}
          fullWidth
          helperText="Comma separated"
        />
        <Box>
          <Label>Modalities</Label>
          <Stack direction="row" spacing={0.75}>
            {MODALITIES.map((m) => {
              const on = form.modalities.includes(m);
              return (
                <Chip
                  key={m}
                  size="small"
                  label={m}
                  onClick={() => toggle("modalities", m)}
                  sx={{
                    color: on ? "primary.main" : "text.tertiary",
                    border: "1px solid",
                    borderColor: on ? "primary.main" : "divider",
                    bgcolor: (t) =>
                      on ? alpha(t.palette.primary.main, 0.08) : "transparent",
                  }}
                />
              );
            })}
          </Stack>
        </Box>

        {/* Saved and editable, but a run doesn't read them yet. */}
        <Box>
          <Button
            size="small"
            variant="outlined"
            onClick={() => setMore((o) => !o)}
            endIcon={
              <Iconify
                icon={
                  more
                    ? "solar:alt-arrow-up-linear"
                    : "solar:alt-arrow-down-linear"
                }
                width={14}
              />
            }
            sx={{ color: "text.primary", borderColor: "divider" }}
          >
            More details (not used by runs yet)
          </Button>
          <Collapse in={more} unmountOnExit>
            <Stack spacing={2} sx={{ pt: 2 }}>
              <TextField
                size="small"
                label="What they want"
                value={form.goal}
                onChange={set("goal")}
                fullWidth
                multiline
                minRows={2}
                helperText="Only if they want something besides the scenario's task"
              />
              <TextField
                size="small"
                label="May agree to"
                value={form.agreesTo}
                onChange={set("agreesTo")}
                fullWidth
              />
              <TextField
                size="small"
                label="Keeps to themselves"
                value={form.keepsPrivate}
                onChange={set("keepsPrivate")}
                fullWidth
              />
              <TextField
                size="small"
                label="Hands over when"
                value={form.handsOverWhen}
                onChange={set("handsOverWhen")}
                fullWidth
              />
              <TextField
                size="small"
                label="Is done when"
                value={form.doneWhen}
                onChange={set("doneWhen")}
                fullWidth
              />
              <TextField
                select
                size="small"
                label="How they behave"
                value={form.behaviour}
                onChange={set("behaviour")}
                fullWidth
              >
                {BEHAVIOURS.map((b) => (
                  <MenuItem key={b.id} value={b.id} sx={{ typography: "s2" }}>
                    {b.label}
                  </MenuItem>
                ))}
              </TextField>
              {!caller && (
                <TextField
                  select
                  size="small"
                  label="Brought in by"
                  value={form.activatedBy}
                  onChange={set("activatedBy")}
                  fullWidth
                >
                  <MenuItem value="" sx={{ typography: "s2" }}>
                    No one — they enter as set above
                  </MenuItem>
                  {others.map((a) => (
                    <MenuItem key={a.id} value={a.id} sx={{ typography: "s2" }}>
                      {a.name}
                    </MenuItem>
                  ))}
                </TextField>
              )}
            </Stack>
          </Collapse>
        </Box>
      </Stack>

      <Stack
        direction="row"
        alignItems="center"
        spacing={1.5}
        sx={{ px: 2.5, py: 2, borderTop: "1px solid", borderColor: "divider" }}
      >
        <CopyCommandButton command={actorCli.set(env, fields)} />
        <Typography
          noWrap
          sx={{ typography: "s3", color: "text.tertiary", flex: 1 }}
        >
          {footnote}
        </Typography>
        <Button size="small" onClick={onClose} sx={{ color: "text.secondary" }}>
          Cancel
        </Button>
        <Button
          size="small"
          variant="contained"
          color="primary"
          disabled={existing ? !dirty || !valid : !valid}
          onClick={() => onSave(actor, fields)}
        >
          {existing ? "Save to draft" : "Add actor"}
        </Button>
      </Stack>
    </Stack>
  );
}
ActorForm.propTypes = {
  env: PropTypes.object,
  envState: PropTypes.object,
  cast: PropTypes.array,
  takenIds: PropTypes.array,
  actor: PropTypes.object,
  existing: PropTypes.bool,
  onClose: PropTypes.func,
  onSave: PropTypes.func,
};
