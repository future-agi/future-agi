import PropTypes from "prop-types";
import { useMemo, useState } from "react";
import { useNavigate } from "react-router-dom";
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
} from "@mui/material";
import SideDrawer from "../components/SideDrawer";
import { textTone } from "../components/textTone";
import Iconify from "src/components/iconify";
import { paths } from "src/routes/paths";
import { envChangePatch } from "../_mock/versions";
import {
  ACTOR_LIBRARY,
  ENTRY_KINDS,
  PRESSURE_KINDS,
  MODALITIES,
  actorsOf,
  castOf,
  getPressure,
  getEntry,
  actorEditPatch,
  resetActorPatch,
} from "../_mock/actors";
import { SectionCard, EmptyState } from "../components/primitives";
import { clickable, focusRing } from "../components/clickable";

/**
 * Actors.
 *
 * The distinction this screen has to earn in one sentence: a persona is who
 * the agent is serving, an actor is someone else in the world who wants
 * something different. You are booking a cab; your colleague wants pizza.
 *
 * So every row leads with the actor's *goal*, not its personality — the goal
 * is the whole mechanism. Traits are decoration on top of it.
 *
 * Colour states the pressure kind once, on the chip that names it. The row
 * used to say it three times — a filled tile, the "Wants:" label and the chip
 * — so seven actors meant twenty-one coloured marks in five hues carrying
 * seven facts. The tile is neutral and "Wants:" is plain text; the chip keeps
 * its colour because it is the only one of the three that says what the
 * colour means.
 */
export default function ActorsPanel({ env, envState, patch, locked = false }) {
  const navigate = useNavigate();
  const [editing, setEditing] = useState(null);
  const cast = actorsOf(env, envState);

  /*
    Only the actors already in the environment render — the "Library"
    of injectable pre-built actors was removed from this tab. If we
    bring library-style discovery back later, it belongs on a
    dedicated screen (like the Personas library), not as a second
    always-visible list below the environment's real cast.
  */
  /* As this environment has them — its edits layered over the library. */
  const inCast = useMemo(() => castOf(env, envState), [env, envState]);

  const drop = (id) =>
    patch({
      actors: cast.filter((x) => x !== id),
      ...envChangePatch(
        envState,
        "actors",
        `Removed ${(inCast.find((a) => a.id === id) || ACTOR_LIBRARY.find((a) => a.id === id))?.name || "an actor"}`,
      ),
    });
  /* Saving an actor in this cast changes how its episodes play out here —
     kept on the environment, so the next version and every run read it. */
  const saved = (actor, fields) => {
    if (actor?.id && cast.includes(actor.id))
      patch(actorEditPatch(envState, actor.id, fields));
    setEditing(null);
  };
  const reset = (id) => patch(resetActorPatch(envState, id));

  return (
    <Box sx={{ p: 2 }}>
      <Stack
        direction={{ xs: "column", sm: "row" }}
        alignItems={{ sm: "flex-end" }}
        spacing={2}
        sx={{ mb: 2 }}
      >
        {/* No heading — the Actors tab names the page. */}
        <Box flex={1}>
          <Typography
            sx={{ typography: "s2", color: "text.secondary", maxWidth: 780 }}
          >
            Other parties in the world, each with a goal that pulls the
            conversation off the task.
          </Typography>
        </Box>
        {!locked && (
          <Button
            variant="contained"
            color="primary"
            size="small"
            onClick={() =>
              setEditing({
                entry: "present",
                pressure: "competing",
                name: "",
                goal: "",
                blurb: "",
                traits: [],
                modalities: [env.surface],
              })
            }
            startIcon={<Iconify icon="solar:add-circle-linear" width={16} />}
            sx={{ flexShrink: 0 }}
          >
            Create actor
          </Button>
        )}
      </Stack>

      {/* The one thing a reader has to leave with. */}
      <Box
        sx={{
          p: 1.75,
          mb: 2,
          borderRadius: "8px",
          border: "1px solid",
          borderColor: "divider",
          bgcolor: "background.neutral",
        }}
      >
        <Stack direction="row" spacing={1.25} alignItems="flex-start">
          <Iconify
            icon="solar:lightbulb-linear"
            width={16}
            sx={{ color: "primary.main", flexShrink: 0, mt: "1px" }}
          />
          <Typography sx={{ typography: "s2", color: "text.secondary" }}>
            <Box
              component="span"
              sx={{ fontWeight: 600, color: "text.primary" }}
            >
              Not the same as a persona.
            </Box>{" "}
            The persona is who your agent serves; an actor is someone else — the
            colleague who wants pizza instead.{" "}
            <Box
              component="span"
              {...clickable(
                () =>
                  navigate(
                    `${paths.dashboard.simulate.environments}?tab=personas`,
                  ),
                { role: "link" },
              )}
              sx={{
                ...focusRing,
                color: "primary.main",
                fontWeight: 600,
                cursor: "pointer",
              }}
            >
              See personas
            </Box>
          </Typography>
        </Stack>
      </Box>

      <SectionCard
        title={`In this environment (${inCast.length})`}
        subtitle="Injected into every run, at the version pinned here"
        sx={{ mb: 2 }}
      >
        {inCast.length === 0 ? (
          <EmptyState
            icon="solar:users-group-two-rounded-linear"
            title="No actors yet"
            body="Without one, every run is a clean two-party conversation — which is rarely what happens in the wild."
          />
        ) : (
          <Stack
            divider={
              <Box sx={{ borderBottom: "1px solid", borderColor: "divider" }} />
            }
          >
            {inCast.map((a) => (
              <ActorRow
                key={a.id}
                actor={a}
                onEdit={locked ? null : () => setEditing(a)}
                onReset={locked || !a.edited ? null : () => reset(a.id)}
                action={
                  !locked && (
                    <Tooltip arrow title="Remove from this environment">
                      <IconButton size="small" onClick={() => drop(a.id)}>
                        <Iconify
                          icon="solar:trash-bin-trash-linear"
                          width={16}
                          sx={{ color: "text.tertiary" }}
                        />
                      </IconButton>
                    </Tooltip>
                  )
                }
              />
            ))}
          </Stack>
        )}
      </SectionCard>

      <ActorEditor
        actor={editing}
        onClose={() => setEditing(null)}
        onSave={saved}
      />
    </Box>
  );
}

ActorsPanel.propTypes = {
  env: PropTypes.object.isRequired,
  envState: PropTypes.object.isRequired,
  patch: PropTypes.func.isRequired,
  locked: PropTypes.bool,
};

/* ── one actor ───────────────────────────────────────────────────────────── */

function ActorRow({ actor, action, onEdit, onReset }) {
  const [open, setOpen] = useState(false);
  const pressure = getPressure(actor.pressure);
  const entry = getEntry(actor.entry);

  return (
    <Box>
      <Stack
        direction="row"
        alignItems="center"
        spacing={1.5}
        sx={{ px: 2.5, py: 1 }}
      >
        <Tooltip arrow title={pressure.blurb}>
          <Box
            onClick={() => setOpen((o) => !o)}
            sx={{
              width: 28,
              height: 28,
              borderRadius: "6px",
              flexShrink: 0,
              cursor: "pointer",
              display: "grid",
              placeItems: "center",
              color: "text.tertiary",
              bgcolor: "background.neutral",
            }}
          >
            <Iconify icon="solar:users-group-two-rounded-linear" width={16} />
          </Box>
        </Tooltip>

        <Box
          flex={1}
          minWidth={0}
          {...clickable(() => setOpen((o) => !o), { expanded: open })}
          sx={{ ...focusRing, cursor: "pointer" }}
        >
          <Stack direction="row" alignItems="center" spacing={0.75}>
            <Typography noWrap sx={{ typography: "s2_1", fontWeight: 500 }}>
              {actor.name}
            </Typography>
            <Typography
              sx={{ typography: "s3", color: "text.tertiary", flexShrink: 0 }}
            >
              · {actor.version}
            </Typography>
            {actor.owner === "system" && (
              <Typography
                sx={{ typography: "s3", color: "text.tertiary", flexShrink: 0 }}
              >
                · built in
              </Typography>
            )}
            {actor.edited && (
              <Tooltip
                arrow
                title={`Edited in this environment${actor.library?.name && actor.library.name !== actor.name ? ` — library name "${actor.library.name}"` : ""}. Other environments use the library ${actor.version}.`}
              >
                <Typography
                  sx={{
                    typography: "s3",
                    color: "text.secondary",
                    fontWeight: 600,
                    flexShrink: 0,
                  }}
                >
                  · edited
                </Typography>
              </Tooltip>
            )}
            {onReset && (
              <Typography
                role="button"
                tabIndex={0}
                onClick={(e) => {
                  e.stopPropagation();
                  onReset();
                }}
                onKeyDown={(e) => {
                  if (e.key === "Enter") {
                    e.stopPropagation();
                    onReset();
                  }
                }}
                sx={{
                  typography: "s3",
                  color: "primary.main",
                  fontWeight: 600,
                  cursor: "pointer",
                  flexShrink: 0,
                }}
              >
                Reset
              </Typography>
            )}
          </Stack>
          {/* The goal leads, because the goal is the mechanism. */}
          <Typography noWrap sx={{ typography: "s3", color: "text.secondary" }}>
            <Box
              component="span"
              sx={{ color: "text.primary", fontWeight: 600 }}
            >
              Wants:
            </Box>{" "}
            {actor.goal}
          </Typography>
        </Box>

        <Chip
          size="small"
          label={pressure.label}
          sx={{
            flexShrink: 0,
            color: textTone(pressure.color, "text.secondary"),
            border: "1px solid",
            borderColor: alpha(pressure.color, 0.4),
            bgcolor: "transparent",
            display: { xs: "none", md: "flex" },
          }}
        />
        <Typography
          sx={{
            typography: "s3",
            color: "text.tertiary",
            flexShrink: 0,
            display: { xs: "none", lg: "block" },
            width: 118,
          }}
        >
          {entry.label.toLowerCase()}
        </Typography>
        <Typography
          sx={{
            typography: "s3",
            color: "text.tertiary",
            flexShrink: 0,
            display: { xs: "none", sm: "block" },
          }}
        >
          used by {actor.usedBy}
        </Typography>

        {onEdit && (
          <Tooltip arrow title="Edit — saving creates a new version">
            <IconButton size="small" onClick={onEdit} sx={{ flexShrink: 0 }}>
              <Iconify
                icon="solar:pen-new-square-linear"
                width={16}
                sx={{ color: "text.tertiary" }}
              />
            </IconButton>
          </Tooltip>
        )}
        <Box sx={{ flexShrink: 0 }}>{action}</Box>
      </Stack>

      <Collapse in={open} unmountOnExit>
        <Stack spacing={1.5} sx={{ px: 2.5, pb: 2, pl: 7 }}>
          <Box>
            <Label>What they do</Label>
            <Typography sx={{ typography: "s2", color: "text.secondary" }}>
              {actor.blurb}
            </Typography>
          </Box>
          <Box>
            <Label>What it tests</Label>
            <Typography sx={{ typography: "s2", color: "text.secondary" }}>
              {actor.tests}
            </Typography>
          </Box>
          <Box>
            <Label>When they enter</Label>
            <Typography sx={{ typography: "s2", color: "text.secondary" }}>
              {entry.label} — {entry.blurb}
            </Typography>
          </Box>
          <Box>
            <Label>Traits</Label>
            <Stack direction="row" spacing={0.75} flexWrap="wrap" rowGap={0.75}>
              {actor.traits.map((t) => (
                <Chip
                  key={t}
                  size="small"
                  label={t}
                  sx={{
                    color: "text.secondary",
                    border: "1px solid",
                    borderColor: "divider",
                    bgcolor: "transparent",
                  }}
                />
              ))}
            </Stack>
          </Box>
          <Box>
            <Label>Versions</Label>
            <Stack spacing={0.75}>
              {actor.versions.map((v) => (
                <Stack
                  key={v.label}
                  direction="row"
                  spacing={1.5}
                  alignItems="flex-start"
                >
                  <Typography
                    sx={{
                      width: 28,
                      flexShrink: 0,
                      typography: "s2",
                      fontWeight: 600,
                    }}
                  >
                    {v.label}
                  </Typography>
                  <Typography
                    sx={{ typography: "s2", color: "text.secondary" }}
                  >
                    {v.note}
                  </Typography>
                </Stack>
              ))}
            </Stack>
          </Box>
        </Stack>
      </Collapse>
    </Box>
  );
}
ActorRow.propTypes = {
  actor: PropTypes.object,
  action: PropTypes.node,
  onEdit: PropTypes.func,
  onReset: PropTypes.func,
};

function Label({ children }) {
  return (
    <Typography
      sx={{
        typography: "s3",
        fontWeight: 600,
        color: "text.tertiary",
        textTransform: "none",
        letterSpacing: 0.4,
        mb: 0.75,
      }}
    >
      {children}
    </Typography>
  );
}
Label.propTypes = { children: PropTypes.node };

/* ── create / edit ───────────────────────────────────────────────────────── */

const formOf = (actor) => ({
  name: actor?.name || "",
  goal: actor?.goal || "",
  blurb: actor?.blurb || "",
  pressure: actor?.pressure || "competing",
  entry: actor?.entry || "present",
  traits: (actor?.traits || []).join(", "),
  modalities: actor?.modalities || [],
});

function ActorEditor({ actor, onClose, onSave }) {
  const existing = !!actor?.id;
  return (
    <SideDrawer open={!!actor} onClose={onClose} width={480}>
      {/* Keyed so reopening starts from what the environment has now. */}
      {actor && (
        <ActorForm
          key={`${actor.id || "new"}-${actor.editedAt || ""}`}
          actor={actor}
          existing={existing}
          onClose={onClose}
          onSave={onSave}
        />
      )}
    </SideDrawer>
  );
}
ActorEditor.propTypes = {
  actor: PropTypes.object,
  onClose: PropTypes.func,
  onSave: PropTypes.func,
};

function ActorForm({ actor, existing, onClose, onSave }) {
  const [form, setForm] = useState(() => formOf(actor));
  const set = (k) => (e) => setForm((f) => ({ ...f, [k]: e.target.value }));
  const toggleModality = (m) =>
    setForm((f) => ({
      ...f,
      modalities: f.modalities.includes(m)
        ? f.modalities.filter((x) => x !== m)
        : [...f.modalities, m],
    }));
  const fields = {
    ...form,
    name: form.name.trim(),
    goal: form.goal.trim(),
    blurb: form.blurb.trim(),
    traits: form.traits
      .split(",")
      .map((t) => t.trim())
      .filter(Boolean),
  };
  const before = formOf(actor);
  const dirty = JSON.stringify(form) !== JSON.stringify(before);
  const valid = !!fields.name && !!fields.goal;

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
        <Box flex={1}>
          <Typography sx={{ typography: "m2", fontWeight: 600 }}>
            {existing ? actor.name : "Create actor"}
          </Typography>
          <Typography sx={{ typography: "s3", color: "text.tertiary" }}>
            {existing
              ? `${actor.version}${actor.edited ? " · edited here" : ""} — saved for this environment only; the library keeps ${actor.version}`
              : "Available to every environment once saved"}
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
        <TextField
          size="small"
          label="Name"
          value={form.name}
          onChange={set("name")}
          fullWidth
        />
        <TextField
          size="small"
          label="What they want"
          value={form.goal}
          onChange={set("goal")}
          fullWidth
          multiline
          minRows={2}
          helperText="The goal, and it must not be the task's goal — that is what makes this an actor."
        />
        <TextField
          size="small"
          label="What they do"
          value={form.blurb}
          onChange={set("blurb")}
          fullWidth
          multiline
          minRows={3}
        />

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

        <TextField
          size="small"
          label="Traits"
          value={form.traits}
          onChange={set("traits")}
          fullWidth
          helperText="Comma separated — decoration on top of the goal, not a substitute for it"
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
                  onClick={() => toggleModality(m)}
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
      </Stack>

      <Stack
        direction="row"
        alignItems="center"
        spacing={1.5}
        sx={{ px: 2.5, py: 2, borderTop: "1px solid", borderColor: "divider" }}
      >
        <Typography sx={{ typography: "s3", color: "text.tertiary", flex: 1 }}>
          {existing && dirty
            ? "Recorded as an actors change on the next environment version"
            : ""}
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
          {existing ? "Save for this environment" : "Create actor"}
        </Button>
      </Stack>
    </Stack>
  );
}
ActorForm.propTypes = {
  actor: PropTypes.object,
  existing: PropTypes.bool,
  onClose: PropTypes.func,
  onSave: PropTypes.func,
};
