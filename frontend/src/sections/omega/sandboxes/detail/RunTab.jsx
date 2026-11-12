import React, { useId, useState } from "react";
import PropTypes from "prop-types";
import Alert from "@mui/material/Alert";
import Box from "@mui/material/Box";
import Button from "@mui/material/Button";
import TextField from "@mui/material/TextField";
import Typography from "@mui/material/Typography";
import LoadingButton from "@mui/lab/LoadingButton";

import { useSandboxList } from "../api/queries";
import { useSandboxesApi } from "../api/SandboxesApiContext";
import SandboxIcon from "../components/SandboxIcon";
import StatePanel from "../components/StatePanel";
import { isExecLive } from "../lib/execOutput";
import {
  hasCustomOptions,
  initialRunOptions,
  normalizeCwd,
  optionsSummary,
  runPayload,
  folderLookup,
  folderMissing,
  validateRunOptions,
  workdirLabel,
} from "../lib/runOptions";
import CommandLimit from "./CommandLimit";
import DeletedPanel from "./DeletedPanel";
import RunOptions from "./RunOptions";
import RunOutput from "./RunOutput";
import useExecStream from "./useExecStream";

const monoFont =
  '"JetBrains Mono", "SFMono-Regular", Menlo, Consolas, monospace';

/** The Options toggle and the one-line summary of what the next command gets. */
function OptionsBar({ open, onToggle, summary, invalid, panelId }) {
  return (
    <Box
      sx={{
        display: "flex",
        alignItems: "center",
        justifyContent: "space-between",
        gap: 1,
        flexWrap: "wrap",
      }}
    >
      <Typography
        typography="s2"
        color={invalid ? "error.main" : "text.secondary"}
        data-testid="run-options-summary"
        sx={{ minWidth: 0, overflowWrap: "anywhere" }}
      >
        {invalid ? "Fix the highlighted option to run a command." : summary}
      </Typography>
      <Button
        size="small"
        color="inherit"
        onClick={onToggle}
        aria-expanded={open}
        aria-controls={open ? panelId : undefined}
        endIcon={
          <SandboxIcon
            name="chevronRight"
            size={14}
            sx={{ transform: open ? "rotate(-90deg)" : "rotate(90deg)" }}
          />
        }
        sx={{ typography: "s2", flexShrink: 0 }}
        data-testid="run-options-toggle"
      >
        Options
      </Button>
    </Box>
  );
}

OptionsBar.propTypes = {
  open: PropTypes.bool.isRequired,
  onToggle: PropTypes.func.isRequired,
  summary: PropTypes.string.isRequired,
  invalid: PropTypes.bool,
  panelId: PropTypes.string.isRequired,
};

export default function RunTab({ sandbox, onResume, resuming }) {
  const caps = useSandboxList().data?.caps;
  const [command, setCommand] = useState("");
  // Only what the user changed is kept; everything else follows EE's stated
  // defaults, so a time limit untouched before the caps load still becomes
  // EE's default once they do (until then nothing is sent for it).
  const [changed, setChanged] = useState({});
  const current = { ...initialRunOptions(caps), ...changed };
  const [showOptions, setShowOptions] = useState(false);
  const [ranWith, setRanWith] = useState(null);
  const panelId = useId();
  const exec = useExecStream(sandbox.id);
  const live = isExecLive(exec.state);
  const api = useSandboxesApi();
  // The folder the last Run found missing. It is shown on the folder field but
  // never disables Run: the next Run looks again (the user may have made it).
  const [missing, setMissing] = useState(null);
  const [checking, setChecking] = useState(false);
  const optionErrors = validateRunOptions(current, caps);
  const cwd = normalizeCwd(current.cwd);
  const invalid = Object.keys(optionErrors).length > 0;
  const shownErrors =
    !optionErrors.cwd && missing !== null && missing === cwd
      ? {
          ...optionErrors,
          cwd: `No folder ${workdirLabel(cwd)} in this sandbox. Make it first, for example with mkdir -p ${cwd}`,
        }
      : optionErrors;

  if (sandbox.readOnly) {
    return (
      <StatePanel
        compact
        icon="terminal"
        title="Commands run inside the agent run"
        description="This sandbox belongs to an agent run, so it can't take commands from here."
      />
    );
  }
  if (sandbox.state === "destroyed") {
    return <DeletedPanel sandbox={sandbox} what="run" />;
  }
  if (sandbox.state !== "active" && !live) {
    return (
      <StatePanel
        compact
        icon="pause"
        title={
          sandbox.state === "hibernated"
            ? "This sandbox is hibernated"
            : "This sandbox isn't running"
        }
        description={
          sandbox.state === "hibernated"
            ? "Commands run in its container, which starts again when you resume it. Its files are kept."
            : "Commands run in its container, which is not running."
        }
        action={
          sandbox.state === "hibernated" && onResume ? (
            <LoadingButton
              size="small"
              variant="contained"
              loading={resuming}
              onClick={onResume}
            >
              Resume
            </LoadingButton>
          ) : null
        }
      />
    );
  }

  const change = (patch) => setChanged((before) => ({ ...before, ...patch }));
  const submit = async (event) => {
    event?.preventDefault();
    const text = command.trim();
    if (!text || live || checking) return;
    if (invalid) {
      setShowOptions(true);
      return;
    }
    // A folder that does not exist would fail inside the container runtime
    // with its own message ("OCI runtime exec failed ... chdir"): look in its
    // parent first. Only a listing that lacks the folder stops the run; a
    // failed lookup leaves the command to EE.
    const lookup = folderLookup(cwd);
    if (lookup) {
      setChecking(true);
      try {
        const listing = await api.listFiles(sandbox.id, lookup.parent);
        if (folderMissing(cwd, listing)) {
          setMissing(cwd);
          setShowOptions(true);
          return;
        }
        setMissing(null);
      } catch {
        // EE answers the command itself.
      } finally {
        setChecking(false);
      }
    }
    setRanWith({ cwd, timeoutSeconds: current.timeoutSeconds });
    exec.run(text, runPayload(current, caps));
  };

  return (
    <Box sx={{ display: "flex", flexDirection: "column", gap: 2, minWidth: 0 }}>
      <Box
        component="form"
        noValidate
        onSubmit={submit}
        sx={{ display: "flex", flexDirection: "column", gap: 1, minWidth: 0 }}
      >
        <Box
          sx={{
            display: "flex",
            gap: 1,
            alignItems: "flex-start",
            flexDirection: { xs: "column", sm: "row" },
          }}
        >
          <TextField
            fullWidth
            multiline
            maxRows={6}
            size="small"
            placeholder="ls -la"
            value={command}
            onChange={(event) => setCommand(event.target.value)}
            onKeyDown={(event) => {
              if (event.key === "Enter" && !event.shiftKey) {
                event.preventDefault();
                submit();
              }
            }}
            helperText="Enter runs, Shift+Enter adds a line."
            InputProps={{
              sx: { "& textarea": { fontFamily: monoFont, fontSize: 13 } },
            }}
            inputProps={{
              "aria-label": "Command",
              "data-testid": "run-command",
            }}
          />
          {live ? (
            <LoadingButton
              variant="outlined"
              color="inherit"
              loading={exec.cancelling}
              disabled={!exec.state.execId}
              onClick={exec.cancel}
              startIcon={<SandboxIcon name="stop" size={14} />}
              sx={{ flexShrink: 0, minWidth: 104 }}
              data-testid="run-cancel"
            >
              Cancel
            </LoadingButton>
          ) : (
            <LoadingButton
              type="submit"
              variant="contained"
              color="primary"
              loading={checking}
              disabled={!command.trim() || invalid}
              startIcon={<SandboxIcon name="play" size={14} />}
              sx={{ flexShrink: 0, minWidth: 104 }}
              data-testid="run-submit"
            >
              Run
            </LoadingButton>
          )}
        </Box>
        <OptionsBar
          open={showOptions}
          onToggle={() => setShowOptions((open) => !open)}
          summary={
            hasCustomOptions(current, caps)
              ? optionsSummary(current, caps)
              : `${optionsSummary(current, caps)} (the defaults)`
          }
          invalid={invalid}
          panelId={panelId}
        />
        <CommandLimit caps={caps} testId="run-command-limit" />
        {showOptions && (
          <RunOptions
            id={panelId}
            caps={caps}
            options={current}
            errors={shownErrors}
            disabled={live}
            onChange={change}
          />
        )}
      </Box>
      {exec.cancelError && <Alert severity="error">{exec.cancelError}</Alert>}
      {exec.state.status === "idle" ? (
        <Typography typography="s2" color="text.secondary">
          Output streams here as the command runs. Activity records each command
          with its exit code and duration.
        </Typography>
      ) : (
        <RunOutput state={exec.state} ranWith={ranWith} caps={caps} />
      )}
    </Box>
  );
}

RunTab.propTypes = {
  sandbox: PropTypes.object.isRequired,
  onResume: PropTypes.func,
  resuming: PropTypes.bool,
};
