import PropTypes from "prop-types";
import { useEffect, useState } from "react";
import { Box, Button, Stack, Typography } from "@mui/material";
import Iconify from "src/components/iconify";
import { OUTLINED_BTN_SX, ROW_BOX_SX, T12, T13 } from "../../drawerStyles";
import { Field, HintLine, MONO } from "../formBits";
import BusyRow from "../shared/BusyRow";
import FoundAgentsList from "../shared/FoundAgentsList";
import TextLink from "../shared/TextLink";
import { SCAN_MS, SCAN_TIMEOUT_MS, scanOutcomeOf } from "./sampleData";

/**
 * One repository, read for agents when it's picked, and again when the
 * branch changes. While it reads it says so; a failed or timed-out read says
 * which and offers Retry; when nothing is found, the entry file is asked for
 * right there. UI only: the read is simulated and the agents are samples.
 *
 * `onStatus(status)`: "scanning" | "error" | "timeout" | "found" | "none".
 */
export default function RepoScan({
  repo,
  branch,
  hostLabel,
  agentId,
  onPick,
  entryFile,
  onEntryFile,
  onStatus,
}) {
  const [attempt, setAttempt] = useState(0);
  const [status, setStatus] = useState("scanning");
  // Found agents, but not the one meant: its entry file, named by hand.
  const [naming, setNaming] = useState(false);
  const agents = repo.agents || [];

  useEffect(() => {
    setAttempt(0);
  }, [repo.name]);
  useEffect(() => {
    setStatus("scanning");
    const outcome = scanOutcomeOf(repo.name, attempt);
    const t = setTimeout(
      () =>
        setStatus(
          outcome === "ok" ? (agents.length ? "found" : "none") : outcome,
        ),
      outcome === "timeout" ? SCAN_TIMEOUT_MS : SCAN_MS,
    );
    return () => clearTimeout(t);
  }, [repo.name, branch, attempt]); // eslint-disable-line react-hooks/exhaustive-deps
  useEffect(() => {
    onStatus?.(status);
  }, [status]); // eslint-disable-line react-hooks/exhaustive-deps

  const at = (
    <Box component="span" sx={{ fontFamily: MONO }}>
      {`${repo.name} @ ${branch || "main"}`}
    </Box>
  );
  const entryField = (
    <Field
      label="Entry file"
      required
      mono
      value={entryFile}
      onChange={onEntryFile}
      placeholder={
        /typescript/i.test(repo.language || "")
          ? "src/agent/index.ts"
          : "app/chat.py:handle_message"
      }
      helper="A path from the repository root; add :function when it isn't the file's main."
    />
  );

  if (status === "scanning") {
    return (
      <BusyRow>
        {"Reading "}
        {at}
        {" for agents…"}
      </BusyRow>
    );
  }

  if (status === "error" || status === "timeout") {
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
          <Typography sx={{ ...T13, fontWeight: 600 }}>
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
          <Typography sx={{ ...T12, color: "text.secondary", mt: 0.25 }}>
            {status === "timeout"
              ? "Large repositories can take longer than one try. Retry, or name the entry file and Connect reads only that."
              : `${hostLabel} didn't return the code. Access may have changed, or the host was briefly unavailable. Retry, or check the repository's access.`}
          </Typography>
          {status === "timeout" && <Box sx={{ mt: 1.25 }}>{entryField}</Box>}
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
        <Typography sx={{ ...T13, fontWeight: 600 }}>
          {"No agent found in "}
          {at}
        </Typography>
        <Typography
          component="div"
          sx={{ ...T12, color: "text.secondary", mt: 0.25, mb: 1.25 }}
        >
          <HintLine
            text="Name the file the agent starts in. Connect reads it from there."
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
        sample
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
RepoScan.propTypes = {
  repo: PropTypes.shape({
    name: PropTypes.string,
    language: PropTypes.string,
    agents: PropTypes.array,
  }).isRequired,
  branch: PropTypes.string,
  hostLabel: PropTypes.string,
  agentId: PropTypes.string,
  onPick: PropTypes.func.isRequired,
  entryFile: PropTypes.string,
  onEntryFile: PropTypes.func.isRequired,
  onStatus: PropTypes.func,
};
