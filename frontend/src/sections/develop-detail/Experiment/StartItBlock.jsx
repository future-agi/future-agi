// IT-R1 G1 P4: `Start it` on Review & run. When any run executes outside
// Future AGI (Your code on this machine, a harness on this machine), the
// review says how to start each one: tab `Terminal` lists per run the exact
// command with Copy, plus `Copy all as one script` (run-experiment-4.sh,
// `&` + `wait`); tab `CI` shows a GitHub Action running the same file.
// Every line comes from the runs' own snippets (snippets/startIt.js).
import React, { useMemo, useState } from "react";
import { useRunActivity } from "src/api/develop/r4";
import PropTypes from "prop-types";
import {
  Box,
  Button,
  Link,
  Stack,
  ToggleButton,
  ToggleButtonGroup,
  Typography,
} from "@mui/material";
import Iconify from "src/components/iconify";
import CopyButton, { copyText } from "src/sections/common/ux/CopyButton";
import { notify } from "src/sections/common/ux/toast";
import ScriptBlock from "src/sections/common/RunSources/ScriptBlock";
import { startItPlan } from "src/sections/common/RunSources/snippets/startIt";
import {
  ciCommitFiles,
  ciExperimentWorkflow,
  ciSecretCommands,
  ciSecrets,
  ciWorkflow,
  ciWorkflowPath,
} from "src/sections/common/RunSources/snippets/ci";
import { CI_PATH } from "src/sections/common/RunSources/snippets/lines";
import {
  catalogEntry,
  isOffered,
} from "src/sections/common/RunSources/catalog";
import RunLetterBadge from "./RunLetterBadge";

const TOP_CENTER = { vertical: "top", horizontal: "center" };
const MONO =
  '"JetBrains Mono", "SFMono-Regular", Menlo, Consolas, "Liberation Mono", monospace';

/**
 * dataset integration FE-0 (backstop): only a card whose source the picker offers is
 * started from here; a code or harness card carried over from an earlier
 * run prints no generated script, workflow or start line.
 */
const offeredCard = (r4) => Boolean(r4) && isOffered(r4.entryId);

/** The runs of a draft that the user starts on their machine (pure). */
export function terminalRunsOf(cards = [], runs = []) {
  return runs
    .map((r) => ({ run: r, r4: cards?.[r.configIndex]?.r4 ?? null }))
    .filter(
      ({ r4 }) =>
        offeredCard(r4) &&
        r4.mode === "local" &&
        ((r4.kind === "code" && r4.form !== "connect") ||
          // a harness bound to a connected worker starts on it (N3)
          (r4.kind === "harness" && !r4.config?.execution?.machine_id)),
    );
}

/** The runs that start themselves: sandbox, remote (pure). */
export function selfStartRunsOf(cards = [], runs = []) {
  return runs
    .map((r) => ({ run: r, r4: cards?.[r.configIndex]?.r4 ?? null }))
    .filter(
      ({ r4 }) => offeredCard(r4) && ["sandbox", "remote"].includes(r4.mode),
    );
}

/** `Starts itself in the Future AGI sandbox` / `Future AGI calls <host>…`. */
export function selfStartLine({ r4 }) {
  if (r4?.mode === "sandbox") return "Starts itself in the Future AGI sandbox";
  const url = r4?.slot?.endpoint?.url ?? r4?.config?.endpoint?.url ?? "";
  let host = "your endpoint";
  try {
    host = new URL(url).host || host;
  } catch {
    // keep `your endpoint`
  }
  return `Future AGI calls ${host} for each row`;
}

/** IT-R4 G1 P5: the runs of a draft that start in CI (pure). */
export function ciRunsOf(cards = [], runs = []) {
  return runs
    .map((r) => ({ run: r, r4: cards?.[r.configIndex]?.r4 ?? null }))
    .filter(
      ({ r4 }) =>
        offeredCard(r4) &&
        r4.mode === "ci" &&
        ["code", "harness"].includes(r4.kind),
    );
}

const AGENT_WORDS = {
  custom_typescript: "your TypeScript agent",
  custom_python: "your Python agent",
};
const runWord = ({ run, r4 }) =>
  AGENT_WORDS[r4?.entryId] ?? r4?.entryName ?? run?.name ?? "this run";
const andList = (xs) =>
  xs.length <= 1
    ? xs[0] ?? ""
    : `${xs.slice(0, -1).join(", ")} and ${xs[xs.length - 1]}`;

/**
 * IT-R4 G1 P5: where each run starts, in one line (pure):
 * `Claude Code runs in CI (GitHub Actions) · Codex CLI and your TypeScript
 * agent run on this machine`. `Every run starts on your machine` only when
 * every run does.
 */
export function startWhereLine(machine = [], ci = [], total = 0) {
  if (machine.length && !ci.length && machine.length === total)
    return "Every run starts on your machine; each row lands here as it finishes.";
  const parts = [];
  if (ci.length) {
    const names = ci.map(runWord);
    parts.push(
      `${andList(names)} ${names.length === 1 ? "runs" : "run"} in CI (GitHub Actions)`,
    );
  }
  if (machine.length) {
    const names = machine.map(runWord);
    parts.push(
      `${andList(names)} ${names.length === 1 ? "runs" : "run"} on this machine`,
    );
  }
  return parts.join(" · ");
}

const copyAndSay = async (text, what) => {
  const ok = await copyText(text);
  notify(
    ok
      ? `${what} copied`
      : "Couldn't copy. Your browser blocked the clipboard.",
    {
      variant: ok ? "success" : "error",
      key: "start-it-copy",
      anchorOrigin: TOP_CENTER,
    },
  );
};

/**
 * IT-R2 G1 P2: one status line per run (the steps live in the run panel):
 * `not started` · `simulated 7/24 · will continue after Run experiment`.
 */
export function startStatusLine(r4, act) {
  if (act?.draft) {
    const prog = `${act.done}/${act.total}`;
    return act.state === "done"
      ? `simulated ${prog} · ${act.passed} passed · ${act.failed} failed · results open after Run experiment`
      : `simulated ${prog} · will continue after Run experiment`;
  }
  if (r4?.arrival)
    return r4.arrival.finished
      ? `simulated · ${r4.arrival.passed_count ?? 0} passed · ${r4.arrival.failed_count ?? 0} failed`
      : "simulated · will continue after Run experiment";
  return r4?.kind === "harness"
    ? "not started · runs when you start its command"
    : "not started · nothing runs until you start it";
}

function RunStatusRow({ run, r4, r, onCopyScript, defaultOpen = false }) {
  // IT-R4 G1 P5: the first run's command shows without a click.
  const [open, setOpen] = useState(defaultOpen);
  const runId = r4?.reserved?.runId ?? null;
  const live = useRunActivity(runId, {
    enabled: Boolean(runId && r4?.arrival),
    poll: 1000,
  });
  const act = live.data?.draft ? live.data : null;
  // knob:run-name: the run's own name, as the user typed it
  const name = r4?.runName ?? run.name ?? r4?.entryName;
  return (
    <Box data-testid="start-it-run">
      <Stack direction="row" alignItems="center" gap={1} sx={{ minWidth: 0 }}>
        <RunLetterBadge letter={run.letter} size={18} />
        <Typography
          typography="s2"
          fontWeight="fontWeightMedium"
          noWrap
          sx={{ flexShrink: 0, maxWidth: 220 }}
          title={`${r.label} ${run.name}`}
        >
          {`${r.label} ${name}`}
        </Typography>
        <Typography
          typography="s2"
          color="text.secondary"
          noWrap
          sx={{ flex: 1, minWidth: 0 }}
          data-testid="start-it-status"
        >
          {`· ${startStatusLine(r4, act)}`}
        </Typography>
        <CopyButton value={r.command} tooltip={`Copy ${r.label}'s command`} />
        <Link
          component="button"
          type="button"
          typography="s3"
          onClick={() => setOpen((v) => !v)}
          aria-expanded={open}
          sx={{ flexShrink: 0 }}
        >
          {open ? "Hide command" : "Show command"}
        </Link>
      </Stack>
      {open ? (
        <Box sx={{ pl: 3.25, pt: 0.5 }}>
          <Box
            component="code"
            sx={{
              display: "block",
              px: 1,
              py: 0.5,
              borderRadius: 0.5,
              bgcolor: "background.neutral",
              fontFamily: MONO,
              fontSize: 12,
              whiteSpace: "pre-wrap",
              wordBreak: "break-all",
            }}
          >
            {r.command}
          </Box>
          <Link
            component="button"
            type="button"
            typography="s3"
            onClick={onCopyScript}
            sx={{ mt: 0.25 }}
          >
            {`Copy ${r.folder === "." ? r.scriptName : `${r.folder}/${r.scriptName}`}`}
          </Link>
        </Box>
      ) : null}
    </Box>
  );
}
RunStatusRow.propTypes = {
  run: PropTypes.object.isRequired,
  r4: PropTypes.object,
  r: PropTypes.object.isRequired,
  onCopyScript: PropTypes.func,
  defaultOpen: PropTypes.bool,
};

export default function StartItBlock({ cards, runs, ordinal, ctx }) {
  const list = terminalRunsOf(cards, runs);
  const ciList = ciRunsOf(cards, runs);
  const selfList = selfStartRunsOf(cards, runs);
  const [tab, setTab] = useState(() =>
    list.length || !ciList.length ? "terminal" : "ci",
  );
  const planOf = (entries) =>
    startItPlan({
      ordinal,
      sandbox:
        entries.find(({ r4 }) => r4.sandbox && !r4.runFlags?.noSandbox)?.r4
          ?.sandbox ?? null,
      runs: entries.map(({ run, r4 }) => {
        const label = ordinal != null ? `#${ordinal}${run.letter}` : run.letter;
        return {
          label,
          name: r4.runName ?? run.name,
          kind: r4.kind,
          entryId: r4.entryId,
          lang: r4.lang,
          recipe: r4.slot?.harness ?? r4.config?.harness ?? undefined,
          // options-truth L2 G4 (C1): the card's limits and form flags, so
          // Start it prints what the drawer and the waiting panel print
          limits: r4.limits,
          flags: r4.runFlags,
          // every model the run calls: its key goes in Start it's CI env
          models: (r4.config?.models ?? []).map((m) => m?.id ?? m),
          ids: {
            datasetId: ctx?.datasetId,
            datasetName: ctx?.datasetName,
            experimentId: r4.reserved?.experimentId,
            runId: r4.reserved?.runId,
            inputColumn: ctx?.inputColumn,
            tries: r4.limits?.tries,
            agentName: r4.agentName,
            keys: ctx?.prototypeKeys,
            sandbox: r4.kind === "code" ? r4.sandbox ?? null : null,
          },
        };
      }),
    });
  const plan = useMemo(
    () => planOf(list),
    // eslint-disable-next-line react-hooks/exhaustive-deps
    [
      JSON.stringify(
        list.map(({ run, r4 }) => [
          run.letter,
          r4.entryId,
          r4.lang,
          r4.limits,
          r4.runFlags,
        ]),
      ),
      ordinal,
    ],
  );
  // IT-R4 G1 P5 / L3 G6 (S3): the CI tab shows the runs set to CI (else
  // the machine runs, as a way to move them to CI). One CI run: exactly the
  // file its L3 CI panel shows (fi-experiment-<label>.yml, FI_RUN_ID).
  // Several: Start it's fi-experiment.yml, one job per run in its folder.
  const ciPlan = useMemo(
    () => {
      if (!ciList.length) {
        const keys = plan.secrets ?? ["FI_API_KEY", "FI_SECRET_KEY"];
        return {
          ...plan,
          instruction: `Commit ${plan.shellName} and each run's folder, add ${andList(keys)} as repository secrets, then run this workflow.`,
          secretLines: null,
        };
      }
      const labelOf = (run) =>
        ordinal != null ? `#${ordinal}${run.letter}` : run.letter;
      const cfgOf = ({ run, r4 }) => {
        const entry = catalogEntry(r4.entryId) ?? undefined;
        const recipe =
          r4.kind === "harness"
            ? r4.slot?.harness ?? r4.config?.harness ?? undefined
            : undefined;
        return {
          entry,
          label: labelOf(run),
          runId: r4.reserved?.runId,
          ci: r4.slot?.ci,
          limits: r4.limits,
          models: (r4.config?.models ?? []).map((m) => m?.id ?? m),
          recipe,
          flags: r4.runFlags,
        };
      };
      const all = ciList.map(cfgOf);
      const secrets = [
        ...new Set(
          all.flatMap((c) =>
            ciSecrets({
              entry: c.entry,
              models: c.models,
              recipe: c.recipe,
              params: c.flags?.params,
            }),
          ),
        ),
      ];
      const filesOf = (c) =>
        ciCommitFiles({
          entry: c.entry,
          ids: { runLabel: c.label, runId: c.runId, run: c.flags },
          recipe: c.recipe,
        }).map((f) => f.fileName);
      if (all.length === 1) {
        const c = all[0];
        const path = ciWorkflowPath(c.label);
        return {
          ciName: path,
          ci: ciWorkflow({
            entry: c.entry,
            ids: {
              datasetName: ctx?.datasetName,
              runId: c.runId,
              runLabel: c.label,
            },
            ci: c.ci,
            limits: c.limits,
            models: c.models,
            recipe: c.recipe,
            flags: c.flags,
          }),
          instruction: `Commit ${andList([path, ...filesOf(c)])}, add ${andList(secrets)} as repository secrets and the FI_RUN_ID variable (this run's id).`,
          secretLines: ciSecretCommands({
            entry: c.entry,
            models: c.models,
            recipe: c.recipe,
            params: c.flags?.params,
            runId: c.runId,
          }),
        };
      }
      const withFolders = all.map((c) => ({
        ...c,
        folder: `run-${String(c.label).replace(/^#/, "")}`,
      }));
      return {
        ciName: CI_PATH,
        ci: ciExperimentWorkflow({
          ordinal,
          datasetName: ctx?.datasetName,
          runs: withFolders,
        }),
        instruction: `Commit ${CI_PATH} and each run's folder (${withFolders
          .map((c) => `${c.folder}/: ${filesOf(c).join(", ")}`)
          .join(" · ")}), then add ${andList(secrets)} as repository secrets.`,
        secretLines: secrets.map((k) => `gh secret set ${k}`).join("\n"),
      };
    },
    // eslint-disable-next-line react-hooks/exhaustive-deps
    [
      JSON.stringify(
        ciList.map(({ run, r4 }) => [
          run.letter,
          r4.entryId,
          r4.lang,
          r4.limits,
          r4.runFlags,
          r4.slot?.ci,
          r4.slot?.harness,
          r4.reserved?.runId,
        ]),
      ),
      plan,
    ],
  );
  if (!list.length && !ciList.length && !selfList.length) return null;
  return (
    <Box
      data-testid="start-it"
      sx={{
        border: "1px solid",
        borderColor: "divider",
        borderRadius: 1,
        px: 1.5,
        py: 1.25,
        bgcolor: "background.paper",
      }}
    >
      <Stack direction="row" alignItems="center" gap={1} sx={{ mb: 0.5 }}>
        <Typography typography="s1" fontWeight="fontWeightSemiBold">
          Start it
        </Typography>
        <Typography
          typography="s2"
          color="text.secondary"
          sx={{ flex: 1, minWidth: 0 }}
        >
          {startWhereLine(list, ciList, runs.length)}
        </Typography>
        <ToggleButtonGroup
          size="small"
          exclusive
          value={tab}
          onChange={(_, v) => v && setTab(v)}
          aria-label="Where you start it"
          sx={{
            "& .MuiToggleButton-root": {
              py: 0.25,
              px: 1,
              typography: "s2",
              textTransform: "none",
            },
          }}
        >
          <ToggleButton value="terminal" data-testid="start-it-terminal">
            Terminal
          </ToggleButton>
          <ToggleButton value="ci" data-testid="start-it-ci">
            CI
          </ToggleButton>
        </ToggleButtonGroup>
      </Stack>
      {selfList.length ? (
        <Stack spacing={0.25} sx={{ mb: 0.75 }} data-testid="start-it-self">
          {selfList.map((x) => (
            <Typography
              key={x.run.letter}
              typography="s2"
              color="text.secondary"
            >
              {`${ordinal != null ? `#${ordinal}${x.run.letter}` : x.run.letter} ${x.r4.runName ?? x.run.name} · ${selfStartLine(x)}`}
            </Typography>
          ))}
        </Stack>
      ) : null}
      {!list.length && !ciList.length ? null : tab === "terminal" ? (
        <Stack spacing={0.75}>
          {/* IT-R2 G1 P9: the bundled script first, then one line per run. */}
          {!list.length ? (
            <Typography typography="s2" color="text.secondary">
              No run starts on this machine. See the CI tab.
            </Typography>
          ) : null}
          {/* L3 G6: a CI run says where it runs (its workflow file on the
              CI tab), never a script this tab does not show. */}
          {ciList.map(({ run, r4 }) => {
            const label =
              ordinal != null ? `#${ordinal}${run.letter}` : run.letter;
            return (
              <Typography
                key={`ci-${run.letter}`}
                typography="s2"
                color="text.secondary"
                data-testid="start-it-ci-run"
              >
                {`${label} ${r4.runName ?? run.name} · runs in CI (GitHub Actions) from ${ciList.length === 1 ? ciWorkflowPath(label) : CI_PATH}: see the CI tab`}
              </Typography>
            );
          })}
          <Stack
            direction="row"
            alignItems="center"
            gap={1}
            sx={{ display: list.length ? undefined : "none" }}
          >
            <Button
              size="small"
              variant="outlined"
              onClick={() => copyAndSay(plan.shell, plan.shellName)}
              startIcon={<Iconify icon="mdi:content-copy" width={14} />}
              data-testid="copy-all-start"
              sx={{ typography: "s2", whiteSpace: "nowrap", flexShrink: 0 }}
            >
              Copy all as one script
            </Button>
            <Typography typography="s3" color="text.secondary">
              {`${plan.shellName}: starts ${plan.runs.length === 1 ? "the run" : `all ${plan.runs.length} together`} and waits${plan.runs.length === 1 ? "" : " for them"}`}
            </Typography>
          </Stack>
          {list.length ? (
            <ScriptBlock
              text={`bash ${plan.shellName}`}
              language="bash"
              dense
              copyTooltip="Copy the command"
              testId="start-it-one-script"
            />
          ) : null}
          {plan.runs.map((r, i) => (
            <RunStatusRow
              key={r.label}
              run={list[i].run}
              r4={list[i].r4}
              r={r}
              defaultOpen={i === 0}
              onCopyScript={() =>
                copyAndSay(
                  r.script,
                  r.folder === "."
                    ? r.scriptName
                    : `${r.folder}/${r.scriptName}`,
                )
              }
            />
          ))}
        </Stack>
      ) : (
        <Stack spacing={0.75}>
          <Typography
            typography="s2"
            color="text.secondary"
            data-testid="start-it-ci-instruction"
          >
            {ciPlan.instruction}
          </Typography>
          <ScriptBlock
            text={ciPlan.ci}
            language="yaml"
            copyTooltip={`Copy ${ciPlan.ciName}`}
            testId="start-it-ci-yaml"
            fileName={ciPlan.ciName.split("/").pop()}
          />
          {ciPlan.secretLines ? (
            <ScriptBlock
              text={ciPlan.secretLines}
              language="bash"
              dense
              copyTooltip="Copy the gh commands"
              testId="start-it-ci-secrets"
            />
          ) : null}
        </Stack>
      )}
    </Box>
  );
}

StartItBlock.propTypes = {
  cards: PropTypes.array,
  runs: PropTypes.array,
  ordinal: PropTypes.number,
  ctx: PropTypes.object,
};
