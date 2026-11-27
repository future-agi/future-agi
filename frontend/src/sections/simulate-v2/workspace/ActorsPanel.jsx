import PropTypes from "prop-types";
import { useMemo, useState } from "react";
import { useNavigate } from "react-router-dom";
import {
  Box,
  Stack,
  Typography,
  Button,
  ButtonBase,
  IconButton,
  Tooltip,
  Collapse,
} from "@mui/material";
import PersonasInUseDrawer from "./PersonasInUseDrawer";
import { useSimStore } from "../store";
import { enqueueSnackbar } from "notistack";
import { personaFor } from "../_mock/scenarios";
import { stampNewBatch } from "../_mock/addScenarios";
import { asProposal } from "../_mock/scenarioSources";
import Iconify from "src/components/iconify";
import { paths } from "src/routes/paths";
import { envChangePatch } from "../_mock/versions";
import {
  ACTOR_LIBRARY,
  NOT_A_WORLD_ACTION,
  actorsOf,
  castOf,
  sideOf,
  arrivalLineOf,
  actorActionsOf,
  actorViewOf,
  getEntry,
  createActorPatch,
  relationsOf,
  actorKindOf,
  CALLER_KINDS,
  firstSpeakerOf,
  actorEditPatch,
  resetActorPatch,
  approvesOf,
  approvalActionsOf,
  kindValueOf,
  KIND_OPTIONS,
  roleGroupsOf,
} from "../_mock/actors";
import { callerCastOf, scenarioCountsByActor } from "../_mock/subActors";
import ActorEditor from "./ActorEditor";
import { TextAction } from "./scenarios/ScenarioStateBits";
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
  /* The personas the customer plays here, from this environment's scenarios. */
  const [personasOpen, setPersonasOpen] = useState(false);
  const liveScenarios = (envState?.scenarios || []).filter(
    (x) => !x.archived && !x.proposed,
  );
  const personaCount = new Set(
    liveScenarios.map((x) => x.persona?.name || "Unnamed caller"),
  ).size;
  const cast = actorsOf(env, envState);
  /* Every actor id in use — built-in ones and any saved earlier — so a new
     actor's id never collides. There's no shared library to add from. */
  const { state: simState } = useSimStore();
  const library = [...ACTOR_LIBRARY, ...(simState.libraryActors || [])];

  /*
    Only the actors already in the environment render — the "Library"
    of injectable pre-built actors was removed from this tab. If we
    bring library-style discovery back later, it belongs on a
    dedicated screen (like the Personas library), not as a second
    always-visible list below the environment's real cast.
  */
  /* As this environment has them — its edits layered over the library. */
  const inCast = useMemo(() => castOf(env, envState), [env, envState]);
  /* No Accept step (Dileep, 24 Nov): the actors are part of the environment
     approved with the contract, and a later builder change lands as an
     unsaved edit reviewed before Save. */

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
      patch(createActorPatch(env, envState, fields));
    }
    setEditing(null);
  };
  const first = firstSpeakerOf(envState);
  const relations = useMemo(
    /* Sub-agents are the agent's Participants, not part of this cast. */
    () => relationsOf(inCast, { first, workers: [] }),
    [inCast, first],
  );
  /*
    The branches an approver opens (roadmap actors.md): approves, rejects,
    doesn't answer, and someone without the authority says "approved". The
    actor's behaviour differs per scenario, never the agent's — each goes to
    the Scenarios tab as a proposal to review.
  */
  const proposeBranches = (a) => {
    const coworker = inCast.find(
      (x) => !approvesOf(x) && x.entry === "present",
    );
    const scope = `that needs ${approvalActionsOf(a).join(" or ")}`;
    const rows = [
      {
        t: `${a.name} approves`,
        k: `The caller asks for something ${scope}. ${a.name} approves when asked.`,
        e: "Agent asks for approval first, waits, then makes the one change and confirms it.",
      },
      {
        t: `${a.name} rejects`,
        k: `The caller asks for something ${scope}. ${a.name} rejects it.`,
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
  /* Actors are read from the environment; "loading" and "failed" are the
     states the card has to be able to say. */
  const loadState = envState.actorsLoad || "ready";

  /* The world's files, from Sources — the one place files are added. An
     actor reads only the ones ticked for it; grading-only files never. */
  const worldFiles = (envState.sources || []).filter((x) => !x.archived);

  /* The short tags on a row: how they take part, when they come in, and how
     many scenarios use them (caller-side people are bound per scenario). */
  const tagsFor = (a) => {
    const n = sideOf(a) === "caller" ? subActorUse.get(a.id) ?? 0 : null;
    const comes = arrivalLineOf(a) || getEntry(a.entry).label.toLowerCase();
    return [
      sideOf(a) === "caller"
        ? CALLER_KINDS.find((k) => k.id === a.kind)?.label || "With the user"
        : a.kind === "external-agent"
          ? "Another company's system"
          : "Acts in the world",
      comes.charAt(0).toUpperCase() + comes.slice(1),
      n == null ? null : `In ${n} scenario${n === 1 ? "" : "s"}`,
    ].filter(Boolean);
  };

  /* One person's row, with what it takes to edit, reset or remove it. In a
     role group the role is the group's line, so the row leads with the person
     and leaves out what everyone in the role shares. */
  const renderRow = (a, group = null) => (
    <Box
      key={a.id}
      sx={
        group
          ? { "& + &": { borderTop: "1px solid", borderColor: "divider" } }
          : { borderBottom: "1px solid", borderColor: "divider" }
      }
    >
      <ActorRow
        actor={a}
        tags={tagsFor(a)}
        group={group}
        env={env}
        envState={envState}
        cast={inCast}
        onEdit={locked ? null : () => setEditing(a)}
        onReset={locked || !a.edited ? null : () => reset(a.id)}
        onBranches={locked ? null : () => proposeBranches(a)}
        files={worldFiles}
        readable={(envState.actorFiles || {})[a.id] || []}
        onToggleFile={
          locked
            ? null
            : (fileId) => {
                const all = envState.actorFiles || {};
                const mine = all[a.id] || [];
                patch({
                  actorFiles: {
                    ...all,
                    [a.id]: mine.includes(fileId)
                      ? mine.filter((x) => x !== fileId)
                      : [...mine, fileId],
                  },
                });
              }
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
            Everyone simulated in this world, the user included. Your
            agent&apos;s own workers aren&apos;t actors.
          </Typography>
        </Box>
        {/* Personas show where they apply — on the Customer row below — in
            a drawer, not as a jump to the workspace library. */}
        {/* No "Add existing" library: the builder proposes the cast from
            the description and files, and Add actor makes one for this
            world (world.md "the harness proposes the actor configuration"). */}
        {/* One Add actor button that opens the form straight away; where
            they are is the form's first field. */}
        {!locked && (
          <Button
            variant="contained"
            color="primary"
            size="small"
            onClick={() => setEditing({ side: "world" })}
            startIcon={<Iconify icon="solar:add-circle-linear" width={16} />}
            sx={{ flexShrink: 0 }}
          >
            Add actor
          </Button>
        )}
      </Stack>

      <SectionCard
        title={`Actors (${inCast.length + 1})`}
        subtitle="In this environment"
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
            {/* One list (Karthik's review, 26 Nov): the user first, then every
                role in cast order. The user is an actor too: the one each
                scenario's task belongs to. */}
            <Stack
              direction="row"
              spacing={1.5}
              alignItems="center"
              sx={{
                px: 2.5,
                py: 1,
                borderBottom: "1px solid",
                borderColor: "divider",
              }}
            >
              <Box sx={{ flex: 1, minWidth: 0 }}>
                <Typography sx={{ typography: "s2_1", fontWeight: 600 }}>
                  User
                </Typography>
                <Typography
                  component="div"
                  noWrap
                  sx={{ typography: "s3", color: "text.secondary" }}
                >
                  Has the scenario&apos;s task · plays{" "}
                  <ButtonBase
                    onClick={() => setPersonasOpen(true)}
                    sx={{
                      typography: "s3",
                      fontWeight: 500,
                      color: "text.primary",
                      textDecoration: "underline",
                      textUnderlineOffset: 3,
                      textDecorationColor: (t) => t.palette.text.disabled,
                      verticalAlign: "baseline",
                      "&:hover": { textDecorationColor: "currentColor" },
                    }}
                  >
                    {`${personaCount} persona${personaCount === 1 ? "" : "s"} across ${liveScenarios.length} scenario${liveScenarios.length === 1 ? "" : "s"}`}
                  </ButtonBase>
                </Typography>
              </Box>
              <Typography
                sx={{ typography: "s3", color: "text.tertiary", flexShrink: 0 }}
              >
                From the start
              </Typography>
            </Stack>
            {inCast.length === 0 ? (
              <EmptyState
                icon="solar:users-group-two-rounded-linear"
                title="No one else yet"
                body="A manager who approves, a teammate on Slack, another company's system — add one and see what it sees."
              />
            ) : (
              roleGroupsOf(inCast).map((g) =>
                g.people.length === 1 ? (
                  renderRow(g.people[0])
                ) : (
                  <RoleGroup
                    key={g.key}
                    group={g}
                    tagsFor={tagsFor}
                    renderPerson={renderRow}
                    onAddPerson={
                      locked
                        ? null
                        : () => {
                            const like = g.people[0];
                            setEditing({
                              ...like,
                              id: undefined,
                              speaksAs: "",
                              edited: false,
                              addingTo: g.label,
                            });
                          }
                    }
                  />
                ),
              )
            )}
          </>
        )}
      </SectionCard>
      <PersonasInUseDrawer
        open={personasOpen}
        onClose={() => setPersonasOpen(false)}
        scenarios={liveScenarios}
        onOpenScenarios={() => {
          setPersonasOpen(false);
          navigate(
            paths.dashboard.simulate.environmentStep(env.id, "scenarios"),
          );
        }}
      />

      {inCast.length > 0 && (
        <SectionCard
          title="How they connect"
          subtitle={`Who can reach whom in a run. ${first === "agent" ? "Your agent" : "The user"} speaks first — set in Settings.`}
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
  tags = [],
  group = null,
  files = [],
  readable = [],
  onToggleFile = null,
}) {
  const [open, setOpen] = useState(false);
  const entry = getEntry(actor.entry);
  /* When they come in, as this side says it. */
  const comes = arrivalLineOf(actor) || entry.label.toLowerCase();
  const actions = actorActionsOf(actor, env, envState);
  const view = actorViewOf(actor, { env, envState, cast });

  return (
    <Box>
      {/* One quiet line of facts: the icon box, the coloured kind chip and
          "New" made every row shout. */}
      <Stack
        className="actor-row-head"
        direction="row"
        alignItems="center"
        spacing={1.5}
        sx={{
          px: 2.5,
          py: 1,
        }}
      >
        <Box
          flex={1}
          minWidth={0}
          {...clickable(() => setOpen((o) => !o), { expanded: open })}
          sx={{ ...focusRing, cursor: "pointer" }}
        >
          <Stack direction="row" alignItems="center" spacing={0.75}>
            {/* Role first, then who they speak as (Karthik, 26 Nov). Under a
                role group the role is the group's line, so the person leads. */}
            {group ? (
              <Typography noWrap sx={{ typography: "s2_1", fontWeight: 600 }}>
                {actor.speaksAs || actor.name}
              </Typography>
            ) : (
              <Typography noWrap sx={{ typography: "s2_1", fontWeight: 600 }}>
                {actor.name}
                {actor.speaksAs && (
                  <Box
                    component="span"
                    sx={{ color: "text.secondary", fontWeight: 400 }}
                  >
                    {` · speaks as ${actor.speaksAs}`}
                  </Box>
                )}
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
                title={`Edited in this environment${actor.library?.name && actor.library.name !== actor.name ? ` — first proposed as "${actor.library.name}"` : ""}. Other environments with this actor keep its standard behaviour.`}
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
          {/* What they push for — left to the group's line when everyone in
              the role pushes for the same. What they know is in the open row. */}
          {!(group?.sharedGoal && actor.goal) && (
            <Typography
              noWrap
              sx={{ typography: "s3", color: "text.secondary" }}
            >
              {actor.goal
                ? `Pushes for: ${actor.goal}`
                : actor.blurb || actorKindOf(actor).label}
              {actions.some((x) => !x.known) && (
                <Box component="span" sx={{ color: "status.danger.fg" }}>
                  {` · ${actions
                    .filter((x) => !x.known)
                    .map((x) => x.name)
                    .join(", ")}: ${NOT_A_WORLD_ACTION}`}
                </Box>
              )}
            </Typography>
          )}
          <Tags tags={tags.filter((t) => !group?.sharedTags.includes(t))} />
        </Box>

        <Stack
          className="row-tools"
          direction="row"
          alignItems="center"
          sx={{ flexShrink: 0 }}
        >
          {onEdit && (
            <Tooltip arrow title="Edit — saving creates a new version">
              <IconButton size="small" onClick={onEdit}>
                <Iconify
                  icon="solar:pen-new-square-linear"
                  width={16}
                  sx={{ color: "text.tertiary" }}
                />
              </IconButton>
            </Tooltip>
          )}
          {action}
        </Stack>
      </Stack>

      <Collapse in={open} unmountOnExit>
        {/* Two spec tables side by side: their part in the run, and what
            they see. "What they do" is the line under the name. Their part
            uses the editor's own field names, row for row: How they join,
            Kind, When they come in, World actions they may take — an
            approval action there is what makes them an approver (24 Nov) —
            so actions aren't repeated under what they see. */}
        <Box
          sx={{
            px: 2.5,
            pb: 2,
            display: "grid",
            gridTemplateColumns: { xs: "1fr", md: "1fr 1fr" },
            gap: 1.5,
            alignItems: "start",
          }}
        >
          <SpecCard title="Their part">
            {actor.tests && <SpecRow label="Tests">{actor.tests}</SpecRow>}
            {sideOf(actor) === "caller" && (
              <SpecRow label="How they join">
                {CALLER_KINDS.find((k) => k.id === actor.kind)?.label ||
                  "Someone nearby"}
              </SpecRow>
            )}
            {kindValueOf(actor) && (
              <SpecRow label="Kind">
                {KIND_OPTIONS.find((k) => k.id === kindValueOf(actor))?.label}
              </SpecRow>
            )}
            <SpecRow label="When they come in">
              {sideOf(actor) === "caller" && arrivalLineOf(actor)
                ? comes.charAt(0).toUpperCase() + comes.slice(1)
                : `${entry.label} — ${entry.blurb}`}
            </SpecRow>
            {sideOf(actor) !== "caller" && (
              <SpecRow label="World actions they may take">
                {actions.length ? (
                  <Box
                    component="span"
                    sx={{ fontFamily: "ui-monospace, Menlo, monospace" }}
                  >
                    {actions.map((x) => x.name).join(", ")}
                  </Box>
                ) : (
                  "None — they only talk"
                )}
              </SpecRow>
            )}
            {sideOf(actor) === "caller" && actor.voice && (
              <SpecRow label="Voice">
                {[
                  actor.voice.gender,
                  actor.voice.age,
                  actor.voice.accent,
                  `loudness ${(actor.loudness || "automatic").toLowerCase()}`,
                ]
                  .filter(Boolean)
                  .join(" · ")}
              </SpecRow>
            )}
            {/* Holding an approval action (approvals.change, approve_<tool>)
                above makes them an approver: how they answer, and the
                branches to propose. */}
            {approvesOf(actor) && (
              <SpecRow
                label="Approval"
                trailing={
                  onBranches ? (
                    <TextAction onClick={onBranches}>
                      Propose scenarios
                    </TextAction>
                  ) : null
                }
              >
                {actor.timeoutMin
                  ? `Answers within ${actor.timeoutMin} min`
                  : "Answers when asked"}
              </SpecRow>
            )}
            {(actor.traits || []).length > 0 && (
              <SpecRow label="Traits">
                <Stack direction="row" flexWrap="wrap" sx={{ gap: 0.5 }}>
                  {actor.traits.map((t) => (
                    <Box
                      key={t}
                      component="span"
                      sx={{
                        px: 0.75,
                        height: 20,
                        display: "inline-flex",
                        alignItems: "center",
                        border: "1px solid",
                        borderColor: "divider",
                        borderRadius: "4px",
                        typography: "s3",
                        color: "text.secondary",
                      }}
                    >
                      {t}
                    </Box>
                  ))}
                </Stack>
              </SpecRow>
            )}
            {(actor.versions || []).length > 0 && (
              <SpecRow label="Versions">
                <Stack spacing={0.25}>
                  {actor.versions.map((v) => (
                    <Typography
                      key={v.label}
                      sx={{ typography: "s2", color: "text.secondary" }}
                    >
                      <Box
                        component="span"
                        sx={{ fontWeight: 600, color: "text.primary", mr: 1 }}
                      >
                        {v.label}
                      </Box>
                      {v.note}
                    </Typography>
                  ))}
                </Stack>
              </SpecRow>
            )}
          </SpecCard>
          <SpecCard
            title={`What ${actor.speaksAs || actor.name || "they"} sees`}
          >
            {view.sees
              .filter(
                (r) =>
                  /* In Their part already, under the editor's names. */
                  !(sideOf(actor) !== "caller" && r.label === "Can do"),
              )
              .map((r) => (
                <SpecRow key={r.label} label={r.label}>
                  {r.text}
                </SpecRow>
              ))}
            {files.length > 0 && (
              <SpecRow label="Files">
                <ActorFiles
                  files={files}
                  readable={readable}
                  onToggle={onToggleFile}
                />
              </SpecRow>
            )}
            <SpecRow label="Never sees">
              <Stack spacing={0.25}>
                {view.never.map((t) => (
                  <Typography
                    key={t}
                    sx={{ typography: "s2", color: "text.secondary" }}
                  >
                    {t}
                  </Typography>
                ))}
              </Stack>
            </SpecRow>
          </SpecCard>
        </Box>
      </Collapse>
    </Box>
  );
}
/* The short facts on a row, as quiet tags. */
function Tags({ tags }) {
  if (!tags.length) return null;
  return (
    <Stack
      direction="row"
      flexWrap="wrap"
      useFlexGap
      spacing={0.5}
      sx={{ mt: 0.5 }}
    >
      {tags.map((t) => (
        <Box
          key={t}
          component="span"
          sx={{
            px: 0.75,
            height: 20,
            display: "inline-flex",
            alignItems: "center",
            borderRadius: "4px",
            bgcolor: "background.neutral",
            typography: "s3",
            color: "text.secondary",
            whiteSpace: "nowrap",
          }}
        >
          {t}
        </Box>
      ))}
    </Stack>
  );
}
Tags.propTypes = { tags: PropTypes.arrayOf(PropTypes.string) };

/*
  A role with more than one person (Karthik, 26 Nov: "say 3 teammates"). The
  role leads, with how many people and their names; what they all share —
  what they push for and the tags — sits once on the role's line. Opening it
  lists each person with only what differs, and their own edit and remove.
  Add person starts a new actor with the role's details. There is no remove
  for the whole role: people are removed one at a time.
*/
function RoleGroup({ group, tagsFor, renderPerson, onAddPerson }) {
  const [open, setOpen] = useState(false);
  const { people, label } = group;
  const lists = people.map(tagsFor);
  const sharedTags = lists[0].filter((t) => lists.every((l) => l.includes(t)));
  const goals = new Set(people.map((p) => p.goal || ""));
  const sharedGoal = goals.size === 1 ? people[0].goal || "" : "";
  const list = people.map((p) => p.speaksAs || p.name);
  const names = `${list.slice(0, -1).join(", ")} and ${list[list.length - 1]}`;
  return (
    <Box sx={{ borderBottom: "1px solid", borderColor: "divider" }}>
      <Stack
        direction="row"
        alignItems="center"
        spacing={1.5}
        sx={{ px: 2.5, py: 1 }}
      >
        <Box
          flex={1}
          minWidth={0}
          {...clickable(() => setOpen((o) => !o), { expanded: open })}
          sx={{ ...focusRing, cursor: "pointer" }}
        >
          <Stack direction="row" alignItems="center" spacing={0.75}>
            <Typography noWrap sx={{ typography: "s2_1", fontWeight: 600 }}>
              {label}
              <Box
                component="span"
                sx={{ color: "text.secondary", fontWeight: 400 }}
              >
                {` · ${people.length} people`}
              </Box>
            </Typography>
            <Iconify
              icon="solar:alt-arrow-down-linear"
              width={14}
              sx={{
                color: "text.tertiary",
                flexShrink: 0,
                transform: open ? "rotate(180deg)" : "none",
                transition: "transform 150ms ease-out",
              }}
            />
          </Stack>
          <Typography noWrap sx={{ typography: "s3", color: "text.secondary" }}>
            {sharedGoal ? `Pushes for: ${sharedGoal}` : `Speak as ${names}`}
          </Typography>
          {sharedGoal && !open && (
            <Typography
              noWrap
              sx={{ typography: "s3", color: "text.tertiary" }}
            >
              {`Speak as ${names}`}
            </Typography>
          )}
          <Tags tags={sharedTags} />
        </Box>
        {onAddPerson && (
          <TextAction onClick={onAddPerson}>Add person</TextAction>
        )}
      </Stack>
      <Collapse in={open} unmountOnExit>
        <Box
          sx={{
            ml: 5,
            mr: 2.5,
            mb: 1.5,
            border: "1px solid",
            borderColor: "divider",
            borderRadius: "8px",
            overflow: "hidden",
            "& .actor-row-head": { px: 1.5 },
          }}
        >
          {people.map((p) =>
            renderPerson(p, { sharedTags, sharedGoal: !!sharedGoal }),
          )}
        </Box>
      </Collapse>
    </Box>
  );
}
RoleGroup.propTypes = {
  group: PropTypes.object.isRequired,
  tagsFor: PropTypes.func.isRequired,
  renderPerson: PropTypes.func.isRequired,
  onAddPerson: PropTypes.func,
};

/* A titled key-value table — the spec-sheet layout used across the env. */
function SpecCard({ title, children }) {
  return (
    <Box
      sx={{
        border: "1px solid",
        borderColor: "divider",
        borderRadius: "8px",
        overflow: "hidden",
      }}
    >
      <Typography
        sx={{
          typography: "s2",
          fontWeight: 600,
          px: 1.5,
          py: 1,
          borderBottom: "1px solid",
          borderColor: "divider",
        }}
      >
        {title}
      </Typography>
      {children}
    </Box>
  );
}
SpecCard.propTypes = { title: PropTypes.string, children: PropTypes.node };

function SpecRow({ label, children, trailing = null }) {
  return (
    <Box
      sx={{
        display: "grid",
        gridTemplateColumns: "112px minmax(0, 1fr) auto",
        columnGap: 1.5,
        alignItems: "baseline",
        px: 1.5,
        py: 0.875,
        "& + &": { borderTop: "1px solid", borderColor: "divider" },
      }}
    >
      <Typography sx={{ typography: "s2", color: "text.tertiary" }}>
        {label}
      </Typography>
      <Box sx={{ typography: "s2", color: "text.secondary", minWidth: 0 }}>
        {children}
      </Box>
      <Box>{trailing}</Box>
    </Box>
  );
}
SpecRow.propTypes = {
  label: PropTypes.string,
  children: PropTypes.node,
  trailing: PropTypes.node,
};

/* Grading-only files: answers for the grader, never for a simulated person. */
const gradingOnly = (f) =>
  f.role === "evaluation-reference" || f.visibility === "evaluator";

/* Which of the world's files this actor can read. Files are added in
   Sources; here they're only granted. */
function ActorFiles({ files, readable, onToggle }) {
  return (
    <Box>
      <Stack direction="row" flexWrap="wrap" sx={{ gap: 0.75 }}>
        {files.map((f) => {
          const locked = gradingOnly(f);
          const on = !locked && readable.includes(f.id);
          return (
            <Tooltip
              key={f.id}
              arrow
              title={
                locked
                  ? "Grading only — never shown to a simulated person."
                  : on
                    ? "They can read it. Click to take it away."
                    : "Click to let them read it."
              }
            >
              <Box
                component="button"
                type="button"
                disabled={locked || !onToggle}
                onClick={() => onToggle?.(f.id)}
                sx={{
                  display: "inline-flex",
                  alignItems: "center",
                  gap: 0.5,
                  px: 0.875,
                  height: 24,
                  border: "1px solid",
                  borderColor: on ? "text.secondary" : "divider",
                  borderRadius: "6px",
                  bgcolor: on ? "action.selected" : "transparent",
                  color: locked ? "text.disabled" : "text.primary",
                  cursor: locked || !onToggle ? "default" : "pointer",
                  font: "inherit",
                  typography: "s3",
                }}
              >
                <Iconify
                  icon={
                    locked
                      ? "solar:lock-keyhole-linear"
                      : on
                        ? "solar:check-circle-linear"
                        : "solar:document-linear"
                  }
                  width={12}
                />
                {f.name}
              </Box>
            </Tooltip>
          );
        })}
      </Stack>
    </Box>
  );
}
ActorFiles.propTypes = {
  files: PropTypes.array,
  readable: PropTypes.array,
  onToggle: PropTypes.func,
};

ActorRow.propTypes = {
  env: PropTypes.object,
  envState: PropTypes.object,
  cast: PropTypes.array,
  files: PropTypes.array,
  readable: PropTypes.array,
  onToggleFile: PropTypes.func,
  actor: PropTypes.object,
  action: PropTypes.node,
  onEdit: PropTypes.func,
  onReset: PropTypes.func,
  onBranches: PropTypes.func,
  tags: PropTypes.arrayOf(PropTypes.string),
  group: PropTypes.shape({
    sharedTags: PropTypes.arrayOf(PropTypes.string),
    sharedGoal: PropTypes.bool,
  }),
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
