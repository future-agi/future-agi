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
import { OUTLINED_BTN_SX, ROW_BOX_SX, T12 } from "../../drawerStyles";
import { Field, HintLine, MONO } from "../formBits";
import FoundAgentsList from "../shared/FoundAgentsList";
import TextLink from "../shared/TextLink";
import {
  SCAN_MS,
  SCAN_TIMEOUT_MS,
  sampleRepoOf,
  scanOutcomeOf,
} from "./sampleGithub";

/**
 * One sample repository, read for agents when it's picked and again when the
 * branch changes. It says while it reads, offers Retry when a read fails or
 * times out, and asks for the entry file when nothing is found. Keyed on the
 * repository by its parent, so a new one starts its tries over.
 *
 * `onStatus` hears "scanning" | "error" | "timeout" | "found" | "none".
 */
export default function RepoAgentScan({
  repoName,
  branch,
  agentId,
  onPick,
  entryFile,
  onEntryFile,
  onStatus,
}) {
  const [attempt, setAttempt] = useState(0);
  const [status, setStatus] = useState("scanning");
  const [naming, setNaming] = useState(false);
  const repo = sampleRepoOf(repoName);
  const agents = repo?.agents || [];

  useEffect(() => {
    setStatus("scanning");
    const outcome = scanOutcomeOf(repoName, attempt);
    const found = (sampleRepoOf(repoName)?.agents || []).length > 0;
    const t = setTimeout(
      () => setStatus(outcome === "ok" ? (found ? "found" : "none") : outcome),
      outcome === "timeout" ? SCAN_TIMEOUT_MS : SCAN_MS,
    );
    return () => clearTimeout(t);
  }, [repoName, branch, attempt]);
  useEffect(() => {
    onStatus(status);
  }, [status, onStatus]);

  if (!repo) return null;
  const at = (
    <Box
      component="span"
      sx={{ fontFamily: MONO }}
    >{`${repo.name} @ ${branch}`}</Box>
  );
  const entryField = (
    <Field
      label="Entry file"
      required
      mono
      value={entryFile}
      onChange={onEntryFile}
      placeholder={
        /typescript/i.test(repo.language)
          ? "src/agent/index.ts"
          : "app/chat.py:handle_message"
      }
      helper="A path from the repository root. Add :function when it isn't the file's main."
    />
  );

  if (status === "scanning") {
    return (
      <Stack
        direction="row"
        alignItems="center"
        spacing={1}
        sx={{ px: 1.5, py: 1.25, borderRadius: "6px", bgcolor: "action.hover" }}
      >
        <CircularProgress
          size={13}
          thickness={5}
          sx={{ color: "text.secondary", flexShrink: 0 }}
        />
        <Typography sx={{ ...T12, color: "text.secondary", flex: 1 }}>
          {"Reading "}
          {at}
          {" for agents…"}
        </Typography>
      </Stack>
    );
  }

  if (status === "error" || status === "timeout") {
    const timedOut = status === "timeout";
    return (
      <Stack
        direction="row"
        alignItems="flex-start"
        spacing={1.25}
        sx={{ ...ROW_BOX_SX, px: 1.5, py: 1.25 }}
      >
        <Iconify
          icon="solar:danger-triangle-linear"
          width={16}
          sx={{ color: "text.secondary", flexShrink: 0, mt: "2px" }}
        />
        <Box flex={1} minWidth={0}>
          <Typography sx={{ ...T12, fontWeight: 600 }}>
            {timedOut ? "Reading " : "Couldn't read "}
            {at}
            {timedOut ? " timed out" : ""}
          </Typography>
          <Typography sx={{ ...T12, color: "text.secondary", mt: 0.25 }}>
            {timedOut
              ? "Large repositories can take longer than one try. Retry, or name the entry file and preflight reads only that."
              : "GitHub didn't return the code. Access may have changed, or GitHub was briefly unavailable. Retry, or check the repository's access."}
          </Typography>
          {timedOut && <Box sx={{ mt: 1.25 }}>{entryField}</Box>}
        </Box>
        <Button
          variant="outlined"
          onClick={() => setAttempt((n) => n + 1)}
          startIcon={<Iconify icon="solar:restart-linear" width={14} />}
          sx={{ ...OUTLINED_BTN_SX, flexShrink: 0 }}
        >
          Retry
        </Button>
      </Stack>
    );
  }

  if (status === "none") {
    return (
      <Box sx={{ ...ROW_BOX_SX, px: 1.5, py: 1.25 }}>
        <Typography sx={{ ...T12, fontWeight: 600 }}>
          {"No agent found in "}
          {at}
        </Typography>
        <Typography
          component="div"
          sx={{ ...T12, color: "text.secondary", mt: 0.25, mb: 1.25 }}
        >
          <HintLine
            text="Name the file the agent starts in, and preflight reads it from there."
            hint="We look for a framework entry point or OpenAI, Anthropic or Gemini calls."
          />
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
  repoName: PropTypes.string.isRequired,
  branch: PropTypes.string.isRequired,
  agentId: PropTypes.string,
  onPick: PropTypes.func.isRequired,
  entryFile: PropTypes.string,
  onEntryFile: PropTypes.func.isRequired,
  onStatus: PropTypes.func.isRequired,
};
