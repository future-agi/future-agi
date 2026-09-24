import PropTypes from "prop-types";
import { useMemo, useState } from "react";
import { alpha } from "@mui/material/styles";
import {
  Box, Stack, Typography, Button, IconButton, Tooltip, Collapse, TextField, MenuItem, Chip,
} from "@mui/material";
import Iconify from "src/components/iconify";
import CustomTooltip from "src/components/tooltip";
import {
  ACTOR_LIBRARY, ENTRY_KINDS, PRESSURE_KINDS, MODALITIES,
  castFor, getPressure, getEntry,
} from "src/api/simulate-environments/_fixtures/actors";
import SideDrawer from "../../components/SideDrawer";
import SectionCard from "../../components/SectionCard";
import EmptyState from "../../components/EmptyState";
import { Label } from "./ContractPart";
import { CONTRACT_COPY } from "./contract.constants";

const C = CONTRACT_COPY.actors;
const LOCK_TOOLTIP = "Fork this environment to edit.";

/**
 * Actors — the contract's third-party slot. A persona is who the agent is
 * serving; an actor is someone else in the world who wants something different.
 * Every row leads with the actor's goal, because the goal is the whole
 * mechanism, and states the pressure kind once — on the role chip.
 *
 * Reads the cast from the environment (an explicit `envState.actors` id list, or
 * the modality-fit default from `castFor`). With a `patch` channel it also
 * offers the create / edit / remove flow (the editor is a prototype form —
 * removal persists via patch; the library of injectable pre-builts is not shown
 * here). A template-locked env renders everything read-only.
 */
export default function ActorsPanel({ env, envState, patch, onGo, locked = false }) {
  const [editing, setEditing] = useState(null);
  const cast = envState?.actors || castFor(env);
  const canEdit = !!patch && !locked;

  const inCast = useMemo(() => {
    const set = new Set(cast);
    return ACTOR_LIBRARY.filter((a) => set.has(a.id));
  }, [cast]);

  const drop = (id) => patch?.({ actors: cast.filter((x) => x !== id) });

  return (
    <Box>
      <Stack direction={{ xs: "column", sm: "row" }} alignItems={{ sm: "flex-end" }} spacing={2} sx={{ mb: 2 }}>
        <Box flex={1}>
          <Typography sx={{ typography: "m2", fontWeight: "fontWeightSemiBold" }}>{C.heading}</Typography>
          <Typography sx={{ typography: "s2", color: "text.secondary", maxWidth: 780 }}>
            {C.headingBlurb}
          </Typography>
        </Box>
        {patch && (
          <MaybeLocked locked={locked}>
            <Button
              variant="contained" color="primary" size="small"
              disabled={locked}
              onClick={() =>
                setEditing({ entry: "present", pressure: "competing", name: "", goal: "", blurb: "", traits: [], modalities: [env.surface] })
              }
              startIcon={<Iconify icon="solar:add-circle-linear" width={15} />}
              sx={{ typography: "s2", fontWeight: "fontWeightBold", flexShrink: 0 }}
            >
              {C.create}
            </Button>
          </MaybeLocked>
        )}
      </Stack>

      <Box
        sx={{
          p: 1.75, mb: 2, borderRadius: 1.25, border: "1px solid", borderColor: "divider",
          bgcolor: "background.neutral",
        }}
      >
        <Stack direction="row" spacing={1.25} alignItems="flex-start">
          <Iconify icon="solar:lightbulb-linear" width={16} sx={{ color: "primary.main", flexShrink: 0, mt: "1px" }} />
          <Typography sx={{ typography: "s2", color: "text.secondary" }}>
            <Box component="span" sx={{ fontWeight: "fontWeightBold", color: "text.primary" }}>{C.calloutLead}</Box>{" "}
            {C.calloutBody}{" "}
            <Box
              component="span"
              onClick={() => onGo?.("scenarios")}
              sx={{ color: "primary.main", fontWeight: "fontWeightBold", cursor: "pointer" }}
            >
              {C.calloutLink}
            </Box>
          </Typography>
        </Stack>
      </Box>

      <SectionCard title={C.castTitle(inCast.length)} subtitle={C.castSubtitle}>
        {inCast.length === 0 ? (
          <EmptyState icon={C.emptyIcon} title={C.emptyTitle} body={C.emptyBody} />
        ) : (
          <Stack divider={<Box sx={{ borderBottom: "1px solid", borderColor: "divider" }} />}>
            {inCast.map((a) => (
              <ActorRow
                key={a.id}
                actor={a}
                onEdit={canEdit ? () => setEditing(a) : undefined}
                action={
                  canEdit ? (
                    <Tooltip arrow title="Remove from this environment">
                      <IconButton size="small" onClick={() => drop(a.id)}>
                        <Iconify icon="solar:trash-bin-trash-linear" width={16} sx={{ color: "text.subtitle" }} />
                      </IconButton>
                    </Tooltip>
                  ) : null
                }
              />
            ))}
          </Stack>
        )}
      </SectionCard>

      {canEdit && <ActorEditor actor={editing} onClose={() => setEditing(null)} />}
    </Box>
  );
}

const ACTOR_SHAPE = PropTypes.shape({
  id: PropTypes.string.isRequired,
  name: PropTypes.string,
  goal: PropTypes.string,
  blurb: PropTypes.string,
  tests: PropTypes.string,
  entry: PropTypes.string,
  pressure: PropTypes.string,
  modalities: PropTypes.arrayOf(PropTypes.string),
  traits: PropTypes.arrayOf(PropTypes.string),
  version: PropTypes.string,
  versions: PropTypes.arrayOf(PropTypes.shape({ label: PropTypes.string, note: PropTypes.string })),
  usedBy: PropTypes.number,
  owner: PropTypes.string,
});

ActorsPanel.propTypes = {
  env: PropTypes.shape({ surface: PropTypes.string }).isRequired,
  envState: PropTypes.shape({ actors: PropTypes.arrayOf(PropTypes.string) }).isRequired,
  patch: PropTypes.func,
  onGo: PropTypes.func,
  locked: PropTypes.bool,
};

// Wraps a disabled control in the "Fork this environment to edit." tooltip when
// the env is a locked template; otherwise renders the child untouched.
function MaybeLocked({ locked, children }) {
  if (!locked) return children;
  return (
    <CustomTooltip show size="small" title={LOCK_TOOLTIP} arrow>
      <span>{children}</span>
    </CustomTooltip>
  );
}
MaybeLocked.propTypes = { locked: PropTypes.bool, children: PropTypes.node };

function ActorRow({ actor, action, onEdit }) {
  const [open, setOpen] = useState(false);
  const pressure = getPressure(actor.pressure);
  const entry = getEntry(actor.entry);
  const toggle = () => setOpen((o) => !o);

  return (
    <Box>
      <Stack direction="row" alignItems="center" spacing={2} sx={{ px: 2.5, py: 1.5 }}>
        <Tooltip arrow title={pressure.blurb}>
          <Box
            onClick={toggle}
            sx={{
              width: 30, height: 30, borderRadius: 0.875, flexShrink: 0, cursor: "pointer",
              display: "grid", placeItems: "center", color: "text.subtitle", bgcolor: "background.neutral",
            }}
          >
            <Iconify icon="solar:users-group-two-rounded-linear" width={16} />
          </Box>
        </Tooltip>

        <Box flex={1} minWidth={0} onClick={toggle} sx={{ cursor: "pointer" }}>
          <Stack direction="row" alignItems="center" spacing={0.75}>
            <Typography noWrap sx={{ typography: "s2", fontWeight: "fontWeightSemiBold" }}>{actor.name}</Typography>
            <Typography sx={{ typography: "s3", color: "text.subtitle", flexShrink: 0 }}>· {actor.version}</Typography>
            {actor.owner === "system" && (
              <Typography sx={{ typography: "s3", color: "text.subtitle", flexShrink: 0 }}>· {C.builtIn}</Typography>
            )}
          </Stack>
          <Typography noWrap sx={{ typography: "s3", color: "text.secondary" }}>
            <Box component="span" sx={{ color: "text.primary", fontWeight: "fontWeightBold" }}>{C.wants}</Box> {actor.goal}
          </Typography>
        </Box>

        <Chip
          size="small"
          label={pressure.label}
          sx={{
            height: 20, borderRadius: 0.75, flexShrink: 0, color: pressure.color,
            border: "1px solid", borderColor: alpha(pressure.color, 0.4), bgcolor: "transparent",
            display: { xs: "none", md: "flex" },
            "& .MuiChip-label": { px: 0.75, typography: "s3", fontWeight: "fontWeightSemiBold" },
          }}
        />

        <Stack direction="row" spacing={0.5} sx={{ flexShrink: 0, display: { xs: "none", md: "flex" } }}>
          {(actor.modalities || []).map((m) => (
            <ModalityChip key={m} label={m} />
          ))}
        </Stack>

        <Typography sx={{ typography: "s3", color: "text.subtitle", flexShrink: 0, display: { xs: "none", lg: "block" }, width: 118 }}>
          {entry.label.toLowerCase()}
        </Typography>
        <Typography sx={{ typography: "s3", color: "text.subtitle", flexShrink: 0, display: { xs: "none", sm: "block" } }}>
          {C.usedBy(actor.usedBy)}
        </Typography>

        {onEdit && (
          <Tooltip arrow title="Edit — saving creates a new version">
            <IconButton size="small" onClick={onEdit} sx={{ flexShrink: 0 }}>
              <Iconify icon="solar:pen-new-square-linear" width={15} sx={{ color: "text.subtitle" }} />
            </IconButton>
          </Tooltip>
        )}
        {action && <Box sx={{ flexShrink: 0 }}>{action}</Box>}

        <Tooltip arrow title={open ? "Collapse" : "Expand"}>
          <IconButton size="small" onClick={toggle} sx={{ flexShrink: 0 }}>
            <Iconify icon={open ? "solar:alt-arrow-up-linear" : "solar:alt-arrow-down-linear"} width={16} sx={{ color: "text.subtitle" }} />
          </IconButton>
        </Tooltip>
      </Stack>

      <Collapse in={open} unmountOnExit>
        <Stack spacing={1.5} sx={{ px: 2.5, pb: 2, pl: 7 }}>
          <DetailBlock label={C.detail.does} body={actor.blurb} />
          <DetailBlock label={C.detail.tests} body={actor.tests} />
          <DetailBlock label={C.detail.entry} body={`${entry.label} — ${entry.blurb}`} />
          <Box>
            <Label>{C.detail.modalities}</Label>
            <Stack direction="row" spacing={0.75} flexWrap="wrap" rowGap={0.75}>
              {(actor.modalities || []).map((m) => (
                <ModalityChip key={m} label={m} />
              ))}
            </Stack>
          </Box>
          <Box>
            <Label>{C.detail.traits}</Label>
            <Stack direction="row" spacing={0.75} flexWrap="wrap" rowGap={0.75}>
              {(actor.traits || []).map((t) => (
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
            <Label>{C.detail.versions}</Label>
            <Stack spacing={0.75}>
              {(actor.versions || []).map((v) => (
                <Stack key={v.label} direction="row" spacing={1.5} alignItems="flex-start">
                  <Typography sx={{ width: 28, flexShrink: 0, typography: "s2", fontWeight: "fontWeightBold" }}>{v.label}</Typography>
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
ActorRow.propTypes = { actor: ACTOR_SHAPE, action: PropTypes.node, onEdit: PropTypes.func };

function ModalityChip({ label }) {
  return (
    <Chip
      size="small" label={label}
      sx={{
        height: 20, borderRadius: 0.75, color: "text.subtitle",
        border: "1px solid", borderColor: "divider", bgcolor: "transparent",
        "& .MuiChip-label": { px: 0.75, typography: "s3", fontWeight: "fontWeightMedium" },
      }}
    />
  );
}
ModalityChip.propTypes = { label: PropTypes.string };

function DetailBlock({ label, body }) {
  return (
    <Box>
      <Label>{label}</Label>
      <Typography sx={{ typography: "s2", color: "text.secondary" }}>{body}</Typography>
    </Box>
  );
}
DetailBlock.propTypes = { label: PropTypes.node, body: PropTypes.node };

// Create / edit an actor. A prototype form: fields are pre-filled from the actor
// and the footer action closes the drawer — removal is the persisted mutation,
// authoring a bespoke actor lands when the actor library is wired.
function ActorEditor({ actor, onClose }) {
  const existing = !!actor?.id;
  return (
    <SideDrawer open={!!actor} onClose={onClose} width={{ xs: "100%", sm: 480 }}>
      {actor && (
        <Stack sx={{ height: "100%", minHeight: 0 }}>
          {/* No close button here — SideDrawer renders the single (outlined)
              close in the corner. pr clears it so a long name doesn't collide. */}
          <Box sx={{ pl: 2.5, pr: 6, py: 2, borderBottom: "1px solid", borderColor: "divider", flexShrink: 0 }}>
            <Typography sx={{ typography: "s1", fontWeight: "fontWeightBold" }}>{existing ? actor.name : "Create actor"}</Typography>
            <Typography sx={{ typography: "s3", color: "text.subtitle" }}>
              {existing ? `Editing ${actor.version} — saving creates a new version` : "Available to every environment once saved"}
            </Typography>
          </Box>

          <Stack spacing={2.25} sx={{ p: 2.5, flex: 1, minHeight: 0, overflowY: "auto" }}>
            <TextField size="small" label="Name" defaultValue={actor.name} fullWidth />
            <TextField
              size="small" label="What they want" defaultValue={actor.goal} fullWidth multiline minRows={2}
              helperText="The goal, and it must not be the task's goal — that is what makes this an actor."
            />
            <TextField size="small" label="What they do" defaultValue={actor.blurb} fullWidth multiline minRows={3} />

            <TextField select size="small" label="Pressure" defaultValue={actor.pressure} fullWidth>
              {PRESSURE_KINDS.map((k) => (
                <MenuItem key={k.id} value={k.id} sx={{ display: "block" }}>
                  <Typography sx={{ typography: "s2", fontWeight: "fontWeightSemiBold" }}>{k.label}</Typography>
                  <Typography sx={{ typography: "s3", color: "text.subtitle" }}>{k.blurb}</Typography>
                </MenuItem>
              ))}
            </TextField>

            <TextField select size="small" label="When they enter" defaultValue={actor.entry} fullWidth>
              {ENTRY_KINDS.map((k) => (
                <MenuItem key={k.id} value={k.id} sx={{ display: "block" }}>
                  <Typography sx={{ typography: "s2", fontWeight: "fontWeightSemiBold" }}>{k.label}</Typography>
                  <Typography sx={{ typography: "s3", color: "text.subtitle" }}>{k.blurb}</Typography>
                </MenuItem>
              ))}
            </TextField>

            <TextField
              size="small" label="Traits" defaultValue={(actor.traits || []).join(", ")} fullWidth
              helperText="Comma separated — decoration on top of the goal, not a substitute for it"
            />

            <Box>
              <Label>Modalities</Label>
              <Stack direction="row" spacing={0.75} flexWrap="wrap" rowGap={0.75}>
                {MODALITIES.map((m) => {
                  const on = actor.modalities?.includes(m);
                  return (
                    <Chip
                      key={m} size="small" label={m}
                      sx={{
                        height: 24, borderRadius: 0.75,
                        color: on ? "primary.main" : "text.subtitle",
                        border: "1px solid", borderColor: on ? "primary.main" : "divider",
                        bgcolor: (t) => (on ? alpha(t.palette.primary.main, 0.08) : "transparent"),
                        "& .MuiChip-label": { px: 1, typography: "s3", fontWeight: "fontWeightSemiBold" },
                      }}
                    />
                  );
                })}
              </Stack>
            </Box>
          </Stack>

          <Stack direction="row" spacing={1.5} sx={{ px: 2.5, py: 2, borderTop: "1px solid", borderColor: "divider", flexShrink: 0 }}>
            <Box flex={1} />
            <Button onClick={onClose} sx={{ typography: "s2", fontWeight: "fontWeightSemiBold", color: "text.secondary" }}>Cancel</Button>
            <Button variant="contained" color="primary" onClick={onClose} sx={{ typography: "s2", fontWeight: "fontWeightBold" }}>
              {existing ? "Save as new version" : "Create actor"}
            </Button>
          </Stack>
        </Stack>
      )}
    </SideDrawer>
  );
}
ActorEditor.propTypes = { actor: PropTypes.object, onClose: PropTypes.func };
