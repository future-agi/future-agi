import PropTypes from "prop-types";
import { Children, useState } from "react";
import { useNavigate } from "react-router-dom";
import { useSnackbar } from "notistack";
import {
  Box,
  Button,
  Collapse,
  Stack,
  Typography,
  ButtonBase,
} from "@mui/material";
import Iconify from "src/components/iconify";
import { currentEnvSnapshot, nextSnapshotLabel } from "../_mock/envSnapshot";
import {
  agentVersions,
  isPendingDraft,
  pendingEnvChanges,
  productionAgentVersion,
} from "../_mock/versions";
import {
  rebuildEnvironmentPatch,
  rebuildScenariosPatch,
  reopenCardPatch,
  reopenScenariosPatch,
  scenarioStepOf,
  skipCardPatch,
  skipScenariosPatch,
  versionCardOf,
} from "../_mock/versionCard";
import { plural } from "../_mock/nudgeEffects";
import { scenarioTitle } from "../_mock/scenarios";
import { runDefaultsOf } from "../_mock/rsiModel";
import { paths } from "src/routes/paths";
import { useConnectedAgents } from "../store";
import { envAgentKey } from "../_mock/agentRegistry";
import { buildPresetFor } from "../agents/agentViewUtils";
import BuildEnvironmentDialog from "../agents/BuildEnvironmentDialog";
import { focusRing } from "../components/clickable";

const MONO = "ui-monospace, Menlo, monospace";
const MAX_LISTED = 5;
const btn = { flexShrink: 0 };
const quiet = { ...btn, color: "text.secondary" };
const outlined = { ...btn, color: "text.primary", borderColor: "divider" };

/*
  One layout for every step of an agent change — the agent's card and the
  scenario step after it, on Overview and in Needs input alike:

    icon  What changed — in one line                    [Skip] [Rebuild …]
          Nothing changes until you choose: what each button does.
          Details ▾   (what's in the agent, the world, the scenarios — closed)

  The buttons are the input. Anything else that needs an answer (a changed
  rule, a channel move) sits under the line as its own row, closed until
  opened; reference lists never open on their own.
*/
function ChangeRow({
  icon,
  title,
  summary,
  hint,
  actions,
  details,
  children,
  px = 2.5,
}) {
  const [open, setOpen] = useState(false);
  return (
    <Box sx={{ px, py: 1.5 }}>
      <Stack direction="row" alignItems="flex-start" spacing={1.5}>
        <Iconify
          icon={icon}
          width={16}
          sx={{ color: "text.secondary", flexShrink: 0, mt: 0.25 }}
        />
        <Box flex={1} minWidth={0}>
          <Typography sx={{ typography: "s2", fontWeight: 600 }}>
            {title}
            {summary && (
              <Box
                component="span"
                sx={{ fontWeight: 400, color: "text.secondary" }}
              >{` — ${summary}`}</Box>
            )}
          </Typography>
          {(hint || details) && (
            <Stack
              direction="row"
              alignItems="baseline"
              spacing={1}
              sx={{ mt: 0.25 }}
            >
              {hint && (
                <Typography
                  sx={{ typography: "s3", color: "text.tertiary", minWidth: 0 }}
                >
                  {hint}
                </Typography>
              )}
              {details && (
                <ButtonBase
                  type="button"
                  aria-expanded={open}
                  onClick={() => setOpen((o) => !o)}
                  sx={{
                    ...focusRing,
                    p: 0,
                    border: 0,
                    bgcolor: "transparent",
                    cursor: "pointer",
                    font: "inherit",
                    flexShrink: 0,
                    typography: "s3",
                    fontWeight: 600,
                    color: "text.primary",
                    display: "inline-flex",
                    alignItems: "center",
                    gap: 0.25,
                    "&:hover": { textDecoration: "underline" },
                  }}
                >
                  {open ? "Hide details" : "Details"}
                  <Iconify
                    icon={
                      open
                        ? "solar:alt-arrow-up-linear"
                        : "solar:alt-arrow-down-linear"
                    }
                    width={12}
                  />
                </ButtonBase>
              )}
            </Stack>
          )}
        </Box>
        {actions && (
          <Stack direction="row" spacing={1} sx={{ flexShrink: 0 }}>
            {actions}
          </Stack>
        )}
      </Stack>
      {details && (
        <Collapse in={open} unmountOnExit>
          <Box sx={{ pl: 3.5, mt: 1 }}>{details}</Box>
        </Collapse>
      )}
      {Children.toArray(children).length > 0 && (
        <Stack spacing={1} sx={{ pl: 3.5, mt: 1.25 }}>
          {children}
        </Stack>
      )}
    </Box>
  );
}
ChangeRow.propTypes = {
  icon: PropTypes.string.isRequired,
  title: PropTypes.node.isRequired,
  summary: PropTypes.node,
  hint: PropTypes.node,
  actions: PropTypes.node,
  details: PropTypes.node,
  children: PropTypes.node,
  px: PropTypes.number,
};

/* The details: label | value rows, one hairline frame. */
function DetailRows({ rows }) {
  const shown = rows.filter(Boolean);
  if (!shown.length) return null;
  return (
    <Box
      sx={{
        border: "1px solid",
        borderColor: "divider",
        borderRadius: 1,
        "& > * + *": { borderTop: "1px solid", borderColor: "divider" },
      }}
    >
      {shown.map(({ label, value }) => (
        <Stack
          key={label}
          direction="row"
          spacing={1.5}
          sx={{ px: 1.5, py: 0.875 }}
        >
          <Typography
            sx={{
              typography: "s3",
              color: "text.tertiary",
              width: 128,
              flexShrink: 0,
              lineHeight: 1.7,
            }}
          >
            {label}
          </Typography>
          <Box flex={1} minWidth={0}>
            {value}
          </Box>
        </Stack>
      ))}
    </Box>
  );
}
DetailRows.propTypes = { rows: PropTypes.array.isRequired };

/* A short list inside a detail row — capped, with "+N more". */
function Lines({ items, render }) {
  return (
    <Stack spacing={0.25}>
      {items.slice(0, MAX_LISTED).map((it, i) => (
        <Typography
          key={it.key || i}
          sx={{ typography: "s2", color: "text.secondary" }}
        >
          {render(it)}
        </Typography>
      ))}
      {items.length > MAX_LISTED && (
        <Typography
          sx={{ typography: "s3", color: "text.tertiary" }}
        >{`+${items.length - MAX_LISTED} more`}</Typography>
      )}
    </Stack>
  );
}
Lines.propTypes = {
  items: PropTypes.array.isRequired,
  render: PropTypes.func.isRequired,
};

const Mono = ({ children }) => (
  <Box component="span" sx={{ fontFamily: MONO, color: "text.primary" }}>
    {children}
  </Box>
);
Mono.propTypes = { children: PropTypes.node };

/* "1 tool added, 1 changed, 1 removed · 1 rule changed" */
function summaryOf(card) {
  const adds = card.capabilities.filter((n) => n.kind === "add").length;
  const changed = card.capabilities.filter(
    (n) => n.kind === "behaviour",
  ).length;
  const tools = [
    [adds, "added"],
    [changed, "changed"],
    [card.removals.length, "removed"],
  ].filter(([n]) => n);
  const toolPart = tools
    .map(([n, verb], i) =>
      i === 0 ? `${plural(n, "tool")} ${verb}` : `${n} ${verb}`,
    )
    .join(", ");
  const rulePart = card.rules.length
    ? `${plural(card.rules.length, "rule")} changed`
    : "";
  return (
    [toolPart, rulePart].filter(Boolean).join(" · ") ||
    "nothing in the world needs to change"
  );
}

/* Scenario rows by id, titled. */
const rowsOf = (envState, ids = []) =>
  ids
    .map((id) => (envState?.scenarios || []).find((s) => s.id === id))
    .filter(Boolean)
    .map((s) => ({
      key: s.id,
      title: scenarioTitle(s.title) || s.name || s.id,
    }));

/*
  The one card a new agent version raises here (see _mock/versionCard.js):
  what changed, Rebuild / Skip, and — closed under Details — what's in the
  agent, the world handlers one Rebuild applies as a single environment
  version, and the scenarios that need a removed tool. Rule changes each need
  an answer, so they sit under the line as rows of their own. `renderNudge`
  draws a rule's own question — passed in so this file doesn't import
  NudgeCard (which renders this card).
*/
/* When the removed tools take at least this share of the scenarios with them,
   the agent looks like a different one — offer a new environment instead. */
const DIFFERENT_AGENT_SHARE = 0.5;
/* The scenario count a new environment starts with — the build screen's default. */
const NEW_ENV_SCENARIOS = 10;

export default function AgentVersionCard({
  env,
  envState,
  patch,
  renderNudge,
  px = 2.5,
}) {
  const { enqueueSnackbar } = useSnackbar();
  const [keepHere, setKeepHere] = useState(false);
  const card = versionCardOf(envState);
  if (!card) return null;
  /* Backward compatible? Update this environment. If most scenarios can't
     apply any more, say so and offer a new environment — built for you, or
     on your own. */
  const active = (envState?.scenarios || []).filter(
    (r) => !r.archived && !r.retired,
  ).length;
  const lost = new Set(card.removals.flatMap((n) => n.scenarioIds || [])).size;
  const different =
    !keepHere &&
    card.status === "open" &&
    active > 0 &&
    lost / active >= DIFFERENT_AGENT_SHARE;
  const nextLabel = nextSnapshotLabel(env, envState);
  const currentLabel = currentEnvSnapshot(env, envState)?.label;
  const waiting = pendingEnvChanges(envState).length;
  const since = card.from ? ` since ${card.from}` : "";

  if (card.status === "skipped") {
    return (
      <ChangeRow
        px={px}
        icon="solar:minus-circle-linear"
        title={`Agent ${card.newest}'s changes were skipped`}
        summary="the environment stays as it is"
        hint="A newer agent version brings them back."
        actions={
          patch && (
            <Button
              size="small"
              variant="outlined"
              onClick={() => patch(reopenCardPatch(envState))}
              sx={outlined}
            >
              Review again
            </Button>
          )
        }
      />
    );
  }

  const rebuild = () => {
    const out = rebuildEnvironmentPatch(env, envState, card);
    if (!out) return;
    patch(out.patch);
    enqueueSnackbar(
      `Environment ${nextLabel} — ${out.summary}${out.alsoSaved ? `, with your ${plural(out.alsoSaved, "unsaved edit")}` : ""}. Roll back from History if it isn't right.`,
      { variant: "success" },
    );
  };
  const skip = () => {
    patch(skipCardPatch(envState, card));
    enqueueSnackbar(
      `Skipped agent ${card.newest}'s changes — the environment stays as it is.`,
      { variant: "info" },
    );
  };
  const canRebuild = card.capabilities.length > 0;
  const questions = [...card.rules, ...card.other];

  const hint = !patch
    ? null
    : canRebuild
      ? `Nothing changes until you choose: Rebuild environment saves the world as environment ${nextLabel}${waiting ? ` with your ${plural(waiting, "unsaved edit")}` : ""}; Skip keeps ${currentLabel || "it"} as it is.`
      : questions.length
        ? "Nothing in the world needs to change. Answer each change below, or Skip to keep everything as it is."
        : "Nothing in the world needs to change.";

  const details = (
    <DetailRows
      rows={[
        card.changes.length > 0 && {
          label: "In the agent",
          value: (
            <Lines
              items={card.changes}
              render={(c) => (
                <>
                  {c.title}
                  {card.versions.length > 1 && (
                    <Box
                      component="span"
                      sx={{ color: "text.tertiary" }}
                    >{` · ${c.version}`}</Box>
                  )}
                </>
              )}
            />
          ),
        },
        canRebuild && {
          label: "World handlers",
          value: (
            <Lines
              items={card.capabilities.map((n) => ({ ...n, key: n.id }))}
              render={(n) => (
                <>
                  <Mono>{n.capability}</Mono>
                  {n.kind === "add"
                    ? " — new handler"
                    : " — follows the new behaviour"}
                </>
              )}
            />
          ),
        },
        card.removals.length > 0 && {
          label: "Removed",
          value: (
            <Lines
              items={card.removals.map((n) => ({ ...n, key: n.id }))}
              render={(n) => (
                <>
                  <Mono>{n.capability}</Mono>
                  {` — ${plural((n.scenarioIds || []).length, "scenario")} need${(n.scenarioIds || []).length === 1 ? "s" : ""} it; handled in the scenario step`}
                </>
              )}
            />
          ),
        },
        card.rules.length > 0 && {
          label: "Rules",
          value: (
            <Typography sx={{ typography: "s2", color: "text.secondary" }}>
              Rules the tests check never change on their own — each needs your
              answer.
            </Typography>
          ),
        },
      ]}
    />
  );

  return (
    <ChangeRow
      px={px}
      icon="solar:refresh-circle-linear"
      title={`Agent ${card.newest} changed${since}${card.versions.length > 1 ? ` (${card.versions.length} versions)` : ""}`}
      summary={summaryOf(card)}
      hint={hint}
      details={details}
      actions={
        patch && (
          <>
            <Button size="small" onClick={skip} sx={quiet}>
              Skip
            </Button>
            {canRebuild && (
              <Button
                size="small"
                variant="contained"
                color="primary"
                onClick={rebuild}
                sx={btn}
              >
                Rebuild environment
              </Button>
            )}
          </>
        )
      }
    >
      {different && (
        <Box
          sx={{
            border: "1px solid",
            borderColor: "divider",
            borderRadius: 1,
            px: 1.5,
            py: 1,
          }}
        >
          <Typography sx={{ typography: "s2", fontWeight: 600 }}>
            {`Looks like a different agent — ${lost} of ${active} scenarios need tools ${card.newest} no longer has.`}
          </Typography>
          <Typography
            sx={{ typography: "s3", color: "text.tertiary", mt: 0.25 }}
          >
            A new environment fits it better. This one stays as it is, with its
            runs.
          </Typography>
          <Stack direction="row" spacing={1} sx={{ mt: 1 }}>
            <NewEnvironmentActions
              env={env}
              envState={envState}
              version={card.newest}
            />
            <Button size="small" onClick={() => setKeepHere(true)} sx={quiet}>
              Keep updating this one
            </Button>
          </Stack>
        </Box>
      )}
      {questions.length > 0 && renderNudge && (
        <Box>
          <Typography
            sx={{ typography: "s3", color: "text.tertiary", mb: 0.5 }}
          >
            {`${plural(questions.length, "change")} also need${questions.length === 1 ? "s" : ""} your answer — open ${questions.length === 1 ? "it" : "each"} to answer`}
          </Typography>
          <Box
            sx={{
              border: "1px solid",
              borderColor: "divider",
              borderRadius: 1,
              overflow: "hidden",
              "& > * + *": { borderTop: "1px solid", borderColor: "divider" },
            }}
          >
            {questions.map((n) => (
              <Box key={n.id}>{renderNudge(n)}</Box>
            ))}
          </Box>
        </Box>
      )}
    </ChangeRow>
  );
}
AgentVersionCard.propTypes = {
  env: PropTypes.object.isRequired,
  envState: PropTypes.object.isRequired,
  patch: PropTypes.func,
  renderNudge: PropTypes.func,
  px: PropTypes.number,
};

/* The second step: scenarios for what the environment now has — same layout. */
export function ScenarioStepCard({ env, envState, patch, px = 2.5 }) {
  const { enqueueSnackbar } = useSnackbar();
  const step = scenarioStepOf(envState);
  if (!step) return null;

  if (step.status === "skipped") {
    return (
      <ChangeRow
        px={px}
        icon="solar:minus-circle-linear"
        title="The scenario update was skipped"
        summary="the scenario set stays as it is"
        actions={
          patch && (
            <Button
              size="small"
              variant="outlined"
              onClick={() => patch(reopenScenariosPatch(envState))}
              sx={outlined}
            >
              Review again
            </Button>
          )
        }
      />
    );
  }

  const rebuild = () => {
    const out = rebuildScenariosPatch(env, envState, step);
    patch(out.patch);
    enqueueSnackbar(
      [
        out.added && `${plural(out.added, "new scenario")}`,
        out.stale && `${plural(out.stale, "scenario")} stale`,
      ]
        .filter(Boolean)
        .join(" · ") || "Scenario set updated",
      { variant: "success" },
    );
  };

  const summary = [
    step.adds.length > 0 &&
      `${plural(step.newRows, "new scenario")} for ${step.adds.map((a) => a.tool).join(", ")}`,
    step.staleRows > 0 && `${step.staleRows} stale`,
  ]
    .filter(Boolean)
    .join(" · ");
  const does = [
    step.newRows > 0 &&
      `writes ${step.newRows}${step.price ? ` (${step.price.line})` : ""}`,
    step.staleRows > 0 && `marks ${step.staleRows} stale`,
  ]
    .filter(Boolean)
    .join(" and ");

  const details = (
    <DetailRows
      rows={[
        step.adds.length > 0 && {
          label: "New scenarios",
          value: (
            <Lines
              items={step.adds.map((a) => ({ ...a, key: a.tool }))}
              render={(a) => (
                <>
                  {"3 for "}
                  <Mono>{a.tool}</Mono>
                  {" — the world now has its handler"}
                </>
              )}
            />
          ),
        },
        ...step.removals.map((n) => ({
          label: `Need ${n.capability}`,
          value: (
            <Stack spacing={0.5}>
              <Lines
                items={rowsOf(envState, n.scenarioIds)}
                render={(s) => s.title}
              />
              <Typography sx={{ typography: "s3", color: "text.tertiary" }}>
                Stale ones still run, but count toward no decision until you
                update or archive them.
              </Typography>
            </Stack>
          ),
        })),
      ]}
    />
  );

  return (
    <ChangeRow
      px={px}
      icon="solar:list-check-linear"
      title="Update the scenarios"
      summary={summary}
      hint={
        patch
          ? `Nothing changes until you choose: Rebuild scenarios ${does}; Skip keeps the scenario set as it is.`
          : null
      }
      details={details}
      actions={
        patch && (
          <>
            <Button
              size="small"
              onClick={() => patch(skipScenariosPatch(envState))}
              sx={quiet}
            >
              Skip
            </Button>
            <Button
              size="small"
              variant="contained"
              color="primary"
              onClick={rebuild}
              sx={btn}
            >
              Rebuild scenarios
            </Button>
          </>
        )
      }
    />
  );
}
ScenarioStepCard.propTypes = {
  env: PropTypes.object.isRequired,
  envState: PropTypes.object.isRequired,
  patch: PropTypes.func,
  px: PropTypes.number,
};

/*
  A change too big for this environment: "Create a new environment for me"
  builds one straight away (or runs `onForMe` when the caller has its own way,
  like a fork), and "I'll do it" opens the normal build dialog for the agent —
  name, version, how many scenarios. The agent comes from Agents, else from
  this environment's own copy of it, so neither button goes missing. A draft
  is never what a new environment tests: it starts from production.
*/
export function NewEnvironmentActions({
  env,
  envState,
  version,
  onForMe,
  forMeContained = false,
}) {
  const navigate = useNavigate();
  const { agents } = useConnectedAgents();
  const [open, setOpen] = useState(false);
  const key = envAgentKey(envState?.agent, env.id);
  const production = productionAgentVersion(envState)?.label;
  const record =
    agents.find((a) => a.key === key) ||
    (envState?.agent
      ? {
          ...envState.agent,
          key,
          name: envState.agent.name || `${env.name} agent`,
          versions: agentVersions(envState).map((v) => ({
            ...v,
            pendingDraft: isPendingDraft(envState, v.label),
          })),
          latest: version,
          production,
        }
      : null);
  const tested =
    version && !isPendingDraft(envState, version) ? version : production;
  const build = (choice) =>
    record &&
    navigate(paths.dashboard.simulate.environmentBuild, {
      state: { presetSource: buildPresetFor(record, choice) },
    });
  const forMe =
    onForMe ||
    (() =>
      build({
        version: tested,
        count: NEW_ENV_SCENARIOS,
        parallel: runDefaultsOf(envState).conversationsAtOnce,
        name: `${env.name} · ${tested}`,
      }));
  const doIt = () =>
    record ? setOpen(true) : navigate(paths.dashboard.simulate.environmentNew);
  return (
    <>
      <Button
        size="small"
        variant="outlined"
        onClick={doIt}
        sx={{ ...btn, color: "text.primary", borderColor: "divider" }}
      >
        I&apos;ll do it
      </Button>
      <Button
        size="small"
        variant={forMeContained ? "contained" : "outlined"}
        color="primary"
        onClick={forMe}
        disabled={!onForMe && !record}
        sx={
          forMeContained
            ? btn
            : { ...btn, color: "text.primary", borderColor: "divider" }
        }
      >
        Create a new environment for me
      </Button>
      {record && (
        <BuildEnvironmentDialog
          open={open}
          onClose={() => setOpen(false)}
          agent={record}
          defaultVersion={tested}
          channel={env?.surface || null}
          defaultParallel={runDefaultsOf(envState).conversationsAtOnce}
          onBuild={(choice) => {
            setOpen(false);
            build(choice);
          }}
        />
      )}
    </>
  );
}
NewEnvironmentActions.propTypes = {
  env: PropTypes.object.isRequired,
  envState: PropTypes.object,
  version: PropTypes.string,
  onForMe: PropTypes.func,
  forMeContained: PropTypes.bool,
};
