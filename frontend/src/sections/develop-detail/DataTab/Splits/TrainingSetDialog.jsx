// Export as training set (W9-5, DG-06), opened from Manage splits: train /
// validation / test files from three splits of current saved rows, in
// OpenAI fine-tune JSONL, after a leakage check. Rows are read by
// common/export/datasetExport.js; the lines and files come from
// trainingSetModel.js; every file is written by common/export/writers.js.
// No fine-tune job is started here: the files go to the person's provider,
// and the tuned model comes back as a custom model (Settings › AI Providers).
import React, { useMemo, useState } from "react";
import PropTypes from "prop-types";
import {
  Box,
  Button,
  Dialog,
  DialogActions,
  DialogContent,
  DialogTitle,
  LinearProgress,
  Stack,
  Typography,
} from "@mui/material";
import { useQueries } from "@tanstack/react-query";
import { useNavigate } from "react-router-dom";
import { notify } from "src/sections/common/ux/toast";
import { fmtCount } from "src/sections/common/format/fmt";
import { isSurfaceShown } from "src/launch/launchSwitch";
import { splitScopeShown } from "../../Experiment/splitScopeVisibility";
import {
  defaultMessageRoles,
  fetchDatasetRows,
  formatBlocker,
  saveFile,
  userColumnsOf,
} from "src/sections/common/export/datasetExport";
import { trainingSetDoor } from "./trainingDoor";
import { useRunExperimentStore } from "src/sections/develop-detail/states";
import { useManageSplitsStore } from "./splitStores";
import {
  TRAINING_PARTS,
  guessSplitRoles,
  planTrainingSet,
  splitNotFileLine,
  trainingFiles,
} from "./trainingSetModel";
import {
  MessagesSection,
  Pick,
  PlanSummary,
  SavedFiles,
} from "./TrainingSetSections";

/**
 * The dialog. `splits` = the dataset's splits (Manage splits' read).
 * @param {{open: boolean, onClose: Function, datasetId: string,
 *   splits: {id: string, name: string}[]}} props
 */
export function TrainingSetDialog({ open, onClose, datasetId, splits }) {
  const navigate = useNavigate();
  const version = null;
  const isLatest = true;
  const [choice, setChoiceState] = useState(() => guessSplitRoles(splits));
  const [roles, setRolesState] = useState(null);
  const [system, setSystem] = useState("");
  const [keepOverlap, setKeepOverlap] = useState(false);
  const [leaveOut, setLeaveOut] = useState({});
  const [saved, setSaved] = useState(null);
  // A different read or mapping is a different finding: fixes start over.
  const resetChecks = () => {
    setKeepOverlap(false);
    setLeaveOut({});
  };
  const setChoice = (key, id) => {
    setChoiceState((c) => ({ ...c, [key]: id }));
    resetChecks();
  };
  const setRoles = (next) => {
    setRolesState(next);
    resetChecks();
  };

  const nameOf = useMemo(
    () => Object.fromEntries((splits ?? []).map((s) => [s.id, s.name])),
    [splits],
  );
  const chosen = TRAINING_PARTS.filter((p) => choice[p.key]);
  const reads = useQueries({
    queries: chosen.map((p) => ({
      queryKey: [
        "training-set",
        datasetId,
        version?.id ?? "latest",
        choice[p.key],
      ],
      queryFn: () =>
        fetchDatasetRows({
          datasetId,
          version,
          isLatest,
          splitIds: [choice[p.key]],
        }),
      enabled: open,
      // These are live rows, not an immutable version; reopening must reread them.
      staleTime: 0,
      refetchOnMount: "always",
    })),
  });
  const failed = reads.some((r) => r.isError);
  const loading = !failed && reads.some((r) => r.isFetching || !r.data);
  const first = reads.find((r) => r.data)?.data ?? null;
  const columns = useMemo(() => first?.columns ?? [], [first]);
  const userCols = useMemo(() => userColumnsOf(columns), [columns]);
  const datasetName = first?.datasetName ?? "dataset";
  const versionNumber = version?.number ?? first?.version?.number ?? null;
  // The reads' identity changes every render; their data only when re-read.
  const chosenKey = chosen.map((p) => `${p.key}:${choice[p.key]}`).join("|");
  const dataKey = reads.map((r) => r.dataUpdatedAt).join("|");
  const partRows = useMemo(
    () => reads.map((r) => r.data?.rows ?? []),
    [dataKey, chosenKey], // eslint-disable-line react-hooks/exhaustive-deps
  );
  const autoRoles = useMemo(
    () => defaultMessageRoles(columns, partRows.flat()),
    [columns, partRows],
  );
  const msgRoles = roles ?? autoRoles;
  const blocker = columns.length ? formatBlocker("openai_ft", msgRoles) : null;
  const plan = useMemo(() => {
    if (loading || failed || blocker || !choice.train) return null;
    return planTrainingSet({
      parts: chosen.map((p, i) => ({
        key: p.key,
        splitId: choice[p.key],
        splitName: nameOf[choice[p.key]] ?? choice[p.key],
        rows: partRows[i] ?? [],
      })),
      roles: msgRoles,
      keepOverlapInFirst: keepOverlap,
      leaveOutLeaked: leaveOut,
    });
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [
    loading,
    failed,
    blocker,
    chosenKey,
    partRows,
    msgRoles,
    keepOverlap,
    leaveOut,
    nameOf,
  ]);
  const testName = choice.test ? nameOf[choice.test] ?? null : null;

  const download = () => {
    if (!plan) return;
    const files = trainingFiles({
      plan,
      roles: msgRoles,
      system,
      datasetName,
      versionNumber,
    });
    const out = files.map((f) => ({
      ...f,
      size: saveFile(f),
    }));
    setSaved(out);
    notify(
      `Saved ${fmtCount(out.length, "file")} · ${fmtCount(plan.total, "row")}`,
      { variant: "success", key: "training-set" },
    );
  };
  // Leaving the page closes Manage splits too (its store outlives the page).
  const closeAll = () => {
    useManageSplitsStore.getState().setOpen(false);
    onClose?.();
  };
  // v2.1 g8 (entry:run-on-rows:training-split): the experiment runs on the
  // export's version (its test split), not on the latest one.
  const openExperiment = () => {
    closeAll();
    const door = trainingSetDoor({ datasetId, testName, version });
    useRunExperimentStore
      .getState()
      .initiateCreateMode({ prefill: door.prefill });
    navigate(door.href);
  };
  const splitOptions = (key) =>
    (splits ?? []).map((s) => ({
      id: s.id,
      label: s.name,
      disabled: TRAINING_PARTS.some(
        (p) => p.key !== key && choice[p.key] === s.id,
      ),
    }));

  let body = null;
  if (!choice.train)
    body = (
      <Typography typography="s2" color="text.secondary">
        Pick the split to train on.
      </Typography>
    );
  else if (failed)
    body = (
      <Stack direction="row" alignItems="center" gap={1}>
        <Typography typography="s2" color="error.main">
          Couldn&apos;t read the rows.
        </Typography>
        <Button size="small" onClick={() => reads.forEach((r) => r.refetch())}>
          Try again
        </Button>
      </Stack>
    );
  else if (loading)
    body = (
      <Stack gap={0.5}>
        <Typography typography="s2" color="text.secondary">
          {`Reading the rows of ${chosen.map((p) => nameOf[choice[p.key]]).join(", ")}…`}
        </Typography>
        <LinearProgress />
      </Stack>
    );
  else if (plan)
    body = (
      <PlanSummary
        plan={plan}
        datasetName={datasetName}
        versionNumber={versionNumber}
        onToggleOverlap={() => setKeepOverlap((v) => !v)}
        onToggleLeak={(k) => setLeaveOut((s) => ({ ...s, [k]: !s[k] }))}
      />
    );

  return (
    <Dialog
      open={open}
      onClose={onClose}
      fullWidth
      maxWidth="md"
      data-testid="training-set-dialog"
    >
      <DialogTitle sx={{ typography: "m3", pb: 0.5 }}>
        Export as training set
      </DialogTitle>
      <DialogContent
        dividers
        sx={{ display: "flex", flexDirection: "column", gap: 2 }}
      >
        {saved ? (
          <SavedFiles
            saved={saved}
            testName={testName}
            note={testName && plan ? splitNotFileLine(plan) : null}
            onLeave={closeAll}
          />
        ) : (
          <>
            <Typography typography="s2" color="text.secondary">
              One file per split for fine-tuning, from current saved rows. Each
              file is checked for rows that are also in another file and for
              inputs already in train.
            </Typography>
            <Stack direction="row" gap={1.5}>
              {TRAINING_PARTS.map((p) => (
                <Pick
                  key={p.key}
                  label={p.label}
                  value={choice[p.key]}
                  options={splitOptions(p.key)}
                  none={p.key !== "train"}
                  onChange={(id) => setChoice(p.key, id)}
                  testId={`training-set-${p.key}`}
                />
              ))}
            </Stack>
            <Stack direction="row" gap={1.5}>
              <Typography typography="s2">Current saved rows</Typography>
              <Box sx={{ flex: 2, minWidth: 0 }}>
                <Typography
                  sx={{ typography: "s2", color: "text.secondary", mb: 0.5 }}
                >
                  Format
                </Typography>
                <Typography
                  typography="s2"
                  sx={{ py: 0.75 }}
                  data-testid="training-set-format"
                >
                  OpenAI fine-tune JSONL (chat messages)
                </Typography>
              </Box>
            </Stack>
            <MessagesSection
              columns={userCols}
              roles={msgRoles}
              onRoles={setRoles}
              system={system}
              onSystem={setSystem}
              blocker={blocker}
            />
            {body}
          </>
        )}
      </DialogContent>
      <DialogActions sx={{ px: 3, py: 1.5, gap: 1 }}>
        <Button color="inherit" onClick={onClose}>
          {saved ? "Close" : "Cancel"}
        </Button>
        {saved &&
          testName &&
          isSurfaceShown("exp.new.scope") &&
          /* hook(SPLIT-SCOPE): require independent split execution. */
          splitScopeShown() && (
            <Button
              variant="contained"
              onClick={openExperiment}
              data-testid="training-set-open-experiment"
            >
              {`Open New experiment on ${testName}`}
            </Button>
          )}
        {!saved && (
          <Button
            variant="contained"
            disabled={!plan || !(plan.parts[0]?.written > 0)}
            onClick={download}
            data-testid="training-set-download"
          >
            Download
          </Button>
        )}
      </DialogActions>
    </Dialog>
  );
}

TrainingSetDialog.propTypes = {
  open: PropTypes.bool,
  onClose: PropTypes.func,
  datasetId: PropTypes.string.isRequired,
  splits: PropTypes.array,
};

/** Manage splits' footer door: shown when the dataset has a split. */
export function TrainingSetButton({ datasetId, splits }) {
  const [open, setOpen] = useState(false);
  if (!splits?.length) return null;
  return (
    <>
      <Button
        size="small"
        variant="outlined"
        onClick={() => setOpen(true)}
        // One line in Manage splits' 720px footer, and narrow enough that
        // Create split keeps its one line too (no icon, tighter padding).
        sx={{ px: 1, whiteSpace: "nowrap", flexShrink: 0 }}
        data-testid="training-set-open"
      >
        Export as training set…
      </Button>
      {open && (
        <TrainingSetDialog
          open
          datasetId={datasetId}
          splits={splits}
          onClose={() => setOpen(false)}
        />
      )}
    </>
  );
}

TrainingSetButton.propTypes = {
  datasetId: PropTypes.string.isRequired,
  splits: PropTypes.array,
};

export default TrainingSetDialog;
