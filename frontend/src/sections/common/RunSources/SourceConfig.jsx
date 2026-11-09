// Level 3 · the one config skeleton for every kind (R4-SPEC D4; lane Ra
// MUST 3): header (logo, name · language, badge, `Change`, run label, run
// name) · `Where it runs` · 1 Run it · 2 Status · 3 What this run uses ·
// 4 Limits and safety, and a sticky footer (status line, `Try on 1 row`,
// `Add to experiment`) portalled outside the drawer's only scroller.
import React, { useEffect, useMemo, useRef, useState } from "react";
import { createPortal } from "react-dom";
import PropTypes from "prop-types";
import {
  Box,
  Button,
  ButtonBase,
  Dialog,
  DialogContent,
  DialogTitle,
  IconButton,
  Link,
  Menu,
  MenuItem,
  Stack,
  TextField,
  Typography,
} from "@mui/material";
import Iconify from "src/components/iconify";
import CustomTooltip from "src/components/tooltip";
import { isPrototypeMode } from "src/utils/prototype";
import { VOCAB } from "src/sections/common/vocab";
import KeyHintTooltip from "src/sections/common/ux/KeyHintTooltip";
import { Section } from "src/sections/common/ux/oneview";
import { useViewUrlState } from "src/sections/common/ux/viewState";
import { copyText } from "src/sections/common/ux/CopyButton";
import { BADGES, catalogEntry, previewReason } from "./catalog";
import SourceLogo from "./SourceLogo";
import WhereItRuns from "./WhereItRuns";
import PathTabs from "./PathTabs";
import { copyTryNote, missingSteps } from "./setupRecipe";
import DraftCostLine from "src/sections/develop-detail/Experiment/DraftCostLine";
import TodayStatus, { SIMULATE_OF, SimulatedTag } from "./TodayStatus";
import { useExperimentForm } from "src/sections/develop-detail/Experiment/ExperimentFormContext";
import { ciKeyModels, ciPathOfYaml, ciWorkflow } from "./snippets/ci";
import { sandboxYaml } from "./snippets/sandbox";
import DeclaredUsesForm from "./DeclaredUsesForm";
import { MODEL_PRICES, cleanDeclared, declaredSummary } from "./declaredUses";
import { modelsInText } from "./snippets/agentSamples";
import WhatItUses from "./WhatItUses";
import LimitsRow from "./LimitsRow";
import SecretsRow from "./SecretsRow";
import SafetyRow from "./SafetyRow";
import HarnessRecipe from "./HarnessRecipe";
import { nameAfterModelChange } from "./recipe/recipeModel";
import EndpointConfig from "./EndpointConfig";
import SandboxConfig from "./SandboxConfig";
import CiConfig from "./CiConfig";
import ScriptBlock from "./ScriptBlock";
import { SectionHeading } from "./parts";
import { usePickerUi } from "./pickerUi";
import { defaultLang, snippetFor } from "./snippets";
import { harnessTryCommand } from "./snippets/prototypeHarness";
import { rowColumnsOf, sandboxIdsOf } from "./snippets/runLocal";
import { runFlagsOf } from "./snippets/lines";
import { cappedLimits, SANDBOX_DEFAULTS } from "./snippets/sandbox";
import {
  scopeRowLabels,
  agentNameFor,
  draftReadiness,
  findAgent,
  limitsDefaults,
  limitsWithSlot,
  limitsSummary,
  paramDefaults,
  runConfigFromDraft,
  experimentYaml,
  runNameFor,
  sideEffectRule,
  usableAgents,
  usesOf,
  usesSummary,
} from "./sourceModel";

const LANGUAGE = { python: "Python", typescript: "TypeScript" };
const DIFF_SNAPSHOT_MAX_BYTES = 50 * 1024;

/** A small YAML serialiser for `Run as code` (pure, flat enough here). */
export function toYaml(value, indent = "") {
  if (value === null || value === undefined) return "null";
  if (Array.isArray(value)) {
    if (!value.length) return "[]";
    return value
      .map((v) => {
        const body = toYaml(v, `${indent}  `);
        return typeof v === "object" && v !== null && !Array.isArray(v)
          ? `\n${indent}- ${body.trimStart().replace(/\n/g, `\n`)}`
          : `\n${indent}- ${body}`;
      })
      .join("");
  }
  if (typeof value === "object") {
    const keys = Object.keys(value).filter((k) => value[k] !== undefined);
    if (!keys.length) return "{}";
    return keys
      .map((k) => {
        const v = value[k];
        const body = toYaml(v, `${indent}  `);
        const nested =
          (typeof v === "object" && v !== null && Object.keys(v).length) ||
          (Array.isArray(v) && v.length);
        return `\n${indent}${k}:${nested ? "" : " "}${body}`;
      })
      .join("");
  }
  if (typeof value === "string")
    return /^[\w.@/ -]+$/.test(value) && !/^\d/.test(value)
      ? value
      : JSON.stringify(value);
  return String(value);
}

const LANG_URL_DEFAULTS = { lang: null };

/** The language a code entry shows: the asked one when it supports it. */
export function langOf(entry, asked) {
  const langs = entry?.languages ?? [];
  if ((asked === "python" || asked === "typescript") && langs.includes(asked))
    return asked;
  return defaultLang(entry);
}

/** SandboxConfig's C3 value as sandboxYaml's flat settings (pure). */
function sandboxEditorOf(v) {
  if (!v) return undefined;
  return {
    ...v,
    cpu: v.resources?.cpu,
    memory_gb: v.resources?.memory_gb,
    timeout_s: v.resources?.timeout_s,
  };
}

function Slot({
  kind,
  mode,
  limits,
  models,
  keyModels,
  agentModels,
  ...props
}) {
  if (kind === "harness")
    return <HarnessRecipe mode={mode} limits={limits} {...props} />;
  if (mode === "sandbox")
    return (
      <SandboxConfig
        mode={mode}
        models={keyModels ?? models}
        agentModels={agentModels}
        {...props}
      />
    );
  if (mode === "ci")
    return <CiConfig mode={mode} limits={limits} models={models} {...props} />;
  if (mode === "remote") {
    // EndpointConfig's value is the run's endpoint (C3 `endpoint`).
    const { onChange, ...rest } = props;
    return (
      <EndpointConfig
        mode={mode}
        {...rest}
        onChange={(v) => onChange?.(v?.endpoint ? v : { endpoint: v })}
      />
    );
  }
  return null;
}
Slot.propTypes = {
  kind: PropTypes.string,
  mode: PropTypes.string,
  limits: PropTypes.object,
  models: PropTypes.array,
  /** The models whose keys the run needs (a changed model param first). */
  keyModels: PropTypes.array,
  agentModels: PropTypes.array,
  onChange: PropTypes.func,
};

export default function SourceConfig({
  state,
  onState,
  onChangeSource,
  dataset,
  runLabel,
  reserved,
  openedAtMs,
  machines,
  initialDraft,
  onAdd,
  onOpenResults,
  rowCount,
  rowNoun = "row",
  tries,
  onTries,
}) {
  const entry = catalogEntry(state.entryId);
  const kind = entry?.kind ?? "code";
  const footerEl = usePickerUi((s) => s.footerEl);
  const deepAgent = state.agentId ? findAgent(machines, state.agentId) : null;
  const [connected, setConnected] = useState(() =>
    deepAgent && deepAgent.machine?.may_use !== false ? deepAgent : null,
  );
  useEffect(() => {
    if (!connected && deepAgent && deepAgent.machine?.may_use !== false)
      setConnected(deepAgent);
  }, [deepAgent, connected]);
  // A fresh handshake announces its agent before the shared machines query
  // has refetched; keep the connected agent in step with that query (its
  // machine record, status and access) whenever it lists the agent.
  useEffect(() => {
    const id = connected?.agent?.id;
    if (!id) return;
    const hit = findAgent(machines, id);
    if (hit && hit.machine !== connected.machine) setConnected(hit);
  }, [machines, connected]);
  const known =
    connected ?? usableAgents(machines, { entryId: entry?.id })[0] ?? null;
  const agentName = agentNameFor({
    knownAgent: known?.agent,
    datasetName: dataset?.name,
  });
  // The Rb slot's partial config (recipe); it reaches the draft.
  const [slot, setSlot] = useState(() => initialDraft?.slot ?? {});
  const savedName = null;
  const [runName, setRunName] = useState(
    initialDraft?.runName ??
      runNameFor({ agentName, entryId: entry?.id, savedName }),
  );
  const nameTouched = useRef(Boolean(initialDraft?.runName));
  useEffect(() => {
    if (!nameTouched.current)
      setRunName(runNameFor({ agentName, entryId: entry?.id, savedName }));
  }, [agentName, entry?.id, savedName]);
  // L3 G6: a harness worker's facts (its declared models and tools, its
  // cap) apply only to a slot bound to its machine (Live connection); an
  // upload, CI or sandbox run of the same harness has none of them.
  const bound = kind !== "harness" || Boolean(slot.harness?.connection);
  const boundAgent = bound ? known?.agent ?? null : null;
  const uses = useMemo(() => usesOf({ agent: boundAgent }), [boundAgent]);
  const [params, setParams] = useState(initialDraft?.params ?? null);
  const values = params ?? paramDefaults(uses.params);
  // IT-R3 finalize: the defaults follow the agent as the machines query
  // resolves (it may land after mount), so a fresh card never shows `4 at a
  // time` on one load and the agent's declared `2 at a time` on the next.
  const [limitsEdited, setLimitsOwn] = useState(initialDraft?.limits ?? null);
  const limitsOwn =
    limitsEdited ??
    limitsDefaults({ agent: boundAgent, tries: tries ?? dataset?.trials });
  const onSlot = (patch) => {
    // L3 G6 fix 8: a copied harness run named `<Harness> × <model>` follows
    // a model change (a name the user typed stays).
    if (kind === "harness" && patch?.harness) {
      const next = nameAfterModelChange(
        runName,
        patch.harness.name ?? entry?.name,
        slot.harness?.model,
        patch.harness.model,
      );
      if (next) setRunName(next);
    }
    setSlot((s) => ({ ...s, ...patch }));
  };
  // Tries are the experiment's (one source, R3's `trials`); the rest are the
  // run's.
  // options-truth L3 G4 fix 1 (S5): in sandbox mode the sandbox caps run
  // at a time at min(2 × vCPU, 10) for every kind (a harness's sandbox is
  // its recipe's); the agent's declared cap applies only when the run
  // executes on its machine through the connection (Connect this
  // machine); a script run on this machine is the script's own (S5).
  // Any clamp is named.
  const sbValue =
    state.mode === "sandbox"
      ? kind === "harness"
        ? slot.harness?.sandbox ?? slot.sandbox ?? null
        : slot.sandbox ?? null
      : null;
  const sandboxCpu =
    state.mode === "sandbox"
      ? sbValue?.resources?.cpu ?? sbValue?.cpu ?? SANDBOX_DEFAULTS.cpu
      : null;
  // The agent's own cap always comes from the agent (a stored edit carries
  // the last derived cap, which a vCPU change must not keep). L3 G6: a
  // harness runs on the worker's machine only when its slot is bound to
  // that connection (Live connection); upload, CI and sandbox runs are not.
  const onAgentMachine =
    kind === "harness"
      ? bound && Boolean(known?.agent)
      : state.mode === "local" &&
        state.form === "connect" &&
        Boolean(known?.agent);
  const agentMax = onAgentMachine
    ? limitsDefaults({ agent: known?.agent }).concurrencyMax
    : null;
  // L3 G6: a harness has one time limit, the recipe's (More settings) and
  // this row's are the same value.
  const harnessTimeout =
    kind === "harness" ? Number(slot.harness?.timeout_s) || null : null;
  const limits = cappedLimits(
    limitsWithSlot({
      ...limitsOwn,
      ...(harnessTimeout ? { timeoutS: harnessTimeout } : {}),
      concurrencyMax: agentMax,
      tries: Number(tries) || limitsOwn.tries,
    }),
    {
      sandboxCpu,
      agentName: onAgentMachine ? known?.agent?.name ?? null : null,
      machineName: onAgentMachine ? known?.machine?.name ?? null : null,
    },
  );
  const setLimits = (next) => {
    if (next.tries !== limits.tries) onTries?.(next.tries);
    // L3 G6: the harness's time limit is its recipe's: write it there too
    // (the recipe adopts slot.harness.timeout_s).
    const t = Number(next.timeoutS);
    if (kind === "harness" && t && t !== Number(limits.timeoutS))
      setSlot((s) => ({
        ...s,
        harness: {
          ...(s.harness ?? {}),
          timeout_s: t,
          budget: { ...(s.harness?.budget ?? {}), timeout_s: t },
        },
      }));
    setLimitsOwn(next);
  };
  const [safety, setSafety] = useState(initialDraft?.safety ?? "real");
  const [secretsSource, setSecretsSource] = useState(
    initialDraft?.secretsSource ?? null,
  );
  // C15: the Data step's scope (row ids or split ids) and the evals.
  const form = useExperimentForm();
  const scopeRowIds = Array.isArray(form?.rowIds) ? form.rowIds : [];
  // L3 G4 C5: the picked rows by label (`T5`), as every command prints them
  const scopeLabels = scopeRowLabels(scopeRowIds, form?.rows, rowNoun);
  // the row a connected Try (footer and panel) runs: the first picked one
  const tryRow = scopeRowIds.length
    ? { id: scopeRowIds[0], label: scopeLabels[0] }
    : {
        id: dataset?.firstRowId ?? null,
        label:
          dataset?.firstRowLabel ??
          (dataset?.firstRowId
            ? scopeRowLabels([dataset.firstRowId], form?.rows, rowNoun)[0]
            : null),
      };
  const scopeSplitIds = Array.isArray(form?.splitIds) ? form.splitIds : [];
  const files = uses.code?.files ?? [];
  const [diffSnapshot, setDiffSnapshot] = useState(
    initialDraft?.diffSnapshot != null
      ? Boolean(initialDraft.diffSnapshot)
      : files.length > 0,
  );
  const [arrival, setArrival] = useState(initialDraft?.arrival ?? null);
  // IT-R4 G1 P2 (S3): `1 task` / `All 24 tasks` in Run it drives Simulate.
  const [runScope, setRunScope] = useState("one");
  // IT-R4 G1 P5: what a Your code run uses, declared (optional).
  const [declared, setDeclared] = useState(initialDraft?.declared ?? null);
  // IT-R1 G1 P5: the waiting timer starts when the user copies a command
  // (or the run is added); before that the status reads `Not started`.
  const [startedAtMs, setStartedAtMs] = useState(
    initialDraft?.startedAtMs ?? null,
  );
  const markStarted = () => setStartedAtMs((t) => t ?? Date.now());
  const [tryState, setTryState] = useState(null);
  // IT-R2 G1 P5: Try on 1 row behaves the same on every card; the copy
  // result shows in the footer (no toast).
  const [tryNote, setTryNote] = useState(null);
  // IT-R2 G1 P9: Simulate sits in the sticky footer next to Try on 1 row.
  const simulateRef = useRef(null);
  // IT-R5 G1 P3 (d): `Simulate 1 task` opens step 5 first, then starts the
  // stand-in once its status block is mounted (pendingSim).
  const [pendingSim, setPendingSim] = useState(false);
  const [reveal, setReveal] = useState(null);
  const revealStep = (id) => setReveal({ id, nonce: Date.now() });
  // IT-R5 G1 P3 (e): which setup steps have proof (PathTabs reports them).
  const [setup, setSetup] = useState({ done: {}, noSandbox: false, steps: [] });
  const [menu, setMenu] = useState(null);
  const [yaml, setYaml] = useState(false);
  const statusRef = useRef(null);
  // The language toggle lives in the URL (`lang=`), so a reload keeps it.
  const [langUrl, setLangUrl] = useViewUrlState(LANG_URL_DEFAULTS);
  const lang = langOf(entry, langUrl.lang ?? state.lang);
  // dataset integration FE-0: there is no live connection command, so no code run is
  // ever started over one.
  const live = false;

  const ids = {
    datasetId: dataset?.id,
    datasetName: dataset?.name,
    experimentId: reserved?.experimentId,
    runId: reserved?.runId,
    runLabel,
    inputColumn: dataset?.inputColumn,
    tries: limits.tries,
    agentName,
    keys: dataset?.keys,
    // the safety choice is a runner flag (--side-effects) too
    sideEffects: safety === "stubbed" ? "stubs" : safety,
    // IT-R1 G1 P2: a task suite's sandbox reaches step 1 and the script.
    sandbox: sandboxIdsOf(dataset?.environment),
    // IT-R2 G1 P3: the samples read the dataset's real columns.
    columns: rowColumnsOf(dataset?.sample?.columns),
  };
  // options-truth L2 G4 (C1, S3): every command this card prints carries
  // the form: tries, at a time, cost cap, time limit, changed params, the
  // picked rows and a skipped sandbox.
  ids.run = runFlagsOf({
    runId: reserved?.runId,
    limits,
    params: values,
    schema: uses.params,
    rowIds: scopeLabels,
    noSandbox: setup.noSandbox,
  });
  const snippet =
    kind === "code"
      ? snippetFor({
          kind,
          entryId: entry?.id,
          form: state.form,
          view: state.view,
          lang,
          ids,
        })
      : null;
  const rule = sideEffectRule({
    kind,
    agentName,
    sideEffectTools: uses.sideEffectTools,
    environment: dataset?.environment,
    safety,
    tries: limits.tries,
    rows: rowCount,
    rowNoun,
  });
  const notAllowed =
    connected && connected.machine?.may_use === false
      ? `Only ${connected.machine.owner?.name} can run on ${connected.machine.name}`
      : null;
  const draft = {
    entryId: entry?.id,
    kind,
    mode: state.mode,
    form: state.form,
    view: state.view,
    lang,
    runName,
    runLabel,
    reserved,
    agentName,
    machine: live ? connected?.machine : null,
    agent: live ? connected?.agent : known?.agent ?? null,
    uses,
    params: values,
    limits,
    slot,
    safety,
    diffSnapshot: files.length
      ? { files: files.map((p) => ({ path: p })), saved: diffSnapshot }
      : null,
    notAllowed,
    sideEffect: rule,
    // IT-R1 G1 P5: the checklist survives Add to experiment / Run.
    arrival,
    startedAtMs,
    sandbox: ids.sandbox,
    // the same flags Review's Start it prints (startIt.js)
    runFlags: ids.run,
    declared: kind === "code" ? cleanDeclared(declared) : null,
    secretsSource,
  };
  const runConfig = runConfigFromDraft(draft);
  const modeOf = state.mode;
  const standIn =
    kind === "code" && !live
      ? modeOf === "ci"
        ? "ci_job"
        : modeOf === "local"
          ? "terminal"
          : null
      : null;
  const declaredModels = (runConfig.models ?? []).map((m) => m.id);
  // options-truth L2 G4 (C3): the key rows ask for the models the run
  // calls: a changed `model` param first, then the declared ones.
  const keyModels = ciKeyModels({
    entry,
    models: declaredModels,
    params: ids.run?.params,
  });
  // L3 G4 C2: with no declared model, the key row asks for the model the
  // drawer's sample agent calls (the upload snippet's step `agent`).
  const agentModels =
    kind === "code" && !keyModels.length
      ? modelsInText(
          snippetFor({
            kind,
            entryId: entry?.id,
            form: "upload",
            lang,
            ids,
          })?.steps?.find((x) => x.id === "agent")?.code,
          Object.keys(MODEL_PRICES),
        )
      : [];
  // The CI file this run's settings make (the footer copies the same file).
  const ciYaml =
    kind === "code" && state.mode === "ci"
      ? ciWorkflow({
          entry,
          ids,
          ci: slot.ci,
          limits,
          models: declaredModels,
          flags: ids.run,
        })
      : null;
  const ready = draftReadiness(draft);
  // options-truth L2 G4 (C1): the cost line counts the rows in scope: the
  // picked rows, else 1 when Run it is on `1 task`, else every row.
  const scopedRows = scopeRowIds.length
    ? scopeRowIds.length
    : kind === "code" && !live && state.mode === "local" && runScope === "one"
      ? 1
      : rowCount;
  // IT-R2 G1 P1: what closing the wizard now would lose (asks first).
  const setHold = usePickerUi((s) => s.setHold);
  const holdName = entry?.name ?? null;
  const simulated = Boolean(arrival);
  const tried = Boolean(tryState?.result);
  useEffect(() => {
    setHold(holdName ? { name: holdName, simulated, tried } : null);
  }, [holdName, simulated, tried, setHold]);
  useEffect(() => () => setHold(null), [setHold]);

  const copyTry = async (command) => {
    const ok = await copyText(command ?? "");
    if (ok) markStarted();
    // L3 G4 C5: copying names only the missing setup steps, by their
    // real numbers and titles; with setup done, it says to add the run
    // first (this card is a draft until Add).
    const missing =
      ok && kind === "code" ? missingSteps(setup.done, setup) : [];
    const text = copyTryNote({ missing, added: false, runLabel });
    setTryNote(
      ok
        ? missing.length
          ? {
              text,
              action: `Open step ${missing[0].n}`,
              onAction: () => revealStep(missing[0].id),
            }
          : text
        : "Couldn't copy. Your browser blocked the clipboard.",
    );
  };

  // IT-R5 G1 P4: `Add model` opens `What this run uses` and focuses Model.
  const openDeclaredModel = () => {
    const root = document.querySelector('[data-testid="source-config"]');
    const head = [...(root?.querySelectorAll("h3") ?? [])].find(
      (h) => h.textContent === VOCAB.WHAT_THIS_RUN_USES,
    );
    const btn = head?.closest("[aria-expanded]");
    if (btn?.getAttribute("aria-expanded") === "false") btn.click();
    setTimeout(() => {
      const input = root?.querySelector('[data-testid="declared-model"]');
      input?.scrollIntoView({ block: "center", behavior: "smooth" });
      input?.focus();
    }, 120);
  };
  const scrollToIssue = () => {
    const root = document.querySelector('[data-testid="source-config"]');
    const bad =
      root?.querySelector(
        '[aria-invalid="true"], .Mui-error, [data-issue="true"]',
      ) ?? statusRef.current;
    bad?.scrollIntoView?.({ block: "center", behavior: "smooth" });
  };
  const tryOne = async () => {
    if (kind === "code" && !live && state.mode === "ci") {
      const ok = await copyText(ciYaml);
      if (ok) markStarted();
      setTryNote(
        ok
          ? `Copied ${ciPathOfYaml(ciYaml)}: commit it, then set the repository variable FI_RUN_ID`
          : "Couldn't copy. Your browser blocked the clipboard.",
      );
      return;
    }
    if (kind === "code" && !live && state.mode === "sandbox") {
      const ok = await copyText(sandboxYaml(sandboxEditorOf(slot.sandbox)));
      setTryNote(
        ok
          ? "Copied the sandbox settings (YAML). The run starts itself when you add it."
          : "Couldn't copy. Your browser blocked the clipboard.",
      );
      return;
    }
    if (kind === "code" && !live && state.mode === "remote") {
      document
        .querySelector(
          '[data-testid="source-config"] [data-testid="endpoint-test"]',
        )
        ?.click();
      return;
    }
    if (kind === "code" && !live) {
      await copyTry(snippet?.tryCommand);
      statusRef.current?.scrollIntoView({
        block: "nearest",
        behavior: "smooth",
      });
      return;
    }
    if (kind === "harness") {
      await copyTry(harnessTryCommand(ids));
      return;
    }
  };
  // IT-R2 G1 P2: `Add to experiment and open results` (never a dead end):
  // adds this run, runs the experiment and opens its results.
  const addAndOpen = ready.ok
    ? () => {
        onAdd({ ...draft, config: runConfigFromDraft(draft) });
        usePickerUi.getState().requestSubmit();
      }
    : undefined;
  // Disabled = the reason + the button that unblocks it (rule 13).
  const tryBlock =
    live && !connected
      ? {
          reason: "Connect your agent first",
          action: "Connect",
          onAction: () =>
            statusRef.current?.scrollIntoView({
              block: "center",
              behavior: "smooth",
            }),
        }
      : !ready.ok && kind === "harness"
        ? { reason: ready.reason, action: "Show", onAction: scrollToIssue }
        : null;
  const tryDisabled = tryBlock?.reason ?? null;

  // IT-R3 G1 P3: script mode copies the 1-row command (it never runs it
  // here); a connected run really runs one row.
  const scriptTry =
    (kind === "code" && !live && state.mode !== "remote") || kind === "harness";
  const tryLabel =
    kind === "code" && !live && state.mode === "ci"
      ? "Copy workflow file"
      : kind === "code" && !live && state.mode === "sandbox"
        ? "Copy sandbox config"
        : kind === "code" && !live && state.mode === "remote"
          ? "Test on one row"
          : scriptTry
            ? VOCAB.COPY_ONE_ROW_COMMAND
            : live && connected
              ? `${VOCAB.TRY_ON_ONE_ROW}${tryRow.label ? ` · ${tryRow.label}` : ""}`
              : VOCAB.TRY_ON_ONE_ROW;

  const footer = (
    <Stack
      direction="row"
      alignItems="center"
      flexWrap="wrap"
      columnGap={1.25}
      rowGap={0.75}
      sx={{ width: "100%", minWidth: 0 }}
      data-testid="source-footer"
    >
      {/* IT-R5 G1 P4: the status never truncates; when the buttons leave
          it less than 240 px it takes its own line above them. */}
      <Stack
        direction="row"
        alignItems="center"
        flexWrap="wrap"
        columnGap={1}
        rowGap={0.25}
        sx={{ flex: "1 1 240px", minWidth: 0 }}
      >
        {/* IT-R5 G1 P4: one cost line in every source footer, first. */}
        <DraftCostLine
          draft={draft}
          rows={scopedRows}
          tries={tries}
          rowNoun={rowNoun}
          onAddModel={kind === "code" ? openDeclaredModel : undefined}
        />
        {tryBlock ? (
          <Stack
            direction="row"
            alignItems="center"
            gap={0.75}
            sx={{ minWidth: 0 }}
            data-testid="try-disabled-reason"
          >
            <Typography
              typography="s2"
              color="text.secondary"
              sx={{ minWidth: 0 }}
              title={tryBlock.reason}
            >
              {tryBlock.reason}
            </Typography>
            <Button
              size="small"
              onClick={tryBlock.onAction}
              sx={{ flexShrink: 0, typography: "s2", minWidth: 0 }}
            >
              {tryBlock.action}
            </Button>
          </Stack>
        ) : tryNote && typeof tryNote === "object" ? (
          <Stack
            direction="row"
            alignItems="center"
            columnGap={0.75}
            flexWrap="wrap"
            sx={{ minWidth: 0 }}
            data-testid="try-note"
          >
            <Typography typography="s2" color="text.secondary">
              {tryNote.text}
            </Typography>
            <Link
              component="button"
              type="button"
              typography="s2"
              onClick={tryNote.onAction}
              data-testid="try-note-action"
              sx={{ flexShrink: 0 }}
            >
              {tryNote.action}
            </Link>
          </Stack>
        ) : tryNote ? (
          <Typography
            typography="s2"
            color="text.secondary"
            title={tryNote}
            data-testid="try-note"
            sx={{ wordBreak: "break-word" }}
          >
            {tryNote}
          </Typography>
        ) : kind === "code" && !live && state.mode === "local" ? (
          // IT-R5 G1 P3 (d): the footer progress is a link to step 5.
          <ButtonBase
            onClick={() => revealStep("run")}
            data-testid="footer-progress-link"
            title="Open the run step"
            sx={{
              justifyContent: "flex-start",
              gap: 0.75,
              minWidth: 0,
              maxWidth: "100%",
              borderRadius: 0.5,
              textAlign: "left",
            }}
          >
            <TodayStatus
              compact
              arrival={arrival}
              openedAtMs={startedAtMs}
              rowCount={rowCount}
              rowNoun={rowNoun}
            />
            <Typography
              typography="s2"
              color="primary.main"
              sx={{ flexShrink: 0, whiteSpace: "nowrap" }}
            >
              {`→ step ${setup.steps.length || 5}`}
            </Typography>
          </ButtonBase>
        ) : (
          <Typography
            typography="s2"
            color="text.secondary"
            data-testid="source-footer-status"
          >
            {state.mode === "ci"
              ? `${entry?.name} · starts when the CI job runs`
              : state.mode === "sandbox"
                ? `${entry?.name} · starts itself in the Future AGI sandbox when you add it`
                : state.mode === "remote"
                  ? `${entry?.name} · Future AGI calls your endpoint for each row`
                  : kind === "harness" && slot.harness?.connection
                    ? // L3 G6: a slot bound to a connected worker starts there
                      `${entry?.name} · starts on ${slot.harness.connection.machine_name ?? "the connected machine"} when you add it`
                    : `${entry?.name} · starts when you run its command`}
          </Typography>
        )}
      </Stack>
      <Stack
        direction="row"
        alignItems="center"
        gap={1.25}
        sx={{ ml: "auto", flexShrink: 0 }}
      >
        {standIn && isPrototypeMode() && !arrival ? (
          <Stack direction="row" alignItems="center" gap={0.75}>
            <Button
              variant="outlined"
              size="medium"
              onClick={() => {
                // IT-R5 G1 P3 (d): open step 5, then start (pendingSim);
                // CI's status block is always mounted and starts at once.
                setPendingSim(true);
                if (standIn === "terminal") revealStep("run");
              }}
              startIcon={<Iconify icon="mdi:play-outline" width={16} />}
              data-testid="footer-simulate"
              title={
                standIn === "terminal"
                  ? `Runs the ${runScope === "one" ? `1-${rowNoun}` : `all-${rowNoun}s`} command's rows`
                  : undefined
              }
              sx={{
                whiteSpace: "nowrap",
                color: "text.primary",
                borderColor: "divider",
              }}
            >
              {SIMULATE_OF[standIn].label}
            </Button>
            <SimulatedTag event={SIMULATE_OF[standIn].event} />
          </Stack>
        ) : null}
        <KeyHintTooltip
          title={
            tryDisabled ??
            (kind === "code" && !live
              ? `Copies: ${snippet?.tryCommand}`
              : kind === "harness"
                ? `Copies: ${harnessTryCommand(ids)}`
                : "Run one row now")
          }
        >
          <span>
            <Button
              variant="outlined"
              size="medium"
              onClick={tryOne}
              disabled={Boolean(tryDisabled)}
              data-testid="try-one-row"
              startIcon={
                scriptTry ? (
                  <Iconify icon="mdi:content-copy" width={14} />
                ) : undefined
              }
              sx={{
                whiteSpace: "nowrap",
                color: "text.primary",
                borderColor: "divider",
              }}
            >
              {tryLabel}
            </Button>
          </span>
        </KeyHintTooltip>
        <CustomTooltip
          show={!ready.ok}
          title={ready.reason ?? ""}
          size="small"
          placement="top"
        >
          <span>
            <Button
              variant="contained"
              color="primary"
              size="medium"
              disabled={!ready.ok}
              onClick={
                // IT-R3 G1 P3: once results are in, the footer does what the
                // inline `Add to experiment and open results` does.
                arrival && kind === "code" && addAndOpen
                  ? addAndOpen
                  : () => onAdd({ ...draft, config: runConfigFromDraft(draft) })
              }
              data-testid="add-to-experiment"
              sx={{ whiteSpace: "nowrap" }}
            >
              {arrival && kind === "code" && addAndOpen
                ? "Add to experiment and open results"
                : "Add to experiment"}
            </Button>
          </span>
        </CustomTooltip>
      </Stack>
    </Stack>
  );

  if (!entry) return null;
  const badge = entry.badge ? BADGES[entry.badge] : null;
  const usesLine = usesSummary(uses);
  const limitsLine = `${limitsSummary({ ...limits, rowNoun })}${rule.state === "warn" ? ` · side effects: ${safety === "real" ? "for real" : safety === "stubbed" || safety === "stubs" ? "stubs" : "dry run"}` : ""}`;

  return (
    <Stack spacing={1.5} data-testid="source-config" data-entry={entry.id}>
      {/* header */}
      <Stack direction="row" alignItems="center" gap={1} sx={{ minWidth: 0 }}>
        <SourceLogo entry={entry} size={28} />
        <Typography typography="s1" fontWeight="fontWeightSemiBold" noWrap>
          {`${entry.name}${kind === "code" ? ` · ${LANGUAGE[lang] ?? ""}` : ""}`}
        </Typography>
        {badge ? (
          <CustomTooltip
            show={Boolean(previewReason(entry))}
            title={previewReason(entry) ?? ""}
            size="small"
            placement="top"
          >
            <Typography
              typography="s3"
              color="text.secondary"
              noWrap
              sx={{
                px: 0.75,
                border: "1px solid",
                borderColor: "divider",
                borderRadius: 0.5,
                flexShrink: 0,
              }}
            >
              {badge}
            </Typography>
          </CustomTooltip>
        ) : null}
        <Button
          size="small"
          onClick={onChangeSource}
          data-testid="change-framework"
          sx={{ minWidth: 0, px: 0.75, typography: "s2", whiteSpace: "nowrap" }}
        >
          {kind === "harness" ? "Change harness" : "Change framework"}
        </Button>
      </Stack>
      {/* IT-R5 G1 P7: the run's id and name get their own row (the title
          above is never cut for them). */}
      <Stack
        direction="row"
        alignItems="center"
        gap={1}
        flexWrap="wrap"
        rowGap={0.75}
        data-testid="run-name-row"
        sx={{ minWidth: 0 }}
      >
        <Typography
          typography="s2"
          color="text.secondary"
          sx={{ flexShrink: 0 }}
        >
          Run name
        </Typography>
        <CustomTooltip
          show
          title={`${runLabel} · id reserved for this draft`}
          size="small"
          placement="top"
        >
          <Typography
            data-testid="run-label"
            typography="s2"
            fontWeight="fontWeightSemiBold"
            sx={{
              px: 0.75,
              borderRadius: 0.5,
              bgcolor: "background.neutral",
              flexShrink: 0,
            }}
          >
            {runLabel}
          </Typography>
        </CustomTooltip>
        <TextField
          size="small"
          value={runName}
          onChange={(e) => {
            nameTouched.current = true;
            setRunName(e.target.value);
          }}
          inputProps={{ "aria-label": "Run name", "data-testid": "run-name" }}
          sx={{
            flex: "1 1 200px",
            maxWidth: 360,
            "& input": { py: 0.6, typography: "s2" },
          }}
        />
        <Box sx={{ ml: "auto", minWidth: 0 }}>
          <WhereItRuns
            kind={kind}
            entryId={entry.id}
            mode={state.mode}
            onChange={(mode) => onState({ mode })}
            slot={null}
            machineLabel={
              live && connected ? connected.machine?.name ?? null : null
            }
          />
        </Box>
      </Stack>

      {/* 1 Run it */}
      <Stack spacing={1}>
        <SectionHeading
          title={kind === "code" ? "Run it" : "Set it up"}
          right={
            <>
              <IconButton
                size="small"
                aria-label="Run it options"
                onClick={(e) => setMenu(e.currentTarget)}
              >
                <Iconify icon="mdi:dots-horizontal" width={18} />
              </IconButton>
              <Menu
                open={Boolean(menu)}
                anchorEl={menu}
                onClose={() => setMenu(null)}
              >
                <MenuItem
                  onClick={() => {
                    setMenu(null);
                    setYaml(true);
                  }}
                >
                  Run as code (YAML)
                </MenuItem>
              </Menu>
            </>
          }
        />
        {kind === "code" && state.mode === "local" ? (
          <PathTabs
            entry={entry}
            form={state.form}
            onForm={(form) => onState({ form })}
            view={state.view}
            onView={(view) => onState({ view })}
            lang={lang}
            onLang={(l) => {
              setLangUrl({ lang: l });
              onState({ lang: l });
            }}
            ids={ids}
            sample={dataset?.sample}
            onCopied={markStarted}
            rowCount={rowCount}
            rowNoun={rowNoun}
            connectPanel={null}
            scope={runScope}
            onScope={setRunScope}
            connected={Boolean(live && connected)}
            reveal={reveal}
            arrived={Boolean(arrival)}
            onDone={(done, o) => setSetup({ done, ...o })}
            status={
              live ? null : (
                <Box ref={statusRef}>
                  <TodayStatus
                    datasetId={dataset?.id}
                    entryId={entry.id}
                    runId={reserved?.runId}
                    rowCount={rowCount}
                    rowNoun={rowNoun}
                    openedAtMs={startedAtMs}
                    tryCommand={snippet?.tryCommand}
                    arrival={arrival}
                    onArrival={setArrival}
                    onSimulate={markStarted}
                    simulateRows={runScope === "one" ? 1 : "all"}
                    tries={limits.tries}
                    config={runConfig}
                    rowIds={scopeRowIds}
                    splitIds={scopeSplitIds}
                    as="terminal"
                    // IT-R4 G1 P2: ONE `Add to experiment and open results`
                    // (the footer's); the inline one only outside the wizard.
                    onOpenResults={
                      onOpenResults ? () => onOpenResults(arrival) : undefined
                    }
                    openResultsLabel="Add to experiment and open results"
                    simulateRef={simulateRef}
                    inlineSimulate={false}
                    autoStart={pendingSim}
                    onAutoStart={() => {
                      setPendingSim(false);
                      setTimeout(
                        () =>
                          statusRef.current?.scrollIntoView({
                            block: "center",
                            behavior: "smooth",
                          }),
                        80,
                      );
                    }}
                    openResultsReason={ready.ok ? null : ready.reason}
                    projectHref={dataset?.projectHref}
                  />
                </Box>
              )
            }
          />
        ) : (
          <Slot
            kind={kind}
            mode={state.mode}
            entry={entry}
            value={kind === "harness" ? slot.harness ?? {} : slot}
            onChange={onSlot}
            dataset={dataset}
            ids={{
              datasetId: dataset?.id,
              runLabel,
              runId: reserved?.runId,
              token: undefined,
              // the form's flags reach the CI run line (C1, C3)
              run: ids.run,
            }}
            limits={limits}
            models={declaredModels}
            keyModels={keyModels}
            agentModels={agentModels}
          />
        )}
      </Stack>

      {/* Status (unnumbered; for Run on your machine it sits under step 3) */}
      {(kind === "code" && state.mode === "local") ||
      kind === "harness" ? null : (
        <Stack spacing={1} ref={statusRef}>
          <SectionHeading title="Status" />
          {kind === "code" && state.mode === "ci" ? (
            <TodayStatus
              datasetId={dataset?.id}
              entryId={entry.id}
              runId={reserved?.runId}
              rowCount={rowCount}
              rowNoun={rowNoun}
              openedAtMs={startedAtMs}
              arrival={arrival}
              onArrival={setArrival}
              onSimulate={markStarted}
              simulateRows="all"
              tries={limits.tries}
              config={runConfig}
              rowIds={scopeRowIds}
              splitIds={scopeSplitIds}
              as="ci_job"
              simulateRef={simulateRef}
              inlineSimulate={false}
              autoStart={pendingSim}
              onAutoStart={() => setPendingSim(false)}
              onOpenResults={
                onOpenResults ? () => onOpenResults(arrival) : undefined
              }
              openResultsLabel="Add to experiment and open results"
              openResultsReason={ready.ok ? null : ready.reason}
            />
          ) : (
            <Typography typography="s2" color="text.secondary">
              {state.mode === "sandbox"
                ? "Starts itself in the Future AGI sandbox when you add the run."
                : state.mode === "remote"
                  ? "Future AGI calls your endpoint for each row when you add the run."
                  : "Shows once the run starts."}
            </Typography>
          )}
          {tryState?.error ? (
            <Typography typography="s2" color="error.main">
              {tryState.error}
            </Typography>
          ) : null}
        </Stack>
      )}

      {/* What this run uses (a harness shows it in its config above) */}
      {kind === "harness" ? null : (
        <Section
          id="uses"
          surface="newExperiment"
          scopeId={kind}
          title={VOCAB.WHAT_THIS_RUN_USES}
          defaultOpen={kind === "code" && !usesLine}
          summary={
            usesLine
              ? `${usesLine} · Edit`
              : kind === "code"
                ? declaredSummary(declared) ??
                  "Optional: model, tools and MCP servers"
                : "Filled when your agent connects or uploads its first row"
          }
        >
          <Box sx={{ pl: 3.5, pb: 1 }}>
            {kind === "code" && !usesLine ? (
              <DeclaredUsesForm
                value={declared}
                onChange={setDeclared}
                tools={ids.sandbox?.tools ?? []}
                mcp={ids.sandbox?.name ? [ids.sandbox.name] : []}
              />
            ) : (
              <WhatItUses
                uses={uses}
                values={values}
                onParams={setParams}
                diffSnapshot={diffSnapshot}
                onDiffSnapshot={setDiffSnapshot}
                sourceNote={
                  known
                    ? `Declared by ${known.agent.name} on ${known.machine?.name ?? "your machine"}`
                    : null
                }
              />
            )}
          </Box>
        </Section>
      )}

      {/* 4 Limits and safety */}
      {
        <Section
          id="limits"
          surface="newExperiment"
          scopeId={kind}
          title="Advanced: limits, concurrency, timeouts"
          defaultOpen={false}
          summary={`${limitsLine} · Edit`}
        >
          <Stack spacing={1.25} sx={{ pl: 3.5, pb: 1 }}>
            <LimitsRow
              value={limits}
              onChange={setLimits}
              rowNoun={rowNoun}
              capLine={limits.capLine}
            />
            {/* options-truth L2 G4 (C3): one key row per screen: the CI
                and sandbox panels above show theirs. */}
            {(kind === "code" || kind === "harness") &&
            (state.mode === "ci" || state.mode === "sandbox") ? null : (
              <SecretsRow
                mode={state.mode}
                kind={kind}
                models={keyModels}
                agentModels={agentModels}
                source={secretsSource ?? undefined}
                onSource={setSecretsSource}
              />
            )}
            <SafetyRow rule={rule} value={safety} onChange={setSafety} />
          </Stack>
        </Section>
      }

      <React.Fragment>
        {footerEl ? createPortal(footer, footerEl) : footer}
      </React.Fragment>

      <Dialog
        open={yaml}
        onClose={() => setYaml(false)}
        maxWidth="sm"
        fullWidth
      >
        <DialogTitle typography="m3">Run as code</DialogTitle>
        <DialogContent>
          <Typography typography="s2" color="text.secondary" sx={{ mb: 1 }}>
            This run as experiment.yml: keep it in your repo, review changes
            like code, and start it with the runner.
          </Typography>
          <ScriptBlock
            text={experimentYaml(runConfig, {
              runId: reserved?.runId,
              runName,
              datasetName: dataset?.name,
              rows: scopeLabels,
              split:
                (form?.splits ?? [])
                  .filter((x) => scopeSplitIds.includes(x.id))
                  .map((x) => x.name)
                  .join(", ") || null,
              evals: form?.evalNames ?? [],
              lang,
            })}
            language="yaml"
            copyTooltip="Copy experiment.yml"
            fileName="experiment.yml"
            testId="run-as-code-yaml"
          />
          <Typography
            typography="s2"
            color="text.secondary"
            sx={{ mt: 1.5, mb: 0.5 }}
          >
            The full run config it stands for:
          </Typography>
          <ScriptBlock
            text={`# ${runLabel} ${runName}${toYaml(runConfig)}\n`}
            language="yaml"
            copyTooltip="Copy run config"
          />
        </DialogContent>
      </Dialog>
    </Stack>
  );
}

SourceConfig.propTypes = {
  state: PropTypes.object.isRequired,
  onState: PropTypes.func.isRequired,
  onChangeSource: PropTypes.func.isRequired,
  dataset: PropTypes.object,
  runLabel: PropTypes.string,
  reserved: PropTypes.object,
  openedAtMs: PropTypes.number,
  machines: PropTypes.array,
  initialDraft: PropTypes.object,
  onAdd: PropTypes.func.isRequired,
  onOpenResults: PropTypes.func,
  rowCount: PropTypes.number,
  rowNoun: PropTypes.string,
  tries: PropTypes.number,
  onTries: PropTypes.func,
};

export { DIFF_SNAPSHOT_MAX_BYTES };
