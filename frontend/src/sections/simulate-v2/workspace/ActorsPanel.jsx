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
  Menu,
} from "@mui/material";
import CopyCommandButton from "../components/CopyCommandButton";
import { ConfirmDialog } from "src/components/custom-dialog";
import { useSimStore } from "../store";
import { enqueueSnackbar } from "notistack";
import { personaFor } from "../_mock/scenarios";
import { stampNewBatch } from "../_mock/addScenarios";
import { asProposal } from "../_mock/scenarioSources";
import { textTone } from "../components/textTone";
import Iconify from "src/components/iconify";
import { paths } from "src/routes/paths";
import { envChangePatch } from "../_mock/versions";
import {
  ACTOR_LIBRARY,
  SIDES,
  NOT_A_WORLD_ACTION,
  actorsOf,
  castOf,
  castBySide,
  sideOf,
  roleOf,
  arrivalLineOf,
  actorActionsOf,
  actorViewOf,
  getPressure,
  getEntry,
  createActorPatch,
  relationsOf,
  actorCli,
  actorKindOf,
  primaryActorOf,
  actorEditPatch,
  resetActorPatch,
} from "../_mock/actors";
import { callerCastOf, scenarioCountsByActor } from "../_mock/subActors";
import ActorEditor from "./ActorEditor";
import ActorSees from "./ActorSees";
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
  /* Every id in use, so a new actor's id is checked against all of them. */
  const takenIds = [
    ...library.map((a) => a.id),
    ...cast,
    ...(envState.customActors || []).map((a) => a.id),
  ];
  const bySide = castBySide(inCast);
  /* How many scenarios include each sub-actor (the caller-side group). */
  const subActorUse = useMemo(
    () =>
      scenarioCountsByActor(
        envState.scenarios || [],
        callerCastOf(env, envState),
        env,
      ),
    [env, envState],
  );
  const [addAnchor, setAddAnchor] = useState(null);
  /* Actors are read from the environment; "loading" and "failed" are the
     states the card has to be able to say. */
  const loadState = envState.actorsLoad || "ready";

  /* One actor's row, with what it takes to edit, reset, accept or remove it. */
  const renderRow = (a) => (
    <Box key={a.id} sx={{ borderBottom: "1px solid", borderColor: "divider" }}>
      <ActorRow
        actor={a}
        env={env}
        envState={envState}
        cast={inCast}
        proposed={!locked && unreviewed.includes(a.id)}
        scenarioCount={
          sideOf(a) === "caller" ? subActorUse.get(a.id) ?? 0 : null
        }
        onAccept={() =>
          patch({
            actorsReviewed: [...(envState.actorsReviewed || []), a.id],
          })
        }
        onEdit={locked ? null : () => setEditing(a)}
        onReset={locked || !a.edited ? null : () => reset(a.id)}
        onBranches={locked ? null : () => proposeBranches(a)}
        inLibrary={
          !a.custom || a.fromLibrary || teamLibrary.some((x) => x.id === a.id)
        }
        onSaveToLibrary={
          locked || !a.custom || a.fromLibrary ? null : () => saveToLibrary(a)
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
    </Box>
  );

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
            Everyone simulated in this world, the customer included. Your
            agent&apos;s own workers aren&apos;t actors.
          </Typography>
        </Box>
        <Button
          size="small"
          variant="outlined"
          onClick={() =>
            navigate(`${paths.dashboard.simulate.environments}?tab=personas`)
          }
          sx={{ flexShrink: 0, color: "text.primary", borderColor: "divider" }}
        >
          Personas
        </Button>
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
          <>
            <Button
              variant="contained"
              color="primary"
              size="small"
              onClick={(e) => setAddAnchor(e.currentTarget)}
              startIcon={<Iconify icon="solar:add-circle-linear" width={16} />}
              sx={{ flexShrink: 0 }}
            >
              Add actor
            </Button>
            <Menu
              anchorEl={addAnchor}
              open={!!addAnchor}
              onClose={() => setAddAnchor(null)}
              slotProps={{ paper: { sx: { width: 320 } } }}
            >
              {[
                [
                  "caller",
                  "Someone nearby, who takes the phone, or a second person in the chat",
                ],
                [
                  "world",
                  "A manager who approves, a teammate on Slack, another company's system",
                ],
              ].map(([side, note]) => (
                <MenuItem
                  key={side}
                  onClick={() => {
                    setAddAnchor(null);
                    setEditing({ side });
                  }}
                  sx={{ display: "block", whiteSpace: "normal" }}
                >
                  <Typography sx={{ typography: "s2", fontWeight: 600 }}>
                    {SIDES.find((x) => x.id === side).label}
                  </Typography>
                  <Typography sx={{ typography: "s3", color: "text.tertiary" }}>
                    {note}
                  </Typography>
                </MenuItem>
              ))}
            </Menu>
          </>
        )}
      </Stack>

      <SectionCard
        title={`Actors (${inCast.length + 1})`}
        subtitle={
          unreviewed.length ? `${unreviewed.length} to review` : "In every run"
        }
        sx={{ mb: 2 }}
      >
        {loadState === "loading" && (
          <Typography
            sx={{ typography: "s2", color: "text.secondary", px: 2.5, py: 1.5 }}
          >
            Loading actors…
          </Typography>
        )}
        {loadState === "error" && (
          <Stack
            direction="row"
            alignItems="center"
            spacing={1.5}
            sx={{ px: 2.5, py: 1.25 }}
          >
            <Typography
              sx={{ typography: "s2", color: "status.danger.fg", flex: 1 }}
            >
              Couldn&apos;t load actors.
            </Typography>
            <Button
              size="small"
              variant="outlined"
              onClick={() => patch({ actorsLoad: undefined })}
              sx={{ color: "text.primary", borderColor: "divider" }}
            >
              Try again
            </Button>
          </Stack>
        )}
        {loadState === "ready" && (
          <>
            <GroupHeader
              title={SIDES[0].label}
              count={bySide.caller.length + 1}
            />
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
                  Has the scenario&apos;s task · behaves as its persona
                </Typography>
              </Box>
              <Typography
                sx={{ typography: "s3", color: "text.tertiary", flexShrink: 0 }}
              >
                From the start
              </Typography>
            </Stack>
            {bySide.caller.map((a) => renderRow(a))}
            <GroupHeader title={SIDES[1].label} count={bySide.world.length} />
            {bySide.world.length === 0 ? (
              <EmptyState
                icon="solar:users-group-two-rounded-linear"
                title="No one in the world yet"
                body="A manager who approves, a teammate on Slack, another company's system — add one and see what it sees."
              />
            ) : (
              bySide.world.map((a) => renderRow(a))
            )}
          </>
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
                The caller
              </MenuItem>
              {inCast.map((a) => (
                <MenuItem key={a.id} value={a.id} sx={{ typography: "s2" }}>
                  {a.name}
                </MenuItem>
              ))}
            </TextField>
          }
          subtitle="Who can reach whom in a run"
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
                {`Trigger loop: ${relations.loops[0].join(" → ")} → ${relations.loops[0][0]}. Change who activates one of them.`}
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
        takenIds={takenIds.filter((id) => id !== editing?.id)}
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
  env,
  envState,
  cast = [],
  action,
  onEdit,
  onReset,
  onBranches,
  proposed = false,
  onAccept,
  inLibrary = true,
  onSaveToLibrary = null,
  scenarioCount = null,
}) {
  const [open, setOpen] = useState(false);
  const pressure = getPressure(actor.pressure);
  const entry = getEntry(actor.entry);
  /* When they come in, as this side says it. */
  const comes = arrivalLineOf(actor) || entry.label.toLowerCase();
  const actions = actorActionsOf(actor, env, envState);
  const view = actorViewOf(actor, { env, envState, cast });

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
            {/* Skip the role when the name already says it. */}
            {!actor.name
              .toLowerCase()
              .includes(roleOf(actor).toLowerCase()) && (
              <Typography
                noWrap
                sx={{ typography: "s3", color: "text.tertiary", flexShrink: 0 }}
              >
                · {roleOf(actor)}
              </Typography>
            )}
            {proposed && (
              <Typography
                sx={{
                  typography: "s3",
                  color: "status.warning.fg",
                  flexShrink: 0,
                }}
              >
                · New
              </Typography>
            )}
            {actor.kind === "external-agent" && (
              <Typography
                sx={{ typography: "s3", color: "text.tertiary", flexShrink: 0 }}
              >
                · External agent
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
          {/* What they know and may do is in the open row. */}
          <Typography noWrap sx={{ typography: "s3", color: "text.secondary" }}>
            {actor.blurb || actorKindOf(actor).label}
            {actions.some((x) => !x.known) && (
              <Box component="span" sx={{ color: "status.danger.fg" }}>
                {` · ${actions
                  .filter((x) => !x.known)
                  .map((x) => x.name)
                  .join(", ")}: ${NOT_A_WORLD_ACTION}`}
              </Box>
            )}
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
            width: 150,
            whiteSpace: "nowrap",
          }}
        >
          {comes.charAt(0).toUpperCase() + comes.slice(1)}
        </Typography>
        <Typography
          sx={{
            typography: "s3",
            color: "text.tertiary",
            flexShrink: 0,
            display: { xs: "none", sm: "block" },
          }}
        >
          {scenarioCount == null
            ? `Used by ${actor.usedBy}`
            : `${scenarioCount} scenario${scenarioCount === 1 ? "" : "s"}`}
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
          <ActorSees view={view} name={actor.name} />
          <Box>
            <Label>What they do</Label>
            <Typography sx={{ typography: "s2", color: "text.secondary" }}>
              {actor.blurb}
            </Typography>
          </Box>
          {actor.tests && (
            <Box>
              <Label>What it tests</Label>
              <Typography sx={{ typography: "s2", color: "text.secondary" }}>
                {actor.tests}
              </Typography>
            </Box>
          )}
          <Box>
            <Label>When they enter</Label>
            <Typography sx={{ typography: "s2", color: "text.secondary" }}>
              {sideOf(actor) === "caller" && arrivalLineOf(actor)
                ? comes.charAt(0).toUpperCase() + comes.slice(1)
                : `${entry.label} — ${entry.blurb}`}
            </Typography>
          </Box>
          {sideOf(actor) === "caller" && actor.voice && (
            <Box>
              <Label>Voice</Label>
              <Typography sx={{ typography: "s2", color: "text.secondary" }}>
                {[
                  actor.voice.gender,
                  actor.voice.age,
                  actor.voice.accent,
                  `loudness ${(actor.loudness || "automatic").toLowerCase()}`,
                ]
                  .filter(Boolean)
                  .join(" · ")}
              </Typography>
            </Box>
          )}
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
              {(actor.traits || []).map((t) => (
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
              {(actor.versions || []).map((v) => (
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
  env: PropTypes.object,
  envState: PropTypes.object,
  cast: PropTypes.array,
  inLibrary: PropTypes.bool,
  onSaveToLibrary: PropTypes.func,
  proposed: PropTypes.bool,
  onAccept: PropTypes.func,
  actor: PropTypes.object,
  action: PropTypes.node,
  onEdit: PropTypes.func,
  onReset: PropTypes.func,
  onBranches: PropTypes.func,
  scenarioCount: PropTypes.number,
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

function GroupHeader({ title, count }) {
  return (
    <Stack
      direction="row"
      alignItems="center"
      spacing={1}
      sx={{
        px: 2.5,
        py: 0.75,
        bgcolor: "background.neutral",
        borderBottom: "1px solid",
        borderColor: "divider",
      }}
    >
      <Typography sx={{ typography: "s3", fontWeight: 600 }}>
        {title}
      </Typography>
      <Typography sx={{ typography: "s3", color: "text.tertiary" }}>
        {count}
      </Typography>
    </Stack>
  );
}
GroupHeader.propTypes = {
  title: PropTypes.string,
  count: PropTypes.number,
};
