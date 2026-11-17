import React, { useCallback, useEffect, useState } from "react";
import PropTypes from "prop-types";
import {
  Box,
  Button,
  Collapse,
  Divider,
  Link,
  Stack,
  Typography,
} from "@mui/material";
import CopyButton from "src/sections/common/ux/CopyButton";
import { useNavigate, useParams } from "react-router";
import Iconify from "src/components/iconify";
import RunLetterBadge from "./RunLetterBadge";
import { useQueryClient } from "@tanstack/react-query";
import PathTabs from "src/sections/common/RunSources/PathTabs";
import TodayStatus, { PersistedRunStatus } from "src/sections/common/RunSources/TodayStatus";
import RunLedgerLine from "./RunLedgerLine";
import SourceLogo from "src/sections/common/RunSources/SourceLogo";
import { catalogEntry } from "src/sections/common/RunSources/catalog";
import { rowColumnsOf } from "src/sections/common/RunSources/snippets/runLocal";
import { useDatasetKind } from "src/sections/common/Datasets/datasetKind";
import { useRunExperimentStore } from "../states";
import {
  ciStepsOf,
  ciWorkflowPathOf,
  commandArgsOf,
  waitNounOf,
  waitNounOfAll,
  waitingOf,
  waitTitleOf,
} from "./waitingModel";
import ScriptBlock from "src/sections/common/RunSources/ScriptBlock";

/**
 * L2 G3 (L4): a CI run's `How to run` is the workflow's own setup (the
 * file, the secrets and the variable its YAML reads), never the local
 * runner. The names come from the workflow text itself.
 */
const CiHowToRun = ({ yaml }) => {
  const [open, setOpen] = useState(false);
  const steps = ciStepsOf(yaml);
  return (
    <Stack spacing={0.75} data-testid="ci-how-to-run">
      <Stack direction="row" alignItems="center" gap={0.75}>
        <Box
          component="code"
          sx={{
            flex: 1,
            minWidth: 0,
            px: 1,
            py: 0.5,
            borderRadius: 0.5,
            bgcolor: "background.neutral",
            typography: "s2",
            fontFamily: "monospace",
            whiteSpace: "nowrap",
            overflow: "hidden",
            textOverflow: "ellipsis",
          }}
        >
          {ciWorkflowPathOf(yaml)}
        </Box>
        <CopyButton value={yaml ?? ""} tooltip="Copy the workflow" />
        <Link
          component="button"
          type="button"
          typography="s2"
          onClick={() => setOpen((v) => !v)}
          aria-expanded={open}
          data-testid="how-to-run"
          sx={{ whiteSpace: "nowrap" }}
        >
          {open ? "Hide how to run" : "How to run"}
        </Link>
      </Stack>
      <Collapse in={open} unmountOnExit>
        <Box component="ol" sx={{ m: 0, pl: 2.5, typography: "s2" }}>
          {steps.map((t) => (
            <li key={t}>{t}</li>
          ))}
        </Box>
      </Collapse>
    </Stack>
  );
};
CiHowToRun.propTypes = { yaml: PropTypes.string };

/**
 * R4 (lane Ra): a Trace + upload run created from Level 3 keeps the same
 * two path tabs and the same complete script (the ids were reserved, so
 * nothing changed), with the checklist waiting for its uploads.
 */
const R4RunWaiting = ({
  run,
  r4,
  datasetId,
  experimentId,
  launched,
  onOpen,
}) => {
  const entry = catalogEntry(r4.entryId);
  const [form, setForm] = useState(r4.form ?? "upload");
  const [view, setView] = useState(r4.view ?? "cli");
  const [lang, setLang] = useState(r4.lang ?? entry?.languages?.[0]);
  // IT-R1 G1 P5: the draft's checklist carries over (a passed T1 stays
  // passed); the clock runs from when the user copied the command, or now
  // (the run is added).
  const [arrival, setArrival] = useState(r4.arrival ?? null);
  const [openedAt] = useState(() => r4.startedAtMs ?? Date.now());
  const queryClient = useQueryClient();
  const { kind: datasetKind } = useDatasetKind(datasetId);
  // IT-R2 G1 P2 (rule 18): the numbered setup lives in the run panel; here
  // the state, the command with Copy, and `How to run` (the same steps,
  // collapsed).
  const [howOpen, setHowOpen] = useState(false);
  const markIn = (a) => {
    const st = useRunExperimentStore.getState();
    if (st.launched)
      st.setLaunched({
        ...st.launched,
        resultsIn: {
          ...(st.launched.resultsIn ?? {}),
          // options-truth L3 G3 (cost cap, made by G8): keep the tries
          // planned and the stop, so the drawer title says the run stopped.
          [run.run_id]: {
            done: a.uploaded,
            total: a.total ?? a.uploaded,
            stop_line: a.stop_line ?? null,
            rows_done: a.rows_done ?? null,
            rows_total: a.rows_total ?? null,
          },
        },
      });
  };
  useEffect(() => {
    // A draft whose simulated rows were all in before Run: results are in.
    if (r4.arrival?.finished) markIn(r4.arrival);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);
  const ids = {
    datasetId,
    datasetName: launched.datasetName,
    experimentId,
    runId: run.run_id,
    runLabel: run.label,
    inputColumn: launched.inputColumn,
    tries: launched.trials,
    agentName: r4.agentName,
    keys: launched.keys,
    sandbox: r4.sandbox ?? null,
    columns: rowColumnsOf(launched.sample?.columns),
  };
  // How this run waits, by its mode (local / ci / sandbox; S2 stand-ins).
  const wait = waitingOf(run, r4, { datasetName: launched.datasetName });
  const runCfg = r4.config ?? run.config ?? {};
  // L5 (I6): the run's own flags (tries, at a time, cap, time limit,
  // changed params, picked rows) ride on every command this panel prints.
  const args = commandArgsOf({ ...run, config: runCfg });
  ids.run = args.flags;
  ids.tries = args.tries;
  const sandbox = wait?.mode === "sandbox";
  const ci = wait?.mode === "ci";
  // The same line the experiment banner, the tray and Review print.
  const command = wait?.mode === "local" ? wait.command : null;
  const rowNoun = datasetKind === "task_suite" ? "task" : "row";
  return (
    <Stack spacing={1.5} data-testid="r4-run-waiting">
      <Box sx={{ display: "flex", alignItems: "center", gap: 1 }}>
        <SourceLogo entry={entry} size={20} />
        <Typography typography="s1" fontWeight="fontWeightSemiBold">
          {run.label} · {r4.runName ?? run.name}
        </Typography>
        {wait ? (
          <Typography
            typography="s2"
            color="text.secondary"
            data-testid="r4-waiting-title"
          >
            {waitTitleOf(wait, arrival)}
          </Typography>
        ) : null}
      </Box>
      {wait?.mode === "ci" && !arrival?.finished ? (
        <ScriptBlock
          text={wait.command}
          language="yaml"
          copyTooltip="Copy the workflow"
          testId="r4-waiting-ci-yaml"
          fileName={ciWorkflowPathOf(wait.command).split("/").pop()}
        />
      ) : null}
      <TodayStatus
        as={sandbox ? "sandbox" : wait?.as ?? "terminal"}
        // L2 G3 (L4): a sandbox run starts itself; the panel follows it.
        follow={sandbox}
        waitingText={sandbox ? wait.title : undefined}
        config={r4.config}
        inlineSimulate={Boolean(wait?.simulate)}
        datasetId={datasetId}
        entryId={r4.entryId}
        runId={run.run_id}
        rowCount={launched.examples ?? undefined}
        rowNoun={rowNoun}
        openedAtMs={openedAt}
        arrival={arrival}
        simulateRows="all"
        tries={launched.trials}
        onArrival={(a) => {
          setArrival(a);
          if (a?.finished) markIn(a);
          queryClient.invalidateQueries({
            queryKey: ["develop", "dataset-runs", datasetId],
          });
        }}
        // The panel's footer holds the one next step (Open results).
        openResultsReason={null}
      />
      <RunLedgerLine
        experimentId={experimentId}
        runId={run.run_id}
        noun={rowNoun}
      />
      {ci && !arrival?.finished ? <CiHowToRun yaml={wait.command} /> : null}
      {entry && command && !arrival?.finished ? (
        <Stack
          direction="row"
          alignItems="center"
          gap={0.75}
          sx={{ minWidth: 0 }}
          data-testid="run-command-line"
        >
          <Box
            component="code"
            sx={{
              minWidth: 0,
              flex: 1,
              px: 1,
              py: 0.5,
              borderRadius: 0.5,
              bgcolor: "background.neutral",
              typography: "s2",
              fontFamily: "monospace",
              whiteSpace: "nowrap",
              overflow: "hidden",
              textOverflow: "ellipsis",
            }}
            title={command ?? undefined}
          >
            {command}
          </Box>
          <CopyButton value={command ?? ""} tooltip="Copy command" />
          <Link
            component="button"
            type="button"
            typography="s2"
            onClick={() => setHowOpen((v) => !v)}
            aria-expanded={howOpen}
            data-testid="how-to-run"
            sx={{ whiteSpace: "nowrap" }}
          >
            {howOpen ? "Hide how to run" : "How to run"}
          </Link>
        </Stack>
      ) : null}
      {entry && command ? (
        <Collapse in={howOpen} unmountOnExit>
          <PathTabs
            rowNoun={rowNoun}
            entry={entry}
            form={form}
            onForm={setForm}
            view={view}
            onView={setView}
            lang={lang}
            onLang={setLang}
            sample={launched.sample}
            ids={ids}
          />
        </Collapse>
      ) : null}
    </Stack>
  );
};
R4RunWaiting.propTypes = {
  run: PropTypes.object.isRequired,
  r4: PropTypes.object.isRequired,
  datasetId: PropTypes.string,
  experimentId: PropTypes.string,
  launched: PropTypes.object.isRequired,
  onOpen: PropTypes.func,
};

// Local framework runs read their persisted header/items directly. Do not mount
// the prototype activity/ledger or invalidate the launch-held run catalogue.
const LocalRunWaiting = ({ run, r4 }) => {
  const entry = catalogEntry(r4.entryId);
  const wait = waitingOf(run, r4);
  const markIn = useCallback((arrival) => {
    const st = useRunExperimentStore.getState();
    if (!st.launched?.runs?.some((item) => item.run_id === run.run_id)) return;
    const previous = st.launched.resultsIn?.[run.run_id];
    if (previous?.done === arrival.uploaded && previous?.total === arrival.total &&
        previous?.status === arrival.status) return;
    st.setLaunched({
      ...st.launched,
      resultsIn: { ...st.launched.resultsIn, [run.run_id]: {
        done: arrival.uploaded, total: arrival.total, status: arrival.status,
      } },
    });
  }, [run.run_id]);
  return (
    <Stack spacing={1.5} data-testid="local-run-waiting">
      <Stack direction="row" alignItems="center" gap={1}>
        <SourceLogo entry={entry} size={20} />
        <Typography typography="s1" fontWeight="fontWeightSemiBold">
          {run.label} · {r4.runName ?? run.name}
        </Typography>
      </Stack>
      <PersistedRunStatus key={run.run_id} runId={run.run_id} onArrival={markIn} />
      {wait?.command ? (
        <Stack spacing={0.5}>
          <Stack direction="row" alignItems="center" gap={1} data-testid="run-command-line">
            <Box component="code" sx={{ minWidth: 0, overflowWrap: "anywhere" }}>{wait.command}</Box>
            <CopyButton value={wait.command} tooltip="Copy command" />
          </Stack>
          <Typography typography="s2" color="text.secondary">
            Run from your project with the Future AGI SDK installed and your API keys set in that shell.
            Replace the file:export target with your own runnable. Keep this run ID to send results here.
            This page only reads results; Retry does not execute your code.
          </Typography>
        </Stack>
      ) : wait?.unavailable ? (
        // The SDK runner is not released: the run command keeps its place
        // and says so, with no line to copy.
        <Stack
          direction="row"
          alignItems="center"
          gap={1}
          data-testid="run-command-unavailable"
        >
          <Typography typography="s2" fontWeight="fontWeightMedium">
            Run command
          </Typography>
          <Typography
            typography="s2"
            color="text.secondary"
            data-not-in-release=""
          >
            {wait.unavailable}
          </Typography>
        </Stack>
      ) : null}
    </Stack>
  );
};
LocalRunWaiting.propTypes = {
  run: PropTypes.object.isRequired,
  r4: PropTypes.object.isRequired,
};

const RunUpload = ({ run }) => (
  <Box sx={{ display: "flex", alignItems: "center", gap: 1 }}>
    <RunLetterBadge letter={run.label.replace(/^#\d+/, "")} />
    <Typography typography="s1" fontWeight="fontWeightMedium">
      {run.label} · {run.name}
    </Typography>
    <Typography typography="s2" color="text.secondary">
      {`${run.label} waits for ${waitNounOf({ target_type: "external", ...run })}`}
    </Typography>
  </Box>
);
RunUpload.propTypes = {
  run: PropTypes.object.isRequired,
};

/**
 * Last panel of a create whose runs are all From code (PRODUCT-SPEC §3.8):
 * the snippets now carry the real experiment and run ids.
 */
const WaitingForResultsPanel = ({ launched, onDone }) => {
  const navigate = useNavigate();
  const resultsIn = Object.values(launched?.resultsIn ?? {});
  const allIn =
    resultsIn.length > 0 && resultsIn.length >= (launched?.runs ?? []).length;
  const { dataset: datasetId } = useParams();
  const { experimentId, ordinal, runs = [], examples, trials = 1 } = launched;
  const openExperiment = () => {
    onDone();
    navigate(`/dashboard/develop/experiment/${experimentId}/data`);
  };
  // R4 (lane Ra): runs added from Level 3 keep their path tabs.
  const r4 = launched.r4 ?? [];
  const localRuns = runs.map((run, i) => r4[i] &&
    catalogEntry(r4[i].entryId)?.kind === "code" &&
    waitingOf(run, r4[i])?.mode === "local");
  const onlyLocal = runs.length > 0 && localRuns.every(Boolean);
  if (r4.some(Boolean))
    return (
      <Stack spacing={2} sx={{ flex: 1, overflowY: "auto", pb: 2, pr: 1 }}>
        {runs.map((run, i) => (
          <React.Fragment key={run.run_id}>
            {i > 0 ? <Divider /> : null}
            {localRuns[i] ? (
              <LocalRunWaiting run={run} r4={r4[i]} />
            ) : r4[i] ? (
              <R4RunWaiting
                run={run}
                r4={r4[i]}
                datasetId={datasetId}
                experimentId={experimentId}
                launched={launched}
                onOpen={openExperiment}
              />
            ) : (
              <RunUpload run={run} />
            )}
          </React.Fragment>
        ))}
        <Box
          sx={{ display: "flex", justifyContent: "flex-end", gap: 1.5, pt: 1 }}
        >
          <Button variant="outlined" onClick={onDone} sx={{ minWidth: 120 }}>
            Done
          </Button>
          {!onlyLocal ? (
            <Button
              variant="contained"
              color="primary"
              sx={{ minWidth: 143 }}
              onClick={openExperiment}
              data-testid="open-experiment"
            >
              {allIn ? "Open results" : "Open experiment"}
            </Button>
          ) : null}
        </Box>
      </Stack>
    );
  return (
    <Stack spacing={2} sx={{ flex: 1, overflowY: "auto", pb: 2 }}>
      <Box
        sx={{
          display: "flex",
          gap: 1.25,
          alignItems: "flex-start",
          p: 1.5,
          borderRadius: 0.5,
          bgcolor: "blue.o5",
          border: "1px solid",
          borderColor: "blue.o20",
        }}
      >
        <Iconify
          icon="mdi:timer-sand"
          width={20}
          sx={{ color: "blue.600", mt: 0.25 }}
        />
        <Box>
          <Typography typography="s1" fontWeight="fontWeightSemiBold">
            {`Experiment #${ordinal} is waiting for ${waitNounOfAll(runs)}`}
          </Typography>
          <Typography typography="s2" color="text.secondary">
            {runs.length === 1
              ? "Its run expects"
              : `Its ${runs.length} runs expect`}{" "}
            {examples != null ? `${examples} examples` : "every example"}
            {trials > 1 ? ` × ${trials} trials` : ""} from {waitNounOfAll(runs)}
            . Each upload moves a run to Running, then Scoring, then Completed.
          </Typography>
        </Box>
      </Box>
      {runs.map((run, i) => (
        <React.Fragment key={run.run_id}>
          {i > 0 ? <Divider /> : null}
          <RunUpload run={run} />
        </React.Fragment>
      ))}
      <Box
        sx={{ display: "flex", justifyContent: "flex-end", gap: 1.5, pt: 1 }}
      >
        <Button variant="outlined" onClick={onDone} sx={{ minWidth: 120 }}>
          Done
        </Button>
        <Button
          variant="contained"
          color="primary"
          sx={{ minWidth: 143 }}
          onClick={() => {
            onDone();
            navigate(`/dashboard/develop/experiment/${experimentId}/data`);
          }}
        >
          Open experiment
        </Button>
      </Box>
    </Stack>
  );
};

WaitingForResultsPanel.propTypes = {
  launched: PropTypes.object.isRequired,
  onDone: PropTypes.func.isRequired,
};

export default WaitingForResultsPanel;
