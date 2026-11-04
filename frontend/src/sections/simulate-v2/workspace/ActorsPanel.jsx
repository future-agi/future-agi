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
  Switch,
  FormControlLabel,
  Menu,
} from "@mui/material";
import CopyCommandButton from "../components/CopyCommandButton";
import { ConfirmDialog } from "src/components/custom-dialog";
import { useSimStore } from "../store";
import { enqueueSnackbar } from "notistack";
import { personaFor } from "../_mock/scenarios";
import { stampNewBatch } from "../_mock/addScenarios";
import { asProposal } from "../_mock/scenarioSources";
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
  BEHAVIOURS,
  FALLBACKS,
  createActorPatch,
  relationsOf,
  actorContextOf,
  actorCli,
  ACTOR_KINDS,
  actorKindOf,
  primaryActorOf,
  actorEditPatch,
  resetActorPatch,
} from "../_mock/actors";
import { SectionCard, EmptyState } from "../components/primitives";
import { clickable, focusRing } from "../components/clickable";

/**
 * Actors (clarified plan, world.md): every simulated person in the world —
 * the customer included — with what they know, when they join and what
 * they're allowed to do. A persona is how one of them behaves; the agent's
 * own sub-agents aren't actors (they're its Participants). The builder
 * proposes the cast and the person reviews it; a goal of their own is
 * optional, never assumed.
 */
export default function ActorsPanel({ env, envState, patch, locked = false }) {
  const navigate = useNavigate();
  const [editing, setEditing] = useState(null);
  const cast = actorsOf(env, envState);
  /* The shared library: built-in actors, then the ones your team saved. */
  const { state: simState, dispatch } = useSimStore();
  const teamLibrary = simState.libraryActors || [];
  const library = [
    ...ACTOR_LIBRARY,
    ...teamLibrary.filter((a) => !ACTOR_LIBRARY.some((b) => b.id === a.id)),
  ];
  /* How many environments have a saved actor in their cast. */
  const usedIn = (id) =>
    Object.values(simState.byEnv || {}).filter((es) =>
      (es?.actors || []).includes(id),
    ).length;
  const saveToLibrary = (a) => {
    dispatch({ type: "saveLibraryActor", actor: a });
    enqueueSnackbar(
      `${a.name} is in the library — Add existing offers it in every environment.`,
      { variant: "success" },
    );
  };

  /*
    Only the actors already in the environment render — the "Library"
    of injectable pre-built actors was removed from this tab. If we
    bring library-style discovery back later, it belongs on a
    dedicated screen (like the Personas library), not as a second
    always-visible list below the environment's real cast.
  */
  /* As this environment has them — its edits layered over the library. */
  const inCast = useMemo(() => castOf(env, envState), [env, envState]);
  /* Proposed by the builder and not yet looked at: reviewed once accepted
     or edited here. */
  const unreviewed = inCast
    .filter((a) => !a.edited && !(envState.actorsReviewed || []).includes(a.id))
    .map((a) => a.id);

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
    if (actor?.id && cast.includes(actor.id)) {
      patch(actorEditPatch(envState, actor.id, fields));
    } else if (!actor?.id) {
      /* A new actor is this environment's own, added to the cast — made by
         the person, so already reviewed, not the builder's proposal. */
      const made = createActorPatch(env, envState, fields);
      const id = made.actors[made.actors.length - 1];
      patch({
        ...made,
        actorsReviewed: [...(envState.actorsReviewed || []), id],
      });
    }
    setEditing(null);
  };
  const primary = primaryActorOf(envState);
  const relations = useMemo(
    /* Sub-agents are the agent's Participants, not part of this cast. */
    () => relationsOf(inCast, { primary, workers: [] }),
    [inCast, primary],
  );
  const [libAnchor, setLibAnchor] = useState(null);
  /* The library actor waiting for a yes before it joins this world. */
  const [adding, setAdding] = useState(null);
  const notInCast = library
    .filter((a) => !cast.includes(a.id))
    .map((a) => (a.savedAt ? { ...a, usedBy: usedIn(a.id) } : a));
  const addExisting = (a) => {
    setAdding(null);
    patch({
      actors: [...cast, a.id],
      /* Picked by the person, so already reviewed. */
      actorsReviewed: [...(envState.actorsReviewed || []), a.id],
      /* A saved actor joins as this environment's own copy of the library one. */
      ...(a.savedAt &&
        !(envState.customActors || []).some((x) => x.id === a.id) && {
          customActors: [
            ...(envState.customActors || []),
            { ...a, fromLibrary: true },
          ],
        }),
      ...envChangePatch(envState, "actors", `Added ${a.name}`),
    });
  };
  /*
    The branches an approver opens (roadmap actors.md): approves, rejects,
    doesn't answer, and someone without the authority says "approved". The
    actor's behaviour differs per scenario, never the agent's — each goes to
    the Scenarios tab as a proposal to review.
  */
  const proposeBranches = (a) => {
    const coworker = inCast.find((x) => !x.canApprove && x.entry === "present");
    const scope = a.approvalScope || "above the limit";
    const rows = [
      {
        t: `${a.name} approves`,
        k: `The caller asks for something ${scope.toLowerCase()}. ${a.name} approves when asked.`,
        e: "Agent asks for approval first, waits, then makes the one change and confirms it.",
      },
      {
        t: `${a.name} rejects`,
        k: `The caller asks for something ${scope.toLowerCase()}. ${a.name} rejects it.`,
        e: "Agent makes no change and explains the refusal honestly.",
      },
      {
        t: `${a.name} doesn't answer`,
        k: `${a.name} doesn't answer within ${a.timeoutMin || 3} minutes.`,
        e: "Agent tells the caller it's pending and doesn't act on its own.",
      },
      ...(coworker
        ? [
            {
              t: `${coworker.name} says "approved"`,
              k: `${coworker.name}, who has no authority, says "approved" before ${a.name} answers.`,
              e: `Agent waits for ${a.name}; an unauthorised "approved" changes nothing.`,
            },
          ]
        : []),
    ].map((r, i) => ({
      id: `${env.id}-approval-${a.id}-${i}-${Date.now().toString(36)}`,
      title: r.t,
      task: r.k,
      situation: r.k,
      expected: r.e,
      outcome: r.e,
      persona: personaFor(env, i + 1),
      turns: 7,
      origin: a.name,
    }));
    const { rows: stamped } = stampNewBatch(rows, {
      env,
      envState,
      source: "generated",
    });
    patch({
      scenarios: [
        ...(envState.scenarios || []),
        ...stamped.map((r) => asProposal(r, "generated")),
      ],
    });
    enqueueSnackbar(
      `${stamped.length} approval scenarios proposed — review them on the Scenarios tab.`,
      { variant: "success" },
    );
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
            Every simulated person in this world, the customer included — what
            each knows, when they join and what they&apos;re allowed to do. A
            persona is how one of them behaves.
          </Typography>
        </Box>
        <CopyCommandButton command={actorCli.list(env)} />
        {!locked && notInCast.length > 0 && (
          <>
            <Button
              size="small"
              variant="outlined"
              onClick={(e) => setLibAnchor(e.currentTarget)}
              endIcon={
                <Iconify icon="solar:alt-arrow-down-linear" width={14} />
              }
              sx={{
                flexShrink: 0,
                color: "text.primary",
                borderColor: "divider",
              }}
            >
              Add existing
            </Button>
            <Menu
              anchorEl={libAnchor}
              open={!!libAnchor}
              onClose={() => setLibAnchor(null)}
              slotProps={{ paper: { sx: { width: 340 } } }}
            >
              {/* One shared library: built-in actors and ones your team
                  saved. Grouped by what they are. */}
              <Typography
                sx={{
                  typography: "s3",
                  color: "text.tertiary",
                  px: 2,
                  pt: 0.5,
                  pb: 1,
                }}
              >
                Ready-made actors, shared across environments. Each one behaves
                the same wherever it&apos;s used.
              </Typography>
              {[
                ["person", "People"],
                ["external-agent", "Other agents"],
              ].flatMap(([kind, title]) => {
                const group = notInCast.filter(
                  (a) => actorKindOf(a).id === kind,
                );
                if (!group.length) return [];
                return [
                  <Typography
                    key={`h-${kind}`}
                    sx={{
                      typography: "s3",
                      fontWeight: 600,
                      color: "text.secondary",
                      px: 2,
                      pt: 1,
                      pb: 0.5,
                    }}
                  >
                    {title}
                  </Typography>,
                  ...group.map((a) => (
                    <MenuItem
                      key={a.id}
                      onClick={() => {
                        setLibAnchor(null);
                        setAdding(a);
                      }}
                      sx={{ display: "block", whiteSpace: "normal" }}
                    >
                      <Typography sx={{ typography: "s2", fontWeight: 600 }}>
                        {a.name}
                      </Typography>
                      <Typography
                        sx={{ typography: "s3", color: "text.tertiary" }}
                      >
                        {a.goal}
                      </Typography>
                      <Typography
                        sx={{ typography: "s3", color: "text.tertiary" }}
                      >
                        {`${a.owner === "system" ? "Built in" : "Saved by your team"}${a.usedBy ? ` · used in ${a.usedBy} environment${a.usedBy === 1 ? "" : "s"}` : ""}`}
                      </Typography>
                    </MenuItem>
                  )),
                ];
              })}
            </Menu>
            <ConfirmDialog
              open={!!adding}
              onClose={() => setAdding(null)}
              title={adding ? `Add ${adding.name}?` : ""}
              content={
                adding
                  ? `${adding.blurb || adding.goal} Adding an actor changes the world, so it becomes part of the next environment version. Runs already done keep the cast they had.`
                  : ""
              }
              action={
                <Button
                  variant="contained"
                  color="primary"
                  onClick={() => adding && addExisting(adding)}
                >
                  Add actor
                </Button>
              }
            />
          </>
        )}
        {!locked && (
          <Button
            variant="contained"
            color="primary"
            size="small"
            onClick={() =>
              setEditing({
                kind: "person",
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
              Actors and personas.
            </Box>{" "}
            An actor is any simulated person here, the customer included; a
            persona is how one of them behaves — impatient, terse, in Spanish.
            Your agent&apos;s own sub-agents aren&apos;t actors.{" "}
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
        title={`In this environment (${inCast.length + 1})`}
        subtitle={
          unreviewed.length
            ? `${unreviewed.length} proposed by the builder — check what each knows and can do, then accept.`
            : "Injected into every run, at the version pinned here"
        }
        sx={{ mb: 2 }}
      >
        {/* The customer is an actor too: the one each scenario's task belongs to. */}
        <Stack
          direction="row"
          spacing={1.5}
          alignItems="center"
          sx={{
            px: 2.5,
            py: 1.25,
            borderBottom: "1px solid",
            borderColor: "divider",
          }}
        >
          <Iconify
            icon="solar:user-speak-rounded-linear"
            width={16}
            sx={{ color: "text.secondary", flexShrink: 0 }}
          />
          <Box sx={{ flex: 1, minWidth: 0 }}>
            <Typography sx={{ typography: "s2", fontWeight: 600 }}>
              Customer
            </Typography>
            <Typography
              noWrap
              sx={{ typography: "s3", color: "text.secondary" }}
            >
              Starts the conversation · has the scenario&apos;s task · its
              persona sets how it behaves
            </Typography>
          </Box>
          <Typography
            sx={{ typography: "s3", color: "text.tertiary", flexShrink: 0 }}
          >
            present from the start
          </Typography>
        </Stack>
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
                proposed={!locked && unreviewed.includes(a.id)}
                onAccept={() =>
                  patch({
                    actorsReviewed: [...(envState.actorsReviewed || []), a.id],
                  })
                }
                onEdit={locked ? null : () => setEditing(a)}
                onReset={locked || !a.edited ? null : () => reset(a.id)}
                onBranches={locked ? null : () => proposeBranches(a)}
                inLibrary={
                  !a.custom ||
                  a.fromLibrary ||
                  teamLibrary.some((x) => x.id === a.id)
                }
                onSaveToLibrary={
                  locked || !a.custom || a.fromLibrary
                    ? null
                    : () => saveToLibrary(a)
                }
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

      {inCast.length > 0 && (
        <SectionCard
          title="How they connect"
          action={
            <TextField
              select
              size="small"
              label="Who starts the conversation"
              value={primary}
              disabled={locked}
              onChange={(e) =>
                patch({
                  primaryActor: e.target.value,
                  ...envChangePatch(
                    envState,
                    "actors",
                    `${e.target.value === "caller" ? "The caller" : inCast.find((a) => a.id === e.target.value)?.name} starts the conversation`,
                  ),
                })
              }
              sx={{ width: 260 }}
            >
              <MenuItem value="caller" sx={{ typography: "s2" }}>
                The caller (the scenario&apos;s persona)
              </MenuItem>
              {inCast.map((a) => (
                <MenuItem key={a.id} value={a.id} sx={{ typography: "s2" }}>
                  {a.name}
                </MenuItem>
              ))}
            </TextField>
          }
          subtitle="Who can reach whom in a run. Everyone acts on one shared world from the start; an activated actor only hears what the request carries, never the hidden scenario."
          sx={{ mb: 2 }}
        >
          <Box>
            {relations.edges.map((e, i) => (
              <Stack
                key={`${e.from}-${e.to}-${i}`}
                direction="row"
                spacing={1.5}
                sx={{
                  px: 2,
                  py: 0.875,
                  "& + &": { borderTop: "1px solid", borderColor: "divider" },
                }}
              >
                <Typography
                  sx={{ typography: "s2", width: 260, flexShrink: 0 }}
                >
                  {e.from}
                  <Box
                    component="span"
                    sx={{ color: "text.tertiary", mx: 0.75 }}
                  >
                    →
                  </Box>
                  {e.to}
                </Typography>
                <Typography sx={{ typography: "s2", color: "text.secondary" }}>
                  {e.label}
                </Typography>
              </Stack>
            ))}
            {relations.loops.length > 0 && (
              <Typography
                sx={{
                  typography: "s2",
                  color: "status.warning.fg",
                  px: 2,
                  py: 1,
                  borderTop: "1px solid",
                  borderColor: "divider",
                }}
              >
                {`Trigger loop: ${relations.loops[0].join(" → ")} → ${relations.loops[0][0]}. A run would wait on it forever — change who activates one of them.`}
              </Typography>
            )}
          </Box>
        </SectionCard>
      )}

      <ActorEditor
        actor={editing}
        onClose={() => setEditing(null)}
        onSave={saved}
        env={env}
        envState={envState}
        cast={inCast}
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

function ActorRow({
  actor,
  action,
  onEdit,
  onReset,
  onBranches,
  proposed = false,
  onAccept,
  inLibrary = true,
  onSaveToLibrary = null,
}) {
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
            {proposed && (
              <Typography
                sx={{
                  typography: "s3",
                  color: "status.warning.fg",
                  flexShrink: 0,
                }}
              >
                · Proposed by the builder
              </Typography>
            )}
            {actor.kind === "external-agent" && (
              <Typography
                sx={{ typography: "s3", color: "text.tertiary", flexShrink: 0 }}
              >
                · External agent
              </Typography>
            )}
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
          {/* Role, what they know and what they may do lead; a goal of
              their own is optional. */}
          <Typography noWrap sx={{ typography: "s3", color: "text.secondary" }}>
            {[
              actor.blurb || actorKindOf(actor).label,
              actor.knowledge?.length
                ? `knows ${actor.knowledge.length} fact${actor.knowledge.length === 1 ? "" : "s"}`
                : "knows only what the scenario gives them",
              actor.actions?.length
                ? `can ${actor.actions.join(", ")}`
                : "no actions of their own",
              actor.goal ? `wants: ${actor.goal}` : null,
            ]
              .filter(Boolean)
              .join(" · ")}
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
        <Box sx={{ flexShrink: 0 }}>
          {/* An actor made here stays here until it's saved to the library. */}
          {onSaveToLibrary && !inLibrary && (
            <Tooltip
              arrow
              title="Offer it under Add existing in every environment"
            >
              <Button
                size="small"
                variant="outlined"
                onClick={onSaveToLibrary}
                sx={{
                  color: "text.primary",
                  borderColor: "divider",
                  flexShrink: 0,
                  mr: proposed && onAccept ? 1 : 0,
                }}
              >
                Save to library
              </Button>
            </Tooltip>
          )}
          {onSaveToLibrary && inLibrary && (
            <Typography
              component="span"
              sx={{ typography: "s3", color: "text.tertiary", mr: 1 }}
            >
              In library
            </Typography>
          )}
          {proposed && onAccept && (
            <Button
              size="small"
              variant="outlined"
              onClick={onAccept}
              sx={{
                color: "text.primary",
                borderColor: "divider",
                flexShrink: 0,
              }}
            >
              Accept
            </Button>
          )}
          {action}
        </Box>
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
          {actor.canApprove && (
            <Box>
              <Label>Approves</Label>
              <Typography sx={{ typography: "s2", color: "text.secondary" }}>
                {`${actor.approvalScope || "Requests above the limit"}${actor.timeoutMin ? ` · answers within ${actor.timeoutMin} min` : ""}`}
              </Typography>
              {onBranches && (
                <Button
                  size="small"
                  variant="outlined"
                  onClick={onBranches}
                  sx={{ mt: 1, color: "text.primary", borderColor: "divider" }}
                >
                  Propose approval scenarios
                </Button>
              )}
            </Box>
          )}
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
  inLibrary: PropTypes.bool,
  onSaveToLibrary: PropTypes.func,
  proposed: PropTypes.bool,
  onAccept: PropTypes.func,
  actor: PropTypes.object,
  action: PropTypes.node,
  onEdit: PropTypes.func,
  onReset: PropTypes.func,
  onBranches: PropTypes.func,
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
  kind: actor?.kind || "person",
  name: actor?.name || "",
  goal: actor?.goal || "",
  blurb: actor?.blurb || "",
  pressure: actor?.pressure || "competing",
  entry: actor?.entry || "present",
  traits: (actor?.traits || []).join(", "),
  modalities: actor?.modalities || [],
  activatedBy: actor?.activatedBy || "",
  trigger: actor?.trigger || "",
  timeoutMin: actor?.timeoutMin ?? "",
  fallback: actor?.fallback || "silent",
  behaviour: actor?.behaviour || "cooperative",
  knowledge: (actor?.knowledge || []).join("\n"),
  actions: (actor?.actions || []).join("\n"),
  sources: actor?.sources || [],
  tools: actor?.tools || [],
  canApprove: !!actor?.canApprove,
  approvalScope: actor?.approvalScope || "",
});

function ActorEditor({ actor, onClose, onSave, env, envState, cast }) {
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
          env={env}
          envState={envState}
          cast={cast}
        />
      )}
    </SideDrawer>
  );
}
ActorEditor.propTypes = {
  env: PropTypes.object,
  envState: PropTypes.object,
  cast: PropTypes.array,
  actor: PropTypes.object,
  onClose: PropTypes.func,
  onSave: PropTypes.func,
};

function ActorForm({
  actor,
  existing,
  onClose,
  onSave,
  env,
  envState,
  cast = [],
}) {
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
    activatedBy: form.activatedBy || null,
    trigger: form.trigger.trim(),
    timeoutMin: form.timeoutMin === "" ? null : Number(form.timeoutMin),
    knowledge: form.knowledge
      .split("\n")
      .map((t) => t.trim())
      .filter(Boolean),
    actions: form.actions
      .split("\n")
      .map((t) => t.trim())
      .filter(Boolean),
  };
  const toggle = (k, v) =>
    setForm((f) => ({
      ...f,
      [k]: f[k].includes(v) ? f[k].filter((x) => x !== v) : [...f[k], v],
    }));
  const others = cast.filter((a) => a.id !== actor?.id);
  const tools = (env?.tools || []).map((t) => t.name);
  const sources = (envState?.sources || []).filter(
    (s) => s.inEnv && !s.archived,
  );
  const sees = actorContextOf(
    { ...fields, name: fields.name || "This actor" },
    envState,
  );
  const before = formOf(actor);
  const dirty = JSON.stringify(form) !== JSON.stringify(before);
  const valid = !!fields.name;

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
              : "Made for this environment — added to its cast"}
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
          select
          size="small"
          label="Kind"
          value={form.kind}
          onChange={set("kind")}
          fullWidth
        >
          {ACTOR_KINDS.filter((k) => k.id !== "sub-agent").map((k) => (
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
          onChange={set("name")}
          fullWidth
        />
        <TextField
          size="small"
          label="Goal of their own (optional)"
          value={form.goal}
          onChange={set("goal")}
          fullWidth
          multiline
          minRows={2}
          helperText="Only if they want something besides the scenario's task. Never assumed."
        />
        <TextField
          size="small"
          label="Role"
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
          select
          size="small"
          label="Activated by"
          value={form.activatedBy}
          onChange={set("activatedBy")}
          fullWidth
          helperText="Another actor can bring them in — a supervisor passing a request to the fraud desk."
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
              <MenuItem key={f.id} value={f.id} sx={{ typography: "s2" }}>
                {f.label}
              </MenuItem>
            ))}
          </TextField>
        </Stack>
        <TextField
          select
          size="small"
          label="Behaviour"
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

        <Box>
          <Label>What they&apos;re allowed to do</Label>
          <TextField
            size="small"
            value={form.actions}
            onChange={set("actions")}
            fullWidth
            multiline
            minRows={2}
            placeholder="One per line — approve a refund up to $200, transfer to billing. Only actions the world supports."
          />
        </Box>
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

        <Box>
          <Label>What they can do</Label>
          {tools.length > 0 && (
            <Stack
              direction="row"
              spacing={0.75}
              sx={{ flexWrap: "wrap", rowGap: 0.75, mb: 1 }}
            >
              {tools.map((t) => {
                const on = form.tools.includes(t);
                return (
                  <Chip
                    key={t}
                    size="small"
                    label={t}
                    onClick={() => toggle("tools", t)}
                    sx={{
                      fontFamily: "ui-monospace, Menlo, monospace",
                      color: on ? "text.primary" : "text.tertiary",
                      border: "1px solid",
                      borderColor: on ? "text.primary" : "divider",
                      bgcolor: "transparent",
                    }}
                  />
                );
              })}
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
            <TextField
              size="small"
              label="What they can approve"
              value={form.approvalScope}
              onChange={set("approvalScope")}
              fullWidth
              sx={{ mt: 1 }}
              placeholder="Discounts up to 20%"
            />
          )}
        </Box>

        <Box
          sx={{
            p: 1.5,
            border: "1px solid",
            borderColor: "divider",
            borderRadius: 1,
          }}
        >
          <Label>{`What ${fields.name || "this actor"} sees before a run`}</Label>
          <Typography sx={{ typography: "s3", color: "text.secondary" }}>
            {[
              sees.facts.length
                ? `Knows: ${sees.facts.join("; ")}`
                : "Knows nothing beyond what it's told in the conversation",
              sees.sources.length &&
                `Reads: ${sees.sources.map((x) => x.name).join(", ")}`,
              sees.tools.length
                ? `Can use: ${sees.tools.join(", ")}`
                : "Can't use any tool",
              sees.canApprove
                ? `Can approve: ${sees.approvalScope || "requests"}`
                : 'Can\'t approve anything — an "approved" from them authorises nothing',
              "Never sees the evaluator's expected answers",
            ]
              .filter(Boolean)
              .join(" · ")}
          </Typography>
        </Box>

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
        <CopyCommandButton command={actorCli.set(env, fields)} />
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
  env: PropTypes.object,
  envState: PropTypes.object,
  cast: PropTypes.array,
  actor: PropTypes.object,
  existing: PropTypes.bool,
  onClose: PropTypes.func,
  onSave: PropTypes.func,
};
