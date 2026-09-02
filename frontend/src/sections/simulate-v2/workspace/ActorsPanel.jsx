import PropTypes from "prop-types";
import { useMemo, useState } from "react";
import { alpha } from "@mui/material/styles";
import {
  Box, Stack, Typography, Button, IconButton, Tooltip, Collapse,
  TextField, MenuItem, Chip,
} from "@mui/material";
import SideDrawer from "../components/SideDrawer";
import Iconify from "src/components/iconify";
import {
  ACTOR_LIBRARY, ENTRY_KINDS, PRESSURE_KINDS, MODALITIES,
  castFor, getPressure, getEntry,
} from "../_mock/actors";
import { SectionCard, EmptyState } from "../components/primitives";

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
export default function ActorsPanel({ env, envState, patch, onGo }) {
  const [editing, setEditing] = useState(null);
  const cast = envState.actors || castFor(env);

  /*
    Only the actors already in the environment render — the "Library"
    of injectable pre-built actors was removed from this tab. If we
    bring library-style discovery back later, it belongs on a
    dedicated screen (like the Personas library), not as a second
    always-visible list below the environment's real cast.
  */
  const inCast = useMemo(() => {
    const set = new Set(cast);
    return ACTOR_LIBRARY.filter((a) => set.has(a.id));
  }, [cast]);

  const drop = (id) => patch({ actors: cast.filter((x) => x !== id) });

  return (
    <Box sx={{ p: 2 }}>
      <Stack direction={{ xs: "column", sm: "row" }} alignItems={{ sm: "flex-end" }} spacing={2} sx={{ mb: 2 }}>
        <Box flex={1}>
          <Typography sx={{ typography: "m2", fontWeight: 600 }}>Actors</Typography>
          <Typography sx={{ typography: "s2", color: "text.secondary", maxWidth: 780 }}>
            Other parties in the world, each with a goal that is not the task. They pull the
            episode off-course, which is what makes them part of the environment&apos;s dynamics
            rather than part of the task.
          </Typography>
        </Box>
        <Button
          variant="contained" color="primary" size="small"
          onClick={() => setEditing({ entry: "present", pressure: "competing", name: "", goal: "", blurb: "", traits: [], modalities: [env.surface] })}
          startIcon={<Iconify icon="solar:add-circle-linear" width={15} />}
          sx={{ typography: "s2", fontWeight: 700, flexShrink: 0 }}
        >
          Create actor
        </Button>
      </Stack>

      {/* The one thing a reader has to leave with. */}
      <Box
        sx={{
          p: 1.75, mb: 2, borderRadius: 1.25, border: "1px solid", borderColor: "divider",
          bgcolor: "background.neutral",
        }}
      >
        <Stack direction="row" spacing={1.25} alignItems="flex-start">
          <Iconify icon="solar:lightbulb-linear" width={16} sx={{ color: "primary.main", flexShrink: 0, mt: "1px" }} />
          <Typography sx={{ typography: "s2", color: "text.secondary" }}>
            <Box component="span" sx={{ fontWeight: 700, color: "text.primary" }}>Not the same as a persona.</Box>{" "}
            The persona is who your agent is serving — the one whose goal the task is.
            An actor is someone else: you are trying to book a cab, and your colleague is
            saying let&apos;s get pizza instead.{" "}
            <Box
              component="span"
              onClick={() => onGo?.("personas")}
              sx={{ color: "primary.main", fontWeight: 700, cursor: "pointer" }}
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
          <Stack divider={<Box sx={{ borderBottom: "1px solid", borderColor: "divider" }} />}>
            {inCast.map((a) => (
              <ActorRow
                key={a.id} actor={a} onEdit={() => setEditing(a)}
                action={
                  <Tooltip arrow title="Remove from this environment">
                    <IconButton size="small" onClick={() => drop(a.id)}>
                      <Iconify icon="solar:close-circle-linear" width={16} sx={{ color: "text.subtitle" }} />
                    </IconButton>
                  </Tooltip>
                }
              />
            ))}
          </Stack>
        )}
      </SectionCard>

      <ActorEditor actor={editing} onClose={() => setEditing(null)} />
    </Box>
  );
}

ActorsPanel.propTypes = {
  env: PropTypes.object.isRequired,
  envState: PropTypes.object.isRequired,
  patch: PropTypes.func.isRequired,
  onGo: PropTypes.func,
};

/* ── one actor ───────────────────────────────────────────────────────────── */

function ActorRow({ actor, action, onEdit }) {
  const [open, setOpen] = useState(false);
  const pressure = getPressure(actor.pressure);
  const entry = getEntry(actor.entry);

  return (
    <Box>
      <Stack direction="row" alignItems="center" spacing={2} sx={{ px: 2.5, py: 1.5 }}>
        <Tooltip arrow title={pressure.blurb}>
          <Box
            onClick={() => setOpen((o) => !o)}
            sx={{
              width: 30, height: 30, borderRadius: 0.875, flexShrink: 0, cursor: "pointer",
              display: "grid", placeItems: "center",
              color: "text.subtitle", bgcolor: "background.neutral",
            }}
          >
            <Iconify icon="solar:users-group-two-rounded-linear" width={16} />
          </Box>
        </Tooltip>

        <Box flex={1} minWidth={0} onClick={() => setOpen((o) => !o)} sx={{ cursor: "pointer" }}>
          <Stack direction="row" alignItems="center" spacing={0.75}>
            <Typography noWrap sx={{ typography: "s2", fontWeight: 600 }}>{actor.name}</Typography>
            <Typography sx={{ typography: "s3", color: "text.subtitle", flexShrink: 0 }}>· {actor.version}</Typography>
            {actor.owner === "system" && (
              <Typography sx={{ typography: "s3", color: "text.subtitle", flexShrink: 0 }}>· built in</Typography>
            )}
          </Stack>
          {/* The goal leads, because the goal is the mechanism. */}
          <Typography noWrap sx={{ typography: "s3", color: "text.secondary" }}>
            <Box component="span" sx={{ color: "text.primary", fontWeight: 700 }}>Wants:</Box> {actor.goal}
          </Typography>
        </Box>

        <Chip
          size="small"
          label={pressure.label}
          sx={{
            height: 20, borderRadius: 0.75, flexShrink: 0, color: pressure.color,
            border: "1px solid", borderColor: alpha(pressure.color, 0.4), bgcolor: "transparent",
            display: { xs: "none", md: "flex" },
            "& .MuiChip-label": { px: 0.75, typography: "s3", fontWeight: 600 },
          }}
        />
        <Typography sx={{ typography: "s3", color: "text.subtitle", flexShrink: 0, display: { xs: "none", lg: "block" }, width: 118 }}>
          {entry.label.toLowerCase()}
        </Typography>
        <Typography sx={{ typography: "s3", color: "text.subtitle", flexShrink: 0, display: { xs: "none", sm: "block" } }}>
          used by {actor.usedBy}
        </Typography>

        <Tooltip arrow title="Edit — saving creates a new version">
          <IconButton size="small" onClick={onEdit} sx={{ flexShrink: 0 }}>
            <Iconify icon="solar:pen-new-square-linear" width={15} sx={{ color: "text.subtitle" }} />
          </IconButton>
        </Tooltip>
        <Box sx={{ flexShrink: 0 }}>{action}</Box>
      </Stack>

      <Collapse in={open} unmountOnExit>
        <Stack spacing={1.5} sx={{ px: 2.5, pb: 2, pl: 7 }}>
          <Box>
            <Label>What they do</Label>
            <Typography sx={{ typography: "s2", color: "text.secondary" }}>{actor.blurb}</Typography>
          </Box>
          <Box>
            <Label>What it tests</Label>
            <Typography sx={{ typography: "s2", color: "text.secondary" }}>{actor.tests}</Typography>
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
                  key={t} size="small" label={t}
                  sx={{
                    height: 20, borderRadius: 0.75, color: "text.secondary",
                    border: "1px solid", borderColor: "divider", bgcolor: "transparent",
                    "& .MuiChip-label": { px: 0.75, typography: "s3" },
                  }}
                />
              ))}
            </Stack>
          </Box>
          <Box>
            <Label>Versions</Label>
            <Stack spacing={0.75}>
              {actor.versions.map((v) => (
                <Stack key={v.label} direction="row" spacing={1.5} alignItems="flex-start">
                  <Typography sx={{ width: 28, flexShrink: 0, typography: "s2", fontWeight: 700 }}>{v.label}</Typography>
                  <Typography sx={{ typography: "s2", color: "text.secondary" }}>{v.note}</Typography>
                </Stack>
              ))}
            </Stack>
          </Box>
        </Stack>
      </Collapse>
    </Box>
  );
}
ActorRow.propTypes = { actor: PropTypes.object, action: PropTypes.node, onEdit: PropTypes.func };

function Label({ children }) {
  return (
    <Typography sx={{ typography: "s3", fontWeight: 700, color: "text.subtitle", textTransform: "uppercase", letterSpacing: .4, mb: 0.75 }}>
      {children}
    </Typography>
  );
}
Label.propTypes = { children: PropTypes.node };

/* ── create / edit ───────────────────────────────────────────────────────── */

function ActorEditor({ actor, onClose }) {
  const existing = !!actor?.id;
  return (
    <SideDrawer open={!!actor} onClose={onClose} width={480}>
      {actor && (
        <Stack sx={{ height: "100%" }}>
          <Stack direction="row" alignItems="center" spacing={2} sx={{ px: 2.5, py: 2, borderBottom: "1px solid", borderColor: "divider" }}>
            <Box flex={1}>
              <Typography sx={{ typography: "s1_2", fontWeight: 700 }}>{existing ? actor.name : "Create actor"}</Typography>
              <Typography sx={{ typography: "s3", color: "text.subtitle" }}>
                {existing ? `Editing ${actor.version} — saving creates a new version` : "Available to every environment once saved"}
              </Typography>
            </Box>
            <IconButton size="small" onClick={onClose}>
              <Iconify icon="solar:close-circle-linear" width={18} sx={{ color: "text.subtitle" }} />
            </IconButton>
          </Stack>

          <Stack spacing={2.25} sx={{ p: 2.5, flex: 1, overflowY: "auto" }}>
            <TextField size="small" label="Name" defaultValue={actor.name} fullWidth />
            <TextField
              size="small" label="What they want" defaultValue={actor.goal} fullWidth multiline minRows={2}
              helperText="The goal, and it must not be the task's goal — that is what makes this an actor."
            />
            <TextField size="small" label="What they do" defaultValue={actor.blurb} fullWidth multiline minRows={3} />

            <TextField select size="small" label="Pressure" defaultValue={actor.pressure} fullWidth>
              {PRESSURE_KINDS.map((k) => (
                <MenuItem key={k.id} value={k.id} sx={{ display: "block" }}>
                  <Typography sx={{ typography: "s2", fontWeight: 600 }}>{k.label}</Typography>
                  <Typography sx={{ typography: "s3", color: "text.subtitle" }}>{k.blurb}</Typography>
                </MenuItem>
              ))}
            </TextField>

            <TextField select size="small" label="When they enter" defaultValue={actor.entry} fullWidth>
              {ENTRY_KINDS.map((k) => (
                <MenuItem key={k.id} value={k.id} sx={{ display: "block" }}>
                  <Typography sx={{ typography: "s2", fontWeight: 600 }}>{k.label}</Typography>
                  <Typography sx={{ typography: "s3", color: "text.subtitle" }}>{k.blurb}</Typography>
                </MenuItem>
              ))}
            </TextField>

            <TextField
              size="small" label="Traits" defaultValue={actor.traits?.join(", ")} fullWidth
              helperText="Comma separated — decoration on top of the goal, not a substitute for it"
            />

            <Box>
              <Label>Modalities</Label>
              <Stack direction="row" spacing={0.75}>
                {MODALITIES.map((m) => {
                  const on = actor.modalities?.includes(m);
                  return (
                    <Chip
                      key={m} size="small" label={m}
                      sx={{
                        height: 24, borderRadius: 0.75,
                        color: on ? "primary.main" : "text.subtitle",
                        border: "1px solid", borderColor: on ? "primary.main" : "divider",
                        bgcolor: (t) => on ? alpha(t.palette.primary.main, 0.08) : "transparent",
                        "& .MuiChip-label": { px: 1, typography: "s3", fontWeight: 600 },
                      }}
                    />
                  );
                })}
              </Stack>
            </Box>
          </Stack>

          <Stack direction="row" spacing={1.5} sx={{ px: 2.5, py: 2, borderTop: "1px solid", borderColor: "divider" }}>
            <Box flex={1} />
            <Button onClick={onClose} sx={{ typography: "s2", fontWeight: 600, color: "text.secondary" }}>Cancel</Button>
            <Button variant="contained" color="primary" onClick={onClose} sx={{ typography: "s2", fontWeight: 700 }}>
              {existing ? "Save as new version" : "Create actor"}
            </Button>
          </Stack>
        </Stack>
      )}
    </SideDrawer>
  );
}
ActorEditor.propTypes = { actor: PropTypes.object, onClose: PropTypes.func };
