import PropTypes from "prop-types";
import { READABLE_CONNECT_CHOICES } from "../../environments/buildTestRule";
import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { useSnackbar } from "notistack";
import { Box, Stack } from "@mui/material";
import { useConnectedAgents, useSimStore } from "../../store";
import { useIntegrations } from "../../_mock/integrations";
import { readyRowsOf } from "./connectedAccounts";
import { agentRecordFromSource } from "../../_mock/agentRegistry";
import { DEFAULT_LIMITS } from "../../_mock/optimizationRuns";
import {
  emptyProfile,
  limitsCustomised,
  observeChoiceOf,
  observeProjectById,
  profileSourceOf,
  resolveObserve,
} from "../../_mock/connectAgent";
import { currentUser } from "../../_mock/scenarioProvenance";
import {
  knownProjects,
  likelyProjectFor,
  projectLinkOf,
} from "../../_mock/projectLinks";
import { scannedTracingOf } from "../../_mock/integrations";
import { observeProjectOf } from "../observeProject";
import {
  ConnectModeContext,
  InDrawerContext,
} from "../../environments/connectMode";
import { FlowPanel } from "../../environments/StartEnvironment";
import ConnectCatalog from "./ConnectCatalog";
import ConnectProjectLine from "./ConnectProjectLine";
import SetupDrawer from "../../environments/SetupDrawer";
import { newAgentChoicesFor } from "./agentConnectData";
import HostedAgentFlow from "./HostedAgentFlow";
import RepoAgentFlow from "./RepoAgentFlow";
import CodingAgentFlow from "./CodingAgentFlow";
import BrowserAgentFlow from "./BrowserAgentFlow";
import BuiltinAgentFlow from "./BuiltinAgentFlow";
import FrameworkAgentFlow from "./FrameworkAgentFlow";
import ClosedPlatformFlow from "./ClosedPlatformFlow";
import PhoneAgentFlow from "./PhoneAgentFlow";
import UploadAgentFlow from "./UploadAgentFlow";
import { CLOSED_WAY, CONNECT_KINDS, MORE_WAYS } from "./connectKinds";
import {
  CONNECT_LABEL,
  ConnectBuildContext,
  ConnectObserveContext,
  DEFAULT_BUILD_SCENARIOS,
  useConnectSubmitLabel,
} from "./connectSubmit";

/**
 * New agent — Agents page and an environment's "Connect a new agent" alike.
 *
 * Pick how the agent gets here — a card, a framework on the Framework
 * row, or one of the more ways — walk that kind's steps, and finish on its
 * constraints. Every kind ends in the same `source` object the environment
 * builder takes, so an agent added here is the same record, with the same
 * identity, as one added anywhere else. Constraints are saved only when
 * they differ from the safe defaults.
 */
export default function ConnectAgentPicker({
  onConnect,
  preset,
  prefill,
  flowsIn = "drawer",
  request = null,
  catalogue = true,
  channel = null,
}) {
  const submitLabel = useConnectSubmitLabel();
  /* Connecting for a new environment: the form also asks how many scenarios
     the build generates, and that count rides along with the agent. */
  const buildMode = submitLabel !== CONNECT_LABEL;
  const [scenarioCount, setScenarioCount] = useState(
    String(DEFAULT_BUILD_SCENARIOS),
  );
  /* Files for the environment it builds (policy, records) — carried to the build. */
  const [buildFiles, setBuildFiles] = useState([]);
  /* What it should test — asked first, as on an agent's Build environment. */
  const [buildRequest, setBuildRequest] = useState("");
  const { state, commit } = useSimStore();
  const { enqueueSnackbar } = useSnackbar();
  /* Who it is and how it's reached (the confirm step), and the Observe
     project its traces land in — the same for every kind, so kept here. */
  const [profile, setProfile] = useState(() =>
    emptyProfile(currentUser().name),
  );
  const [observe, setObserve] = useState(null);
  const [choice, setChoice] = useState(preset ? "repo" : null);
  const buildCtx = useMemo(
    () =>
      buildMode
        ? {
            scenarioCount,
            setScenarioCount,
            files: buildFiles,
            setFiles: setBuildFiles,
            request: buildRequest,
            setRequest: setBuildRequest,
            /* An agent the builder can read proposes its own tests. */
            testOptional: READABLE_CONNECT_CHOICES.includes(choice),
          }
        : null,
    [buildMode, scenarioCount, buildFiles, buildRequest, choice],
  );
  /* What the pick already knew (a platform, a pasted ID), which row it
     was, and what the drawer's header shows. */
  const [picked, setPicked] = useState(
    preset
      ? {
          id: "github",
          initial: null,
          meta: { name: "GitHub", logo: "github" },
          at: 0,
        }
      : null,
  );
  /* Which framework the adapter flow opens on — picked on the Framework row. */
  const [framework, setFramework] = useState("langgraph");
  const [limits, setLimits] = useState(DEFAULT_LIMITS);
  /* An agent endpoint uses the environment builder's own panel; its
     Connect saves the agent straight away. */
  const [name, setName] = useState("");
  const [runtime, setRuntime] = useState({
    endpoint: "",
    tokenRef: "",
    location: "hosted",
  });
  const panelRef = useRef(null);

  const pick = (id, fw, item = null) => {
    setChoice(id);
    setPicked(item ? { ...item, at: Date.now() } : null);
    if (fw) setFramework(fw);
    setLimits(DEFAULT_LIMITS);
    setProfile(emptyProfile(currentUser().name));
    setObserve(null);
    if (flowsIn === "inline")
      setTimeout(
        () =>
          panelRef.current?.scrollIntoView({
            behavior: "smooth",
            block: "start",
          }),
        60,
      );
  };
  const close = () => {
    setChoice(null);
    setPicked(null);
  };
  /* A directory row or a paste: open its flow with what it already knows. */
  const open = (kind, initial, id, meta) =>
    pick(kind, kind === "framework" ? initial?.framework : null, {
      id,
      initial: initial || null,
      meta,
    });
  /* Opened from outside — the page already knows which source it is (an
     Observe project's traces said so). `at` makes a repeat request reopen. */
  useEffect(() => {
    if (request) open(request.kind, request.initial, request.id, request.meta);
  }, [request?.at]); // eslint-disable-line react-hooks/exhaustive-deps

  const save = useCallback(
    (raw) => {
      const { flowReach, ...source } = raw;
      const agentName = agentRecordFromSource(source).name;
      /* A hosted agent with observability on brings its project (its calls are
       pulled with the same key); an issue's project comes with any agent.
       Anything else is chosen on the agent's page, in Next steps — never
       guessed here. */
      /* Every agent is saved with a tracing project. Picked in the line above
       the button, else a new one named after the agent — never one Observe
       already has. An agent already in Agents keeps the project it has. */
      const sameKey = agentRecordFromSource(source).key;
      const already = (state?.connectedAgents || []).find(
        (a) => a.key === sameKey,
      );
      const keepsOwn =
        !observe &&
        !prefill?.project &&
        !!already &&
        !!observeProjectOf(already, state, state?.connectedAgents || []);
      const freshProject = () => {
        const taken = new Set(
          knownProjects(state, state?.connectedAgents || []).map((p) => p.id),
        );
        const base = resolveObserve(
          observe?.mode === "create" ? observe : null,
          null,
          agentName,
        ).id;
        let id = base;
        for (let n = 2; taken.has(id); n += 1) id = `${base}-${n}`;
        return { mode: "create", id };
      };
      /* Left at the default: the project it already sends to, if we can
         tell — read from its code, or (not for code) a project whose name
         matches. Only one no other agent has. It's linked unconfirmed, so
         the agent page asks to confirm it; nothing new is made. */
      const guessed = (() => {
        if (observe || prefill?.project || keepsOwn) return null;
        const everyone = state?.connectedAgents || [];
        const rec = agentRecordFromSource({ ...source, name: agentName });
        const fromCode = scannedTracingOf(rec)?.project || null;
        const id =
          fromCode ||
          (source.kind === "repo"
            ? null
            : likelyProjectFor(rec, state, everyone)?.id || null);
        if (!id) return null;
        const l = projectLinkOf(id, state, everyone);
        return l.status === "linked" && l.agentKey !== rec.key
          ? null
          : { mode: "existing", id };
      })();
      const observeProject =
        (source.kind === "platform" &&
          observeChoiceOf({ ...source, name: agentName }, prefill?.project)) ||
        (keepsOwn
          ? null
          : observe?.mode === "existing" && observe.id
            ? observe
            : prefill?.project
              ? resolveObserve(null, prefill.project, agentName)
              : guessed || freshProject());
      /* Pulled with the platform key: nothing to wait for, so it's settled now. */
      const pulled =
        source.kind === "platform" &&
        !!source.observability &&
        !!observeProject;
      const full = {
        ...source,
        ...profileSourceOf(profile, {
          defaultChannel: source.agentType === "voice" ? "voice" : "chat",
          owner: currentUser().name,
          flowReach,
        }),
        ...(observeProject && { observeProject }),
        ...(buildMode && {
          scenarioCount:
            Number(source.scenarioCount || scenarioCount) ||
            DEFAULT_BUILD_SCENARIOS,
          /* Not saved on the agent — handed to the build it starts. */
          ...(buildFiles.length && { buildFiles }),
          ...(buildRequest.trim() && { buildRequest: buildRequest.trim() }),
        }),
        /* Connected from an issue: its project is the one — settled, not a suggestion. */
        ...(((prefill?.project && observeProject?.id === prefill.project) ||
          pulled ||
          /* Picked by hand in the Tracing project line — the person said so. */
          (observe?.mode === "existing" &&
            observeProject?.id === observe.id)) && {
          observeChosenAt: new Date().toISOString(),
        }),
        /* A project created here for code has no traces yet — it settles with the first one. */
        ...(observeProject?.mode === "create" &&
          !pulled &&
          !(prefill?.project && observeProject.id === prefill.project) && {
            observePending: {
              project: observeProject.id,
              since: new Date().toISOString(),
              via: "manual",
            },
          }),
      };
      const agentKey = agentRecordFromSource(full).key;
      /* Saved at once — connecting from an issue goes straight back to it. */
      if (limitsCustomised(limits)) {
        commit({ type: "setAgentLimits", agentKey, limits });
      }
      /* Error Feed → Fix finds the agent through its project, for every issue
       from it. One Observe project per agent: an agent already in Agents,
       found again by connecting it the same way, links this project instead
       of the one it had. */
      if (observeProject?.id) {
        const existing = (state?.connectedAgents || []).find(
          (a) => a.key === agentKey,
        );
        const replaced = [
          ...new Set([
            ...Object.entries(state?.projectLinks || {})
              .filter(([, k]) => k === agentKey)
              .map(([p]) => p),
            existing?.values?.observability?.project,
          ]),
        ].filter((p) => p && p !== observeProject.id);
        replaced.forEach((p) => {
          if (state?.projectLinks?.[p] === agentKey)
            commit({ type: "linkProjectAgent", project: p, agentKey: null });
        });
        commit({
          type: "linkProjectAgent",
          project: observeProject.id,
          agentKey,
        });
        if (replaced.length) {
          const nameOf = (id) => observeProjectById(id)?.name || id;
          enqueueSnackbar(
            `${existing?.name || agentName} now links ${nameOf(observeProject.id)} instead of ${replaced.map(nameOf).join(", ")} — one tracing project per agent.`,
            { variant: "info" },
          );
        }
      }
      onConnect(full);
    },
    [
      buildMode,
      commit,
      enqueueSnackbar,
      limits,
      onConnect,
      observe,
      prefill?.project,
      profile,
      scenarioCount,
      buildFiles,
      buildRequest,
      state,
    ],
  );

  /* The panels' context is memoised; save is read through a ref so it's current. */
  const saveRef = useRef(save);
  saveRef.current = save;
  const ctx = useMemo(
    () => ({
      /* Saved straight from the panel — the Observe project and the details
         start at the defaults and are set on the agent's page. */
      onConnect: (source) => saveRef.current({ ...source, name }),
      name,
      setName,
      preset: null,
      runtime,
      setRuntime,
      needsRuntime: false,
      blocked: false,
      submitLabel,
    }),
    [name, runtime, submitLabel],
  );
  /* Code first, then the platforms. Built in Future AGI is one line under the tabs. */
  const { builtin, groups } = useMemo(
    () => newAgentChoicesFor(channel),
    [channel],
  );
  /* Rows whose account is already connected: they say what's waiting there,
     and open with the key or the app's access in place. */
  const { agents: connectedAgents } = useConnectedAgents();
  const integrations = useIntegrations();
  const ready = useMemo(
    () => readyRowsOf(connectedAgents, integrations, groups),
    [connectedAgents, integrations, groups],
  );
  const pickItem = (item) =>
    open(
      item.kind,
      ready[item.id]?.initial
        ? { ...(item.initial || {}), ...ready[item.id].initial }
        : item.initial,
      item.id,
      { name: item.name, logo: item.logo, icon: item.icon },
    );
  const legacy = MORE_WAYS.find((o) => o.id === choice && o.id === "running");
  const kind = CONNECT_KINDS.find((o) => o.id === choice);
  const flowProps = {
    title: kind?.title,
    limits,
    onLimits: setLimits,
    onSave: save,
    initial: picked?.initial || null,
    profile,
    onProfile: setProfile,
    observe,
    onObserve: setObserve,
    prefill: prefill || null,
    lockedType: channel,
  };

  const observeCtx = useMemo(
    () => ({
      observe,
      setObserve,
      prefillProject: prefill?.project || null,
      kind: choice,
    }),
    [observe, prefill?.project, choice],
  );
  const flows = (
    <ConnectObserveContext.Provider value={observeCtx}>
      <ConnectBuildContext.Provider value={buildCtx}>
        <Box key={picked?.at || 0}>
          {choice === "hosted" && (
            <HostedAgentFlow
              {...flowProps}
              onSwitch={pick}
              onPlatform={(pl) =>
                setPicked((cur) =>
                  cur && cur.meta?.name !== pl.name
                    ? {
                        ...cur,
                        meta: { ...cur.meta, name: pl.name, logo: pl.id },
                      }
                    : cur,
                )
              }
            />
          )}
          {choice === "repo" && (
            <RepoAgentFlow {...flowProps} preset={preset} />
          )}
          {choice === "coding" && (
            <CodingAgentFlow {...flowProps} title={picked?.meta?.name} />
          )}
          {choice === "browser" && (
            <BrowserAgentFlow {...flowProps} title="Browser agent" />
          )}
          {choice === "builtin" && <BuiltinAgentFlow {...flowProps} />}
          {choice === CLOSED_WAY.id && (
            <ClosedPlatformFlow {...flowProps} title={CLOSED_WAY.title} />
          )}
          {choice === "phone" && (
            <PhoneAgentFlow {...flowProps} title="Phone number" />
          )}
          {choice === "upload" && (
            <UploadAgentFlow {...flowProps} title="Code upload" />
          )}
          {choice === "framework" && (
            <FrameworkAgentFlow
              {...flowProps}
              title="Framework adapter"
              framework={framework}
              onFramework={setFramework}
              onSwitch={pick}
            />
          )}
          {legacy && (
            <ConnectModeContext.Provider value={ctx}>
              <FlowPanel
                choice={choice}
                prefill={picked?.initial || null}
                bare={flowsIn === "drawer"}
              />
              {/* The older panels have their own footer — the line goes under them. */}
              <ConnectProjectLine />
            </ConnectModeContext.Provider>
          )}
        </Box>
      </ConnectBuildContext.Provider>
    </ConnectObserveContext.Provider>
  );

  return (
    <Stack spacing={flowsIn === "inline" ? 2.5 : 4}>
      {catalogue && (
        <>
          <ConnectCatalog
            groups={groups}
            /* No All tab: one group at a time, Code first. */
            showAll={false}
            ready={ready}
            compact={flowsIn === "inline"}
            pickedId={choice ? picked?.id : null}
            onPick={pickItem}
            emptyHint="If it runs somewhere we can call, connect it as an agent endpoint."
            onFallback={() =>
              pickItem(
                groups
                  .flatMap((g) => g.items)
                  .find((it) => it.id === "running"),
              )
            }
            onBuild={builtin ? () => pickItem(builtin) : null}
          />
        </>
      )}
      {flowsIn === "inline" ? (
        choice && (
          <Box ref={panelRef} sx={{ scrollMarginTop: 16 }}>
            {flows}
          </Box>
        )
      ) : (
        <SetupDrawer
          open={!!choice}
          onClose={close}
          meta={
            picked?.meta || {
              name:
                kind?.title ||
                MORE_WAYS.find((o) => o.id === choice)?.title ||
                (choice === CLOSED_WAY.id ? CLOSED_WAY.title : undefined),
            }
          }
          width={960}
        >
          {choice && (
            <InDrawerContext.Provider value>
              <Box sx={{ p: 2 }}>{flows}</Box>
            </InDrawerContext.Provider>
          )}
        </SetupDrawer>
      )}
    </Stack>
  );
}

ConnectAgentPicker.propTypes = {
  onConnect: PropTypes.func.isRequired,
  /* { repo, agent } — open on Repository with a scanned agent picked. */
  preset: PropTypes.object,
  /* { kind, initial, id, meta, at } — open that flow now. */
  request: PropTypes.object,
  /* The search and directory; off when the page already knows the source. */
  catalogue: PropTypes.bool,
  /* { project, issue } — from Error Feed's Fix → "Connect a new agent": the
     Observe step opens on that project. */
  prefill: PropTypes.object,
  /* "drawer" on the New agent page; "inline" where the picker already sits in a drawer. */
  flowsIn: PropTypes.oneOf(["drawer", "inline"]),
  /* Connecting for one environment: only ways to bring this type of agent
     ("voice", "chat"…), and the flows open with the type set, not a choice. */
  channel: PropTypes.string,
};
