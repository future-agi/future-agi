import PropTypes from "prop-types";
import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { useSnackbar } from "notistack";
import { Box, Stack } from "@mui/material";
import { useSimStore } from "../../store";
import { agentRecordFromSource } from "../../_mock/agentRegistry";
import { DEFAULT_LIMITS } from "../../_mock/optimizationRuns";
import { emptyProfile, limitsCustomised, observeChoiceOf, observeProjectById, profileSourceOf, resolveObserve } from "../../_mock/connectAgent";
import { currentUser } from "../../_mock/scenarioProvenance";
import { ConnectModeContext, InDrawerContext } from "../../environments/connectMode";
import { FlowPanel } from "../../environments/StartEnvironment";
import ConnectCatalog from "./ConnectCatalog";
import SetupDrawer from "../../environments/SetupDrawer";
import { POPULAR_IDS, detectAgentPaste, newAgentChoicesFor } from "./agentConnectData";
import HostedAgentFlow from "./HostedAgentFlow";
import RepoAgentFlow from "./RepoAgentFlow";
import BuiltinAgentFlow from "./BuiltinAgentFlow";
import FrameworkAgentFlow from "./FrameworkAgentFlow";
import ClosedPlatformFlow from "./ClosedPlatformFlow";
import TracesAgentFlow from "./TracesAgentFlow";
import { CLOSED_WAY, CONNECT_KINDS, MORE_WAYS } from "./connectKinds";
import { useConnectSubmitLabel } from "./connectSubmit";

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
export default function ConnectAgentPicker({ onConnect, preset, prefill, flowsIn = "drawer", request = null, catalogue = true, channel = null }) {
  const submitLabel = useConnectSubmitLabel();
  const { state, commit } = useSimStore();
  const { enqueueSnackbar } = useSnackbar();
  /* Who it is and how it's reached (the confirm step), and the Observe
     project its traces land in — the same for every kind, so kept here. */
  const [profile, setProfile] = useState(() => emptyProfile(currentUser().name));
  const [observe, setObserve] = useState(null);
  const [choice, setChoice] = useState(preset ? "repo" : null);
  /* What the pick already knew (a platform, a pasted ID), which row it
     was, and what the drawer's header shows. */
  const [picked, setPicked] = useState(preset ? { id: "github", initial: null, meta: { name: "GitHub", logo: "github" }, at: 0 } : null);
  /* Which framework the adapter flow opens on — picked on the Framework row. */
  const [framework, setFramework] = useState("langgraph");
  const [limits, setLimits] = useState(DEFAULT_LIMITS);
  /* An endpoint or an upload uses the environment builder's own panel; its
     Connect saves the agent straight away. An upload is built in our hosted
     sandbox — your own machine is Coming soon. */
  const [name, setName] = useState("");
  const [runtime, setRuntime] = useState({ endpoint: "", tokenRef: "", location: "hosted" });
  const panelRef = useRef(null);

  const pick = (id, fw, item = null) => {
    setChoice(id);
    setPicked(item ? { ...item, at: Date.now() } : null);
    if (fw) setFramework(fw);
    setLimits(DEFAULT_LIMITS);
    setProfile(emptyProfile(currentUser().name));
    setObserve(null);
    if (flowsIn === "inline") setTimeout(() => panelRef.current?.scrollIntoView({ behavior: "smooth", block: "start" }), 60);
  };
  const close = () => { setChoice(null); setPicked(null); };
  /* A directory row or a paste: open its flow with what it already knows. */
  const open = (kind, initial, id, meta) => pick(kind, kind === "framework" ? initial?.framework : null, { id, initial: initial || null, meta });
  /* Opened from outside — the page already knows which source it is (an
     Observe project's traces said so). `at` makes a repeat request reopen. */
  useEffect(() => {
    if (request) open(request.kind, request.initial, request.id, request.meta);
  }, [request?.at]); // eslint-disable-line react-hooks/exhaustive-deps

  const save = useCallback((raw) => {
    const { flowReach, ...source } = raw;
    const agentName = agentRecordFromSource(source).name;
    /* Vapi, Retell and Bland agents are linked through their key (call
       logs); an issue's project comes with any agent. Anything else is
       chosen on the agent's page, in Finish setting up — never guessed here. */
    /* Read from traces: the project they land in is the one — settled. */
    const observeProject = (source.kind === "traces" && source.observeProject)
      || (source.kind === "platform" && observeChoiceOf({ ...source, name: agentName }, prefill?.project))
      || (observe || prefill?.project ? resolveObserve(observe, prefill?.project, agentName) : null);
    const full = {
      ...source,
      ...profileSourceOf(profile, { defaultChannel: source.agentType === "voice" ? "voice" : "chat", owner: currentUser().name, flowReach }),
      ...(observeProject && { observeProject }),
      /* Connected from an issue: its project is the one — settled, not a suggestion. */
      ...(((prefill?.project && observeProject?.id === prefill.project) || source.kind === "traces") && { observeChosenAt: new Date().toISOString() }),
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
      const existing = (state?.connectedAgents || []).find((a) => a.key === agentKey);
      const replaced = [...new Set([
        ...Object.entries(state?.projectLinks || {}).filter(([, k]) => k === agentKey).map(([p]) => p),
        existing?.values?.observability?.project,
      ])].filter((p) => p && p !== observeProject.id);
      replaced.forEach((p) => {
        if (state?.projectLinks?.[p] === agentKey) commit({ type: "linkProjectAgent", project: p, agentKey: null });
      });
      commit({ type: "linkProjectAgent", project: observeProject.id, agentKey });
      if (replaced.length) {
        const nameOf = (id) => observeProjectById(id)?.name || id;
        enqueueSnackbar(`${existing?.name || agentName} now links ${nameOf(observeProject.id)} instead of ${replaced.map(nameOf).join(", ")} — one Observe project per agent.`, { variant: "info" });
      }
    }
    onConnect(full);
  }, [commit, enqueueSnackbar, limits, onConnect, observe, prefill?.project, profile, state]);

  const needsRuntime = choice === "upload";
  /* The panels' context is memoised; save is read through a ref so it's current. */
  const saveRef = useRef(save);
  saveRef.current = save;
  const ctx = useMemo(
    () => ({
      /* Hosted sandbox: we build it, so an address typed before switching is dropped. */
      /* Saved straight from the panel — the Observe project and the details
         start at the defaults and are set on the agent's page. */
      onConnect: (source) => saveRef.current({
        ...source, name,
        /* An upload is built in the hosted sandbox — nothing to reach. */
        ...(needsRuntime && { runtime: { endpoint: "", location: "hosted" }, runLocation: "hosted" }),
      }),
      name, setName, preset: null,
      runtime, setRuntime,
      needsRuntime,
      blocked: false,
      submitLabel,
    }),
    [name, runtime, needsRuntime, submitLabel],
  );
  /* Code first, then the platforms. Built in Future AGI is reached by pasting a prompt. */
  const { groups } = useMemo(() => newAgentChoicesFor(channel), [channel]);
  /* The traces flow switches between its two ways in, so its drawer is named for both. */
  const pickItem = (item) => open(item.kind, item.initial, item.id, item.kind === "traces"
    ? { name: "An agent from its traces", icon: "solar:pulse-2-linear" }
    : { name: item.name, logo: item.logo, icon: item.icon });
  const legacy = MORE_WAYS.find((o) => o.id === choice);
  const kind = CONNECT_KINDS.find((o) => o.id === choice);
  const flowProps = {
    title: kind?.title, limits, onLimits: setLimits, onSave: save, initial: picked?.initial || null,
    profile, onProfile: setProfile, observe, onObserve: setObserve, prefill: prefill || null,
    lockedType: channel,
  };

  const flows = (
    <Box key={picked?.at || 0}>
          {choice === "hosted" && <HostedAgentFlow {...flowProps} onSwitch={pick} onPlatform={(pl) => setPicked((cur) => (cur && cur.meta?.name !== pl.name ? { ...cur, meta: { ...cur.meta, name: pl.name, logo: pl.id } } : cur))} />}
          {choice === "repo" && <RepoAgentFlow {...flowProps} preset={preset} />}
          {choice === "builtin" && <BuiltinAgentFlow {...flowProps} />}
          {choice === CLOSED_WAY.id && <ClosedPlatformFlow {...flowProps} title={CLOSED_WAY.title} />}
          {choice === "traces" && <TracesAgentFlow {...flowProps} title="From traces" onOpen={(r) => r && open(r.kind, r.initial, r.id, r.meta)} />}
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
              <FlowPanel choice={choice} prefill={picked?.initial || null} bare={flowsIn === "drawer"} />
            </ConnectModeContext.Provider>
          )}
    </Box>
  );

  return (
    <Stack spacing={flowsIn === "inline" ? 2.5 : 4}>
      {catalogue && (
      <>
      <ConnectCatalog
        groups={groups}
        popularIds={POPULAR_IDS}
        compact={flowsIn === "inline"}
        pickedId={choice ? picked?.id : null}
        onPick={pickItem}
        detect={detectAgentPaste}
        onPaste={(kind, initial, meta) => open(kind, initial, "paste", meta)}
        emptyHint="If it runs somewhere we can call, connect it as an agent endpoint."
        onFallback={() => pickItem(groups.flatMap((g) => g.items).find((it) => it.id === "running"))}
      />
      </>
      )}
      {flowsIn === "inline" ? (
        choice && <Box ref={panelRef} sx={{ scrollMarginTop: 16 }}>{flows}</Box>
      ) : (
        <SetupDrawer open={!!choice} onClose={close} meta={picked?.meta || { name: kind?.title || MORE_WAYS.find((o) => o.id === choice)?.title || (choice === CLOSED_WAY.id ? CLOSED_WAY.title : undefined) }} width={760}>
          {choice && <InDrawerContext.Provider value><Box sx={{ p: 2 }}>{flows}</Box></InDrawerContext.Provider>}
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
  /* The paste bar and directory; off when the page already knows the source. */
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
