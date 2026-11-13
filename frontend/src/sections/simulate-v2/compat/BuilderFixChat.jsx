import { useEffect, useRef, useState } from "react";
import PropTypes from "prop-types";
import {
  Box,
  Button,
  CircularProgress,
  IconButton,
  Stack,
  TextField,
  Typography,
} from "@mui/material";
import Iconify from "src/components/iconify";

/*
  The builder chat fixing Ready to run rows in place (13 Nov, a teammate's
  run plan the manager liked): the same builder that builds the environment
  takes each ticked fix, says what it reads and changes, asks when there's a
  real choice, and ends by re-checking the pair once for all of them.
*/

const toolOf = (row) =>
  /^Point (\S+) at/.exec(row?.fixLabel || "")?.[1] || "its tools";

/* What the builder does for one fix, step by step. */
export const builderStepsFor = (kind, row) => {
  if (kind === "tools") {
    const tool = toolOf(row);
    return [
      { t: "say", text: `Reading ${tool} in the agent's tools.` },
      { t: "ok", text: `Found ${tool}`, sub: "it still reaches production" },
      {
        t: "ask",
        text: `Should ${tool} use the seeded records or a copy of the agent's store?`,
        options: ["Seeded records", "A copy of the agent's store"],
      },
      { t: "ok", text: `Pointed ${tool} at the test world` },
      { t: "ok", text: "Saved on the agent's connection" },
    ];
  }
  if (kind === "release")
    return [
      { t: "say", text: "Finding the release checks that haven't run." },
      { t: "ok", text: "Started the environment's release checks" },
    ];
  return [
    { t: "say", text: "Reading what this agent needs." },
    { t: "ok", text: "Rebuilding the environment for this agent" },
    { t: "ok", text: "Saved as a new environment version" },
  ];
};

/* What the row sends to the builder, in the words of its fix. */
export const builderPromptFor = (kind, row) =>
  kind === "tools"
    ? `${row?.fixLabel || "Point the tools at the test world"}, so runs never reach production.`
    : kind === "release"
      ? "Run the environment's release checks."
      : "Rebuild the environment for this agent.";

export default function BuilderFixChat({ jobs, onDone, onClose }) {
  /* jobs: [{ id, label, kind, row }] in the order they run. */
  const [jobAt, setJobAt] = useState(0);
  const [stepAt, setStepAt] = useState(0);
  const [log, setLog] = useState([]);
  const [asking, setAsking] = useState(null);
  const [reply, setReply] = useState("");
  const [finished, setFinished] = useState(false);
  const answers = useRef({});

  const job = jobs.at(jobAt);
  const steps = job ? builderStepsFor(job.kind, job.row) : [];

  useEffect(() => {
    if (finished || asking || !job) return undefined;
    if (stepAt >= steps.length) {
      /* This fix is done; the next one, or one re-check for all of them. */
      if (jobAt + 1 < jobs.length) {
        const t = setTimeout(() => {
          setJobAt((i) => i + 1);
          setStepAt(0);
        }, 500);
        return () => clearTimeout(t);
      }
      setLog((l) => [
        ...l,
        { t: "spin", text: "Re-checking the pair", key: "recheck" },
      ]);
      const t = setTimeout(() => {
        setFinished(true);
        onDone(answers.current);
      }, 900);
      return () => clearTimeout(t);
    }
    const s = steps.at(stepAt);
    const t = setTimeout(
      () => {
        setLog((l) => [
          ...l,
          { ...s, key: `${job.id}-${stepAt}`, job: job.id },
        ]);
        if (s.t === "ask") setAsking(s);
        else setStepAt((i) => i + 1);
      },
      stepAt === 0 ? 300 : 700,
    );
    return () => clearTimeout(t);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [jobAt, stepAt, asking, finished]);

  const answer = (text) => {
    const clean = text.trim();
    if (!clean || !asking) return;
    answers.current[job.id] = clean;
    setLog((l) => [
      ...l,
      { t: "you", text: clean, key: `${job.id}-a${stepAt}` },
    ]);
    setReply("");
    setAsking(null);
    setStepAt((i) => i + 1);
  };

  return (
    <Box
      sx={{ px: 1.5, py: 1.25, borderTop: "1px solid", borderColor: "divider" }}
    >
      <Stack direction="row" alignItems="center" spacing={1} sx={{ mb: 1 }}>
        <Iconify
          icon="solar:chat-round-dots-linear"
          width={16}
          sx={{ color: "text.secondary" }}
        />
        <Typography sx={{ typography: "s2", fontWeight: 600 }}>
          Builder chat
        </Typography>
        <Typography
          sx={{
            typography: "s3",
            color: "text.tertiary",
            flex: 1,
            minWidth: 0,
          }}
          noWrap
        >
          {finished
            ? `Fixed ${jobs.length}`
            : job
              ? `Fixing: ${job.label}`
              : ""}
        </Typography>
        {finished && (
          <IconButton
            size="small"
            onClick={onClose}
            aria-label="Close the builder chat"
          >
            <Iconify icon="solar:close-linear" width={14} />
          </IconButton>
        )}
      </Stack>
      <Stack spacing={0.75}>
        {jobs.map((j, i) =>
          i <= jobAt ? (
            <Box
              key={j.id}
              sx={{
                alignSelf: "flex-end",
                maxWidth: "85%",
                px: 1.25,
                py: 0.5,
                borderRadius: "8px",
                bgcolor: "background.neutral",
                typography: "s3",
              }}
            >
              {builderPromptFor(j.kind, j.row)}
            </Box>
          ) : null,
        )}
        {log.map((s) =>
          s.t === "you" ? (
            <Box
              key={s.key}
              sx={{
                alignSelf: "flex-end",
                px: 1.25,
                py: 0.5,
                borderRadius: "8px",
                bgcolor: "background.neutral",
                typography: "s3",
              }}
            >
              {s.text}
            </Box>
          ) : (
            <Stack
              key={s.key}
              direction="row"
              spacing={1}
              alignItems="flex-start"
            >
              {s.t === "ok" && (
                <Iconify
                  icon="solar:check-circle-linear"
                  width={14}
                  sx={{ color: "success.main", mt: "3px", flexShrink: 0 }}
                />
              )}
              {s.t === "spin" &&
                (finished ? (
                  <Iconify
                    icon="solar:check-circle-linear"
                    width={14}
                    sx={{ color: "success.main", mt: "3px", flexShrink: 0 }}
                  />
                ) : (
                  <CircularProgress
                    size={12}
                    sx={{ mt: "4px", flexShrink: 0 }}
                  />
                ))}
              <Typography
                sx={{
                  typography: "s3",
                  color: s.t === "say" ? "text.secondary" : "text.primary",
                }}
              >
                {s.text}
                {s.sub && (
                  <Box component="span" sx={{ color: "text.tertiary" }}>
                    {` · ${s.sub}`}
                  </Box>
                )}
              </Typography>
            </Stack>
          ),
        )}
        {asking && (
          <Stack direction="row" spacing={0.75} flexWrap="wrap" useFlexGap>
            {asking.options.map((o) => (
              <Button
                key={o}
                size="small"
                variant="outlined"
                onClick={() => answer(o)}
                sx={{ py: 0 }}
              >
                {o}
              </Button>
            ))}
          </Stack>
        )}
      </Stack>
      {!finished && (
        <TextField
          size="small"
          fullWidth
          value={reply}
          disabled={!asking}
          onChange={(e) => setReply(e.target.value)}
          onKeyDown={(e) => e.key === "Enter" && answer(reply)}
          placeholder={
            asking ? "Reply to the builder" : "The builder is working…"
          }
          inputProps={{ "aria-label": "Reply to the builder" }}
          sx={{ mt: 1, "& input": { typography: "s3", py: 0.75 } }}
        />
      )}
    </Box>
  );
}
BuilderFixChat.propTypes = {
  jobs: PropTypes.array.isRequired,
  onDone: PropTypes.func.isRequired,
  onClose: PropTypes.func.isRequired,
};
