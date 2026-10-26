import PropTypes from "prop-types";
import { useEffect, useState } from "react";
import {
  Box,
  Button,
  CircularProgress,
  Stack,
  Typography,
} from "@mui/material";
import Iconify from "src/components/iconify";
import { Field } from "../../environments/StartEnvironment";
import FoundAgentsList from "./FoundAgentsList";
import { HintLine } from "./replicationBits";
import { TextLink } from "./connectBits";
import { SCAN_MS, SCAN_TIMEOUT_MS, scanOutcomeOf } from "./repoScan";

const MONO = "ui-monospace, Menlo, monospace";

/**
 * One repository, read for agents when it's picked — and read again when
 * the branch changes. While it reads it says so; a failed or timed-out read
 * says which and offers Retry; and when nothing is found, the entry file is
 * asked for right there, so Connect can still go ahead.
 *
 * `onStatus(status)` tells the form whether the read is still going
 * ("scanning"), failed ("error" | "timeout"), or finished ("found" | "none").
 */
export default function RepoAgentScan({
  repo,
  branch,
  hostLabel,
  agentId,
  onPick,
  entryFile,
  onEntryFile,
  onStatus,
  connectedOf,
  onOpenConnected,
}) {
  const [attempt, setAttempt] = useState(0);
  const [status, setStatus] = useState("scanning");
  /* Found agents, but not the one meant: its entry file, named by hand. */
  const [naming, setNaming] = useState(false);
  const agents = repo?.agents || [];

  /* A different repository starts its tries over. */
  useEffect(() => {
    setAttempt(0);
  }, [repo?.name]);
  useEffect(() => {
    if (!repo) return undefined;
    setStatus("scanning");
    const outcome = scanOutcomeOf(repo.name, attempt);
    const t = setTimeout(
      () => {
        setStatus(
          outcome === "ok" ? (agents.length ? "found" : "none") : outcome,
        );
      },
      outcome === "timeout" ? SCAN_TIMEOUT_MS : SCAN_MS,
    );
    return () => clearTimeout(t);
  }, [repo?.name, branch, attempt]); // eslint-disable-line react-hooks/exhaustive-deps
  useEffect(() => {
    onStatus?.(status);
  }, [status]); // eslint-disable-line react-hooks/exhaustive-deps

  if (!repo) return null;
  const at = (
    <Box
      component="span"
      sx={{ fontFamily: MONO }}
    >{`${repo.name} @ ${branch || "main"}`}</Box>
  );
  const entryField = (
    <Field
      label="Entry file"
      required
      mono
      placeholder={
        /typescript/i.test(repo.language || "")
          ? "src/agent/index.ts"
          : "app/chat.py:handle_message"
      }
      value={entryFile}
      onChange={onEntryFile}
      helper="A path from the repository root; add :function when it isn't the file's main."
    />
  );

  if (status === "scanning") {
    return (
      <Stack
        direction="row"
        alignItems="center"
        spacing={1}
        sx={{
          px: 1.5,
          py: 1.25,
          borderRadius: 1,
          bgcolor: "background.neutral",
        }}
      >
        <CircularProgress
          size={13}
          thickness={5}
          sx={{ color: "text.subtitle", flexShrink: 0 }}
        />
        <Typography
          sx={{
            typography: "s2",
            color: "text.secondary",
            flex: 1,
            minWidth: 0,
          }}
        >
          {"Reading "}
          {at}
          {" for agents…"}
        </Typography>
      </Stack>
    );
  }

  if (status === "error" || status === "timeout") {
    return (
      <Stack
        direction="row"
        alignItems="flex-start"
        spacing={1.25}
        sx={{
          px: 1.5,
          py: 1.25,
          borderRadius: 1,
          border: "1px solid",
          borderColor: "divider",
        }}
      >
        <Iconify
          icon="solar:danger-triangle-linear"
          width={16}
          sx={{ color: "text.secondary", flexShrink: 0, mt: "2px" }}
        />
        <Box flex={1} minWidth={0}>
          <Typography sx={{ typography: "s2", fontWeight: 600 }}>
            {status === "timeout" ? (
              <>
                {"Reading "}
                {at}
                {" timed out"}
              </>
            ) : (
              <>
                {"Couldn't read "}
                {at}
              </>
            )}
          </Typography>
          <Typography
            sx={{ typography: "s3", color: "text.subtitle", mt: 0.25 }}
          >
            {status === "timeout"
              ? "Large repositories can take longer than one try. Retry, or name the entry file and Connect reads only that."
              : `${hostLabel || "The git host"} didn't return the code — access may have changed, or the host was briefly unavailable. Retry, or check the repository's access.`}
          </Typography>
          {status === "timeout" && <Box sx={{ mt: 1.25 }}>{entryField}</Box>}
        </Box>
        <Button
          size="small"
          variant="outlined"
          onClick={() => setAttempt((n) => n + 1)}
          startIcon={<Iconify icon="solar:restart-linear" width={14} />}
          sx={{ color: "text.primary", borderColor: "divider", flexShrink: 0 }}
        >
          Retry
        </Button>
      </Stack>
    );
  }

  if (status === "none") {
    return (
      <Box
        sx={{
          px: 1.5,
          py: 1.25,
          borderRadius: 1,
          border: "1px solid",
          borderColor: "divider",
        }}
      >
        <Typography sx={{ typography: "s2", fontWeight: 600 }}>
          {"No agent found in "}
          {at}
        </Typography>
        <Typography
          component="div"
          sx={{ typography: "s3", color: "text.subtitle", mt: 0.25, mb: 1.25 }}
        >
          <HintLine hint="We look for a framework entry point or OpenAI, Anthropic or Gemini calls.">
            Name the file the agent starts in — Connect reads it from there.
          </HintLine>
        </Typography>
        {entryField}
      </Box>
    );
  }

  return (
    <Box>
      <FoundAgentsList
        agents={agents}
        agentId={agentId}
        onPick={(a) => {
          setNaming(false);
          onPick(a);
        }}
        connectedOf={connectedOf}
        onOpenConnected={onOpenConnected}
      />
      {naming || (!agentId && entryFile) ? (
        <Box sx={{ mt: 1.25 }}>{entryField}</Box>
      ) : (
        <TextLink onClick={() => setNaming(true)} sx={{ mt: 0.75 }}>
          Not in the list? Name its entry file
        </TextLink>
      )}
    </Box>
  );
}

RepoAgentScan.propTypes = {
  /* { name, language, agents } — the repository picked. */
  repo: PropTypes.object,
  branch: PropTypes.string,
  hostLabel: PropTypes.string,
  agentId: PropTypes.string,
  onPick: PropTypes.func.isRequired,
  entryFile: PropTypes.string,
  onEntryFile: PropTypes.func.isRequired,
  onStatus: PropTypes.func,
  connectedOf: PropTypes.func,
  onOpenConnected: PropTypes.func,
};
