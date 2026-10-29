import PropTypes from "prop-types";
import { useEffect, useRef, useState } from "react";
import { Box, Button, Stack, Tooltip, Typography } from "@mui/material";
import Iconify from "src/components/iconify";
import { useConnectedAgents, useSimStore } from "../store";
import {
  FRAMEWORK_META,
  PROVIDER_LABELS,
  agentNeedsRuntime,
  agentSourceOf,
  frameworkIdOf,
  sourceKindOf,
} from "../_mock/agentRegistry";
import { needsLiveConsent, platformTestingOf } from "../_mock/rsiModel";
import { elevenlabsVersioningOn } from "../_mock/improvementPolicy";
import { replicationOf } from "../_mock/replication";
import { instrumentorFor } from "../_mock/traceAiCatalog";
import {
  installationOfRepo,
  repoNameOf,
  useIntegrations,
  scannedTracingOf,
  setTracing,
  telemetryDraft,
  updatePr,
} from "../_mock/integrations";
import OpenPrDialog from "../integrations/OpenPrDialog";
import { likelyProjectFor, projectLinkOf } from "../_mock/projectLinks";
import { observeProjectOf, pendingProjectOf } from "./observeProject";
import { resolveObserve } from "../_mock/connectAgent";
import { ObserveProjectStep } from "./connect/ConnectAboutStep";
import { showsSetup, testedInLine } from "./agentViewUtils";
import AgentFoundReview from "./AgentFoundReview";
import SendTracesSetup, { FirstTraceWait } from "./connect/SendTracesSetup";
import { isCodeAgent, needsFoundCheck } from "./foundFacts";
import {
  checksConnection,
  connectionStateOf,
} from "../_mock/connectionCheck";

/**
 * Next steps — what a newly connected agent is for, as two equal ways
 * forward (team feedback: after connecting, the next step is getting traces
 * or building an environment, not one prominent "Build environment"):
 *
 *   In production   get its traces into an Observe project — Error Feed
 *                   finds issues in real calls, and Fix can repair them
 *   In simulation   build an environment — scenarios run against simulated
 *                   tools and data, and improvements try fixes before they ship
 *
 * Either can come first; each says what it unlocks and, once done, where it
 * stands. Under them, only what blocks a run (what we read from code to
 * confirm, where it runs, consent, versioning); the constraints, already at
 * safe defaults, are a footnote.
 *
 * Shown for an agent connected through the one-step connect, or any agent
 * not tested anywhere yet (`showsSetup`), until both are done or it's hidden.
 * A code agent's "Check what we found" is needed before it runs or is
 * improved, so the card can't be hidden while it's open.
 */
export default function AgentSetupCard({
  agent,
  env,
  onTab,
  onBuild,
  onSaveValues,
}) {
  const { agents, state } = useConnectedAgents();
  const customLimits = !!state?.agentLimits?.[agent?.key];
  const { dispatch } = useSimStore();
  /* The traces setup, opened in place under the two paths. */
  const [choosing, setChoosing] = useState(false);
  const [observeChoice, setObserveChoice] = useState(null);
  /* A new project: the instrumentor picked, and the wait for its first trace. */
  const [instrumentorPick, setInstrumentorPick] = useState(null);
  const [traceWait, setTraceWait] = useState("idle");
  useEffect(() => {
    if (traceWait !== "listening") return undefined;
    /* Demo: the first trace arrives a few seconds after listening starts. */
    const t = setTimeout(() => setTraceWait("received"), 4500);
    return () => clearTimeout(t);
  }, [traceWait]);
  const [reviewing, setReviewing] = useState(false);
  const integrations = useIntegrations();
  /* The tracing PR's draft, while the PR dialog is open. */
  const [prDialog, setPrDialog] = useState(null);
  /* Waiting on a project with nothing in it yet: its first trace settles it. */
  const pendingId = agent?.values?.observePending?.project || null;
  const saveObserveRef = useRef(null);
  useEffect(() => {
    if (traceWait !== "received" || choosing || !pendingId) return;
    saveObserveRef.current?.(
      { mode: "existing", id: pendingId },
      { firstTrace: true },
    );
    setTraceWait("idle");
  }, [traceWait, choosing, pendingId]);
  if (!showsSetup(agent)) return null;
  const v = agent.values || {};

  const source = agentSourceOf(agent);
  const kind = sourceKindOf(agent);
  const readFrom =
    source.kind === "repo"
      ? "the code"
      : source.kind === "framework"
        ? "the adapter"
        : source.kind === "platform"
          ? source.label
          : null;
  /* One Observe project per agent. */
  const project = observeProjectOf(agent, state, agents);
  /* Settled only when someone chose it — or a platform's call logs decide it.
     A new project isn't settled by naming it: Observe only has it once its
     first trace arrives (dev creates projects from incoming spans), so until
     then it's pending. */
  const tracesDone =
    !!project &&
    (!!v.observeChosenAt ||
      (source.kind === "platform" && !!v.observability?.on));
  /* A hosted agent's calls are pulled with its key — there's no first trace
     to wait for, so it's never pending. */
  const pulls = kind === "platform";
  /* Only while the project is still this agent's — another agent may have taken it. */
  const pending =
    !tracesDone && !pulls ? pendingProjectOf(agent, state, agents) : null;
  const provider = PROVIDER_LABELS[v.provider] || source.label;
  /* What the repository scan says about tracing — evidence, not a name match. */
  const scan = source.kind === "repo" ? scannedTracingOf(agent) : null;
  const envDone = (agent.usedIn || []).length > 0;
  /* Unconfirmed: the project it's linked to for now, else the existing one
     its traces most likely land in — suggested before a new, empty one. */
  const suggestedBase =
    !tracesDone &&
    !pending &&
    (scan?.project
      ? { id: scan.project, name: scan.project, evidence: scan.file }
      : project
        ? { id: project.project, name: project.name }
        : /* A repo agent's suggestion comes from its code or nothing — never a name match. */
          source.kind === "repo"
          ? null
          : (() => {
              const p = likelyProjectFor(agent, state, agents);
              return p ? { id: p.id, name: p.name, traces: p.traces } : null;
            })());
  /* A suggestion another agent owns is reviewed, not confirmed in one click. */
  const suggestedOwner = suggestedBase
    ? (() => {
        const l = projectLinkOf(suggestedBase.id, state, agents);
        return l.status === "linked" && l.agentKey !== agent.key
          ? l.agent?.name || "another agent"
          : null;
      })()
    : null;
  const suggested = suggestedBase
    ? { ...suggestedBase, owner: suggestedOwner }
    : null;
  /*
    Linking a project. An existing one (or a new one whose first trace has
    arrived) settles it; a new one with nothing in it yet is linked and left
    pending, so nothing claims Error Feed is watching an empty project.
  */
  const saveObserve = (
    choice = observeChoice,
    { firstTrace = false, pendingVia: via = null, pr = null } = {},
  ) => {
    const chosen = resolveObserve(choice, null, agent.name);
    /* Re-picking its own pending project keeps waiting — it still has no trace. */
    const pendingVia = pulls
      ? null
      : via ||
        (!firstTrace && pending && chosen.id === pending.project
          ? pending.via || "manual"
          : null);
    Object.entries(state?.projectLinks || {})
      .filter(([p, k]) => k === agent.key && p !== chosen.id)
      .forEach(([p]) =>
        dispatch({ type: "linkProjectAgent", project: p, agentKey: null }),
      );
    dispatch({
      type: "linkProjectAgent",
      project: chosen.id,
      agentKey: agent.key,
    });
    setChoosing(false);
    setTraceWait("idle");
    if (pendingVia) {
      onSaveValues(
        {
          observePending: {
            project: chosen.id,
            since: new Date().toISOString(),
            via: pendingVia,
          },
          observeChosenAt: null,
          ...(pr && { tracingPr: pr }),
        },
        pr
          ? `Opened PR #${pr.number} on ${pr.repo} — ${chosen.id} links when its first trace arrives.`
          : `${chosen.id} is linked — it settles when its first trace arrives.`,
      );
      return;
    }
    const at = new Date().toISOString();
    /* The first trace means tracing is on — Fix and improvements read it from here. */
    if (firstTrace && source.viaApp)
      setTracing(agent.key, {
        on: true,
        since: at,
        project: chosen.id,
        ...(v.tracingPr && {
          prNumber: v.tracingPr.number,
          repo: v.tracingPr.repo,
        }),
      });
    /* A hosted agent: observability on — its calls come in with the key it already has. */
    if (pulls) {
      onSaveValues(
        {
          observeChosenAt: at,
          observePending: null,
          observability: {
            ...(v.observability || {}),
            on: true,
            since: at,
            project: chosen.id,
          },
        },
        `Observability on — ${provider} calls are pulled into ${chosen.id}, and Error Feed watches them.`,
      );
      return;
    }
    onSaveValues(
      {
        observeChosenAt: at,
        observePending: null,
        ...(firstTrace &&
          !source.viaApp && {
            observability: {
              ...(v.observability || {}),
              on: true,
              since: at,
              project: chosen.id,
            },
          }),
      },
      firstTrace
        ? `First trace in ${chosen.id} — Error Feed watches its traces now.`
        : `Linked to ${chosen.id} — Error Feed watches its traces.`,
    );
  };
  saveObserveRef.current = saveObserve;
  const consent =
    needsLiveConsent(agent) && !replicationOf(agent, env).needsConsent;

  /* How its traces reach Observe, by where it lives. */
  const sendsCode = ["repo", "framework", "upload", "endpoint"].includes(kind);
  const tracesHow =
    kind === "platform"
      ? `Enable observability: its ${provider} calls are pulled with the key it already has — nothing to install. Choose the Observe project they land in.`
      : kind === "builtin"
        ? "Choose the Observe project its production calls land in."
        : scan && !scan.project
          ? `No traceAI in its code yet — we checked ${scan.entry || "its entry point"} and what it imports. Add it, or link a project it already sends to.`
          : "Add traceAI to its code, or link the project it already sends to.";

  /* What blocks a run — only what applies. */
  const foundChecked = !!v.foundCheckedAt;
  const foundNeeded = needsFoundCheck(agent);
  const gates = [
    isCodeAgent(agent) && {
      id: "found",
      done: foundChecked && !reviewing,
      title: "Check what we found",
      body: foundChecked
        ? "Entry point, prompts, tools, model, how to run it and its tests — confirmed."
        : `What we read from ${readFrom || "the code"}: confirm or correct each item, say which tools must never run for real and which test credentials to use.`,
      action: reviewing
        ? null
        : {
            label: foundChecked ? "Review" : "Check",
            onClick: () => setReviewing(true),
          },
      panel: reviewing && (
        <Box sx={{ pl: 4.25, pr: 2, pb: 1.5 }}>
          <AgentFoundReview
            agent={agent}
            onCancel={() => setReviewing(false)}
            onSave={(patch, message) => {
              onSaveValues(patch, message);
              setReviewing(false);
            }}
          />
        </Box>
      ),
    },
    /* The connection check: it starts, replies, keeps the conversation, its
       tools reach the test world, it cancels — a draft until it passes. */
    checksConnection(agent) && {
      id: "connection",
      done: connectionStateOf(agent).state === "ready",
      title: "Test the connection",
      body:
        connectionStateOf(agent).state === "ready"
          ? `Every check passed — ${connectionStateOf(agent).line.replace(/^Tested /, "tested ").replace(/\.$/, "")}.`
          : "Confirm how we start it and talk to it, then check it starts, replies, keeps the conversation, reaches the test world's tools and cancels.",
      action: {
        label: connectionStateOf(agent).state === "ready" ? "Review" : "Test",
        onClick: () => onTab("connections"),
      },
    },
    agentNeedsRuntime(agent) && {
      id: "runtime",
      done: false,
      title: "Say where it runs",
      body: "Simulations call the agent on every turn — add the address our adapter's serve() answers on.",
      action: { label: "Add", onClick: () => onTab("connections") },
    },
    consent && {
      id: "consent",
      done: false,
      title: "Let us call your agent for tests",
      body: `We place simulated calls to your ${PROVIDER_LABELS[v.provider] ? `${PROVIDER_LABELS[v.provider]} ` : ""}agent. What it does on a call — like issuing a refund — really happens, so approve this once before the first run.`,
      action: { label: "Review", onClick: () => onTab("connections") },
    },
    platformTestingOf(agent)?.needsVersioning && {
      id: "versioning",
      done: elevenlabsVersioningOn(agent) || !!v.versioningOnAt,
      title: "Switch versioning on",
      body:
        elevenlabsVersioningOn(agent) || v.versioningOnAt
          ? "On — improvements can ship their change as a branch."
          : `In ${PROVIDER_LABELS[v.provider] || "the platform"}: Agent → Settings → Versioning. It can't be undone. Until then, tests run but a change can't ship as a branch.`,
      action:
        elevenlabsVersioningOn(agent) || v.versioningOnAt
          ? null
          : {
              label: "It's on",
              onClick: () => {
                const at = new Date().toISOString();
                onSaveValues(
                  { versioningOnAt: at, versioning: { at } },
                  "Versioning recorded as on — changes can ship as a branch.",
                );
              },
            },
    },
  ].filter(Boolean);
  const gatesOpen = gates.filter((g) => !g.done).length;

  if (tracesDone && envDone && !gatesOpen) return null;

  /* The project name its traces would land in — for the traceAI lines. */
  const chosen = resolveObserve(observeChoice, null, agent.name);
  /* "Use an existing project" picked, but no project yet. */
  const pickingExisting =
    observeChoice?.mode === "existing" && !observeChoice.id;
  /* A project another agent owns: taking it moves it, so the button says so. */
  const takenFrom =
    chosen.mode === "existing" && !pickingExisting
      ? (() => {
          const l = projectLinkOf(chosen.id, state, agents);
          return l.status === "linked" && l.agentKey !== agent.key
            ? l.agent?.name || "another agent"
            : null;
        })()
      : null;
  /* The tracing pull request, for a GitHub agent — open until it's marked merged. */
  /* Merged here or from the PR's own card — either counts. */
  const prRecord = v.tracingPr?.id
    ? (integrations.prs || []).find((x) => x.id === v.tracingPr.id)
    : null;
  const pr = v.tracingPr
    ? {
        ...v.tracingPr,
        ...(prRecord?.status === "merged" &&
          !v.tracingPr.mergedAt && { mergedAt: prRecord.mergedAt }),
      }
    : null;
  const prOpen = !!pr && !pr.mergedAt;
  const repoUrl = agent.definition?.repoUrl || v.repoUrl || "";
  /* A pull request needs the GitHub app on the repository, and code that
     isn't traced yet — one already registering traceAI would register twice. */
  const github =
    source.kind === "repo" &&
    /github\.com/.test(repoUrl) &&
    !!installationOfRepo(repoNameOf(repoUrl)) &&
    !scan?.project;
  const mergeTracingPr = () => {
    const at = new Date().toISOString();
    /* The PR record and the agent agree: merged, tracing only. */
    if (pr?.id) updatePr(pr.id, { status: "merged", mergedAt: at });
    onSaveValues(
      { tracingPr: { ...pr, mergedAt: at } },
      `PR #${pr?.number} merged — tracing only, so ${agent.name} stays at the same version. Deploy it and its first trace settles ${pending?.project}.`,
    );
  };
  const frameworkId = frameworkIdOf(agent.definition?.framework || v.framework);
  const entry = String(agent.definition?.entry || v.entry || "");
  const matched = instrumentorFor({
    frameworkId,
    frameworkLabel: FRAMEWORK_META[frameworkId]?.label,
    model: agent.definition?.model || v.model,
  });

  const paths = [
    {
      id: "traces",
      eyebrow: "In production",
      /* Done: the project is the answer — "Traces" only names the step. */
      title: tracesDone
        ? project.name
        : pending
          ? "Waiting for its first trace"
          : suggested
            ? "Confirm its traces"
            : pulls
              ? "Enable observability"
              : "Get its traces",
      body: tracesDone
        ? pulls
          ? `Observe project · its ${provider} calls are pulled in, and Error Feed watches them`
          : "Observe project · Error Feed watches its real calls"
        : pending
          ? prOpen
            ? `Draft PR #${pr.number} on ${pr.repo} adds traceAI. Merge and deploy it — the first trace settles ${pending.project}.`
            : pr && pending.via === "pr"
              ? `PR #${pr.number} is merged. Deploy it — the first trace from production settles ${pending.project}.`
              : `In ${pending.project} — nothing has arrived yet. Once traceAI is in its code and it runs, the first trace settles it.`
          : suggested
            ? `${
                suggested.evidence
                  ? `Its code already sends traces to ${suggested.name} (${suggested.evidence}).`
                  : `Its traces look like they land in ${suggested.name}${suggested.traces ? ` (${suggested.traces.split(" · ")[0]})` : ""}.`
              }${suggested.owner ? ` It's linked to ${suggested.owner} now — review moving it here.` : " Confirm it, or pick another project."}`
            : tracesHow,
      unlocks: tracesDone
        ? null
        : pending
          ? "Error Feed starts watching with the first trace."
          : "Error Feed finds issues in real calls, and Fix can repair them.",
      done: tracesDone,
      action: choosing
        ? null
        : tracesDone
          ? {
              label: "Change",
              onClick: () => {
                setObserveChoice({ mode: "existing", id: project.project });
                setChoosing(true);
              },
            }
          : pending
            ? prOpen
              ? {
                  label: "Mark merged",
                  tooltip:
                    "Once the pull request is merged on GitHub. The tracing commit isn't a new agent version.",
                  onClick: mergeTracingPr,
                }
              : traceWait === "idle"
                ? {
                    label: "Listen for traces",
                    onClick: () => setTraceWait("listening"),
                  }
                : null
            : suggested
              ? suggested.owner
                ? {
                    label: `Review ${suggested.name}`,
                    onClick: () => {
                      setObserveChoice({ mode: "existing", id: suggested.id });
                      setChoosing(true);
                    },
                  }
                : {
                    label: `Confirm ${suggested.name}`,
                    onClick: () =>
                      saveObserve({ mode: "existing", id: suggested.id }),
                  }
              : {
                  label:
                    kind === "platform" || kind === "builtin"
                      ? "Choose project"
                      : "Connect traces",
                  onClick: () => {
                    setObserveChoice(null);
                    setChoosing(true);
                  },
                },
      secondary:
        !tracesDone && suggested && !suggested.owner && !choosing
          ? {
              label: "Pick another",
              onClick: () => {
                setObserveChoice({ mode: "existing", id: suggested.id });
                setChoosing(true);
              },
            }
          : pending && !choosing && traceWait === "idle"
            ? {
                label: "Change",
                onClick: () => {
                  setObserveChoice({ mode: "create", id: pending.project });
                  setChoosing(true);
                },
              }
            : null,
    },
    {
      id: "environment",
      eyebrow: "In simulation",
      title: envDone
        ? `${agent.usedIn[0].envName}${agent.usedIn.length > 1 ? ` +${agent.usedIn.length - 1} more` : ""}`
        : "Build an environment",
      body: envDone
        ? agent.usedIn.length > 1
          ? testedInLine(agent.usedIn)
          : "Environment · scenarios run against simulated tools, data and callers"
        : "Simulated tools, data and callers to run scenarios against.",
      unlocks: envDone
        ? null
        : "Test changes, and let improvements try fixes before they ship.",
      done: envDone,
      action: envDone
        ? { label: "View", onClick: () => onTab("environments") }
        : onBuild
          ? { label: "Build environment", onClick: () => onBuild() }
          : null,
    },
  ];

  return (
    <Box
      sx={{
        mb: 2,
        border: "1px solid",
        borderColor: "divider",
        borderRadius: "8px",
        bgcolor: "background.paper",
        overflow: "hidden",
      }}
    >
      <Stack
        direction="row"
        alignItems="center"
        spacing={1}
        sx={{
          px: 2,
          py: 1.25,
          borderBottom: "1px solid",
          borderColor: "divider",
        }}
      >
        <Typography sx={{ typography: "s1", fontWeight: 600 }}>
          Next steps
        </Typography>
        {!(tracesDone && envDone) && (
          <Typography sx={{ typography: "s3", color: "text.subtitle" }}>
            Do either first — the other is here when you need it.
          </Typography>
        )}
        <Box flex={1} />
        {/* A pending project is settled from here — hiding it would leave nothing to listen with. */}
        {!foundNeeded && !pending && (
          <Button
            size="small"
            onClick={() =>
              onSaveValues(
                { setupDismissedAt: new Date().toISOString() },
                "Hidden — everything here is still on the agent's tabs.",
              )
            }
            sx={{ color: "text.secondary", minWidth: 0 }}
          >
            Hide
          </Button>
        )}
      </Stack>

      {/* ── the two ways forward, side by side and equal ── */}
      <Box
        sx={{
          display: "grid",
          gridTemplateColumns: { xs: "1fr", md: "1fr 1fr" },
          "& > *": {
            borderColor: "divider",
            borderStyle: "solid",
            borderWidth: 0,
          },
          "& > *:not(:first-of-type)": {
            borderLeftWidth: { md: 1 },
            borderTopWidth: { xs: 1, md: 0 },
          },
        }}
      >
        {paths.map((p) => (
          <PathCell key={p.id} {...p} />
        ))}
      </Box>

      {/* ── a pending project, listened for in place ── */}
      {pending && !choosing && traceWait !== "idle" && (
        <Box
          sx={{
            px: 2,
            py: 1.5,
            borderTop: "1px solid",
            borderColor: "divider",
          }}
        >
          <FirstTraceWait
            project={pending.project}
            status={traceWait}
            spans={6}
            onListen={() => setTraceWait("listening")}
            onStop={() => setTraceWait("idle")}
          />
        </Box>
      )}

      {/* ── getting traces, opened in place ── */}
      {choosing && (
        <Box
          sx={{
            px: 2,
            py: 1.75,
            borderTop: "1px solid",
            borderColor: "divider",
          }}
        >
          <ObserveProjectStep
            agentName={agent.name}
            agentKey={agent.key}
            value={observeChoice}
            onChange={setObserveChoice}
          />
          {/* A new project has nothing coming in yet — the same steps as Observe's New project, then its first trace. */}
          {sendsCode && chosen.mode === "create" && !pickingExisting && (
            <Box sx={{ mt: 2 }}>
              <Typography sx={{ typography: "s2", fontWeight: 600 }}>
                Then start sending traces
              </Typography>
              <Typography
                sx={{ typography: "s3", color: "text.subtitle", mb: 0.75 }}
              >
                {`Add traceAI where the agent runs — its calls land in ${chosen.id} from the next one.`}
              </Typography>
              {/* Our own runs of this code must never land in its production project. */}
              <Stack
                direction="row"
                spacing={0.75}
                alignItems="flex-start"
                sx={{ mb: 1.5 }}
              >
                <Iconify
                  icon="solar:info-circle-linear"
                  width={14}
                  sx={{ color: "text.subtitle", flexShrink: 0, mt: "2px" }}
                />
                <Typography sx={{ typography: "s3", color: "text.subtitle" }}>
                  {kind === "endpoint"
                    ? `Simulations call its endpoint, so they're traced too — they're tagged as simulated, and Error Feed leaves them out of ${chosen.id}.`
                    : `Simulation runs of this code aren't sent here — our sandbox runs it without these keys, and each run keeps its own traces.`}
                </Typography>
              </Stack>
              <SendTracesSetup
                project={chosen.id}
                instrumentorId={instrumentorPick || matched.id}
                onInstrumentor={(id) => {
                  setInstrumentorPick(id);
                  setTraceWait("idle");
                }}
                matchedId={matched.id}
                fallbackFor={matched.fallback}
                defaultLanguage={
                  /\.(ts|tsx|js)$/.test(entry.split(":")[0])
                    ? "typescript"
                    : "python"
                }
                pullRequest={
                  github
                    ? {
                        repo: repoNameOf(repoUrl),
                        draft: telemetryDraft({
                          agent,
                          repo: repoNameOf(repoUrl),
                          project: chosen.id,
                          instrumentorId: instrumentorPick || matched.id,
                        }),
                        /* The same draft-PR dialog every PR goes through: repository, write access, then open. */
                        onOpen: () =>
                          setPrDialog({
                            choice: observeChoice,
                            draft: telemetryDraft({
                              agent,
                              repo: repoNameOf(repoUrl),
                              project: chosen.id,
                              instrumentorId: instrumentorPick || matched.id,
                            }),
                          }),
                      }
                    : null
                }
              />
              <Box sx={{ mt: 2 }}>
                <FirstTraceWait
                  project={chosen.id}
                  status={traceWait}
                  spans={6}
                  onListen={() => setTraceWait("listening")}
                  onStop={() => setTraceWait("idle")}
                />
              </Box>
            </Box>
          )}
          <Stack
            direction="row"
            justifyContent="flex-end"
            alignItems="center"
            spacing={1}
            sx={{ mt: 1.5 }}
          >
            {takenFrom && (
              <Typography
                sx={{
                  typography: "s3",
                  color: "text.secondary",
                  flex: 1,
                  minWidth: 0,
                }}
              >
                {`${chosen.id} traces ${takenFrom}. Moving it here takes it from ${takenFrom} — its issues are this agent's from now on, and a fix ${takenFrom} already started stays with ${takenFrom}.`}
              </Typography>
            )}
            <Button
              size="small"
              onClick={() => {
                setChoosing(false);
                setTraceWait("idle");
              }}
              sx={{ color: "text.secondary" }}
            >
              Cancel
            </Button>
            {/* New and empty: linked now, settled by its first trace. */}
            {pickingExisting ? (
              <Button size="small" variant="contained" color="primary" disabled>
                Pick a project
              </Button>
            ) : pulls ? (
              <Button
                size="small"
                variant="contained"
                color="primary"
                onClick={() =>
                  saveObserve(
                    chosen.mode === "create"
                      ? { mode: "existing", id: chosen.id }
                      : observeChoice,
                  )
                }
              >
                {takenFrom
                  ? `Move ${chosen.id} here`
                  : `Pull calls into ${chosen.id}`}
              </Button>
            ) : chosen.mode === "create" && traceWait !== "received" ? (
              <Button
                size="small"
                variant="contained"
                color="primary"
                onClick={() =>
                  saveObserve(observeChoice, { pendingVia: "manual" })
                }
              >
                Save — settle on its first trace
              </Button>
            ) : (
              <Button
                size="small"
                variant="contained"
                color="primary"
                onClick={() =>
                  saveObserve(
                    chosen.mode === "create"
                      ? { mode: "existing", id: chosen.id }
                      : observeChoice,
                    { firstTrace: chosen.mode === "create" },
                  )
                }
              >
                {takenFrom ? `Move ${chosen.id} here` : `Link ${chosen.id}`}
              </Button>
            )}
          </Stack>
        </Box>
      )}

      {/* ── only what blocks a run ── */}
      {gates.length > 0 && (
        <>
          <Typography
            sx={{
              px: 2,
              pt: 1.25,
              pb: 0.5,
              typography: "s3",
              fontWeight: 600,
              color: "text.subtitle",
              borderTop: "1px solid",
              borderColor: "divider",
            }}
          >
            {gatesOpen
              ? `Needed before its next run or fix · ${gatesOpen} left`
              : "Needed before its next run or fix · done"}
          </Typography>
          {gates.map((g) => (
            <Row key={g.id} {...g} />
          ))}
        </>
      )}

      {prDialog && (
        <OpenPrDialog
          open
          onClose={() => setPrDialog(null)}
          agent={agent}
          kind="telemetry"
          draft={prDialog.draft}
          files={prDialog.draft.files}
          heading="Add traceAI with a pull request"
          subheading={`Registers ${prDialog.draft.project} and instruments the agent. Tracing only — it isn't a new agent version.`}
          onOpened={(opened) =>
            saveObserve(prDialog.choice, {
              pendingVia: "pr",
              pr: {
                id: opened.id,
                number: opened.number,
                repo: opened.repo,
                branch: opened.branch,
                title: opened.title,
                files: prDialog.draft.files.map((f) => f.path),
                openedAt: opened.openedAt,
              },
            })
          }
        />
      )}

      {/* The constraints start at safe defaults — said, not a step. */}
      <Stack
        direction="row"
        alignItems="center"
        spacing={1}
        sx={{ px: 2, py: 1, borderTop: "1px solid", borderColor: "divider" }}
      >
        <Iconify
          icon="solar:shield-check-linear"
          width={14}
          sx={{ color: "text.subtitle", flexShrink: 0 }}
        />
        <Typography
          noWrap
          sx={{
            typography: "s3",
            color: "text.subtitle",
            flex: 1,
            minWidth: 0,
          }}
        >
          {customLimits
            ? "Constraints set on the Constraints tab — every improvement is held to them."
            : "Constraints: safe defaults — prompts and tool descriptions only, model locked, no cost increase."}
        </Typography>
        <Button
          size="small"
          onClick={() => onTab("constraints")}
          sx={{ color: "text.secondary", minWidth: 0, flexShrink: 0 }}
        >
          Review
        </Button>
      </Stack>
    </Box>
  );
}

AgentSetupCard.propTypes = {
  agent: PropTypes.object,
  env: PropTypes.object,
  /* Opens one of the agent's tabs ("definition", "connections", "constraints", "environments"). */
  onTab: PropTypes.func.isRequired,
  onBuild: PropTypes.func,
  /* (valuesPatch, message) — the agent page's own save. */
  onSaveValues: PropTypes.func.isRequired,
};

/* One way forward: where it applies, what to do, what it unlocks — or, done, where it stands. */
function PathCell({ eyebrow, title, body, unlocks, done, action, secondary }) {
  return (
    <Stack spacing={0.5} sx={{ px: 2, py: 1.75, minWidth: 0 }}>
      <Stack direction="row" alignItems="center" spacing={0.75}>
        <Typography
          sx={{
            typography: "s3",
            fontWeight: 600,
            color: "text.subtitle",
            textTransform: "none",
            letterSpacing: 0.4,
          }}
        >
          {eyebrow}
        </Typography>
        {done && (
          <Iconify
            icon="solar:check-circle-bold"
            width={14}
            sx={{ color: "text.secondary" }}
          />
        )}
      </Stack>
      <Typography sx={{ typography: "s1", fontWeight: 600 }}>
        {title}
      </Typography>
      <Typography sx={{ typography: "s2", color: "text.secondary" }}>
        {body}
      </Typography>
      {unlocks && (
        <Typography sx={{ typography: "s3", color: "text.subtitle" }}>
          {unlocks}
        </Typography>
      )}
      {action && (
        <Stack direction="row" spacing={0.5} sx={{ pt: 0.75 }}>
          <Tooltip arrow title={action.tooltip || ""}>
            <Button
              size="small"
              variant="outlined"
              onClick={action.onClick}
              sx={{ color: "text.primary", borderColor: "divider" }}
            >
              {action.label}
            </Button>
          </Tooltip>
          {secondary && (
            <Button
              size="small"
              onClick={secondary.onClick}
              sx={{ color: "text.secondary" }}
            >
              {secondary.label}
            </Button>
          )}
        </Stack>
      )}
    </Stack>
  );
}
PathCell.propTypes = {
  eyebrow: PropTypes.string,
  title: PropTypes.string,
  body: PropTypes.node,
  unlocks: PropTypes.string,
  done: PropTypes.bool,
  action: PropTypes.object,
  secondary: PropTypes.object,
};

function Row({ done, title, body, action, panel }) {
  return (
    <Box>
      <Stack
        direction="row"
        alignItems="center"
        spacing={1.5}
        sx={{ px: 2, py: 1 }}
      >
        {/* A status, not a control: open items hold the next run or fix (a lock), done ones are ticked.
            An empty circle read as a radio button. */}
        <Iconify
          icon={
            done
              ? "solar:check-circle-bold"
              : "solar:lock-keyhole-minimalistic-linear"
          }
          width={done ? 18 : 16}
          aria-label={done ? "Done" : "Still needed"}
          sx={{
            color: done ? "text.secondary" : "text.subtitle",
            flexShrink: 0,
            mx: done ? 0 : "1px",
          }}
        />
        <Box flex={1} minWidth={0}>
          <Typography
            sx={{
              typography: "s2_1",
              fontWeight: 500,
              color: done ? "text.secondary" : "text.primary",
            }}
          >
            {title}
          </Typography>
          <Typography sx={{ typography: "s3", color: "text.subtitle" }}>
            {body}
          </Typography>
        </Box>
        {action && (
          <Button
            size="small"
            variant="outlined"
            onClick={action.onClick}
            sx={{
              flexShrink: 0,
              color: "text.primary",
              borderColor: "divider",
            }}
          >
            {action.label}
          </Button>
        )}
      </Stack>
      {panel}
    </Box>
  );
}
Row.propTypes = {
  done: PropTypes.bool,
  title: PropTypes.string,
  body: PropTypes.node,
  action: PropTypes.object,
  panel: PropTypes.node,
};
