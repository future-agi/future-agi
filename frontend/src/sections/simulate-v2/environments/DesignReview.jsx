import PropTypes from "prop-types";
import { useState } from "react";
import {
  Box,
  Button,
  ButtonBase,
  Stack,
  Tooltip,
  Typography,
} from "@mui/material";
import Iconify from "src/components/iconify";
import AddSourceMenu from "../workspace/data/AddSourceMenu";

/*
  Builder → Review design (builder.md). The proposal — world, data, tools,
  rules, people and scenario coverage — waits for approval; only approval
  starts the cloud build. Auto mode carries out approved steps without
  stopping, but never skips this approval. A question left past the waiting
  limit is Parked; Continue reopens the same proposal, not a new one.
*/
export default function DesignReview({
  env,
  draftCount,
  waiting,
  parked,
  since,
  autoMode,
  onApprove,
  onContinue,
  framed = false,
  sources = null,
  onAddFiles,
  onAddOrg,
  question = null,
}) {
  const [anchor, setAnchor] = useState(null);
  /* Built from scratch there's no agent: the world comes from the words. */
  const scratch = env?.builtFrom?.kind === "scratch";
  /* Files before the build (plan: "Attach a policy and optional CSV"):
     they shape this design and join the first version. */
  const canAdd = !!(onAddFiles && onAddOrg);
  const files = sources?.sources || [];
  const sourcesValue = (
    <>
      {files.length
        ? `${files.map((f) => f.name).join(", ")} · adds ${
            [
              sources.rules &&
                `${sources.rules} rule${sources.rules === 1 ? "" : "s"}`,
              sources.records &&
                `${sources.records.toLocaleString()} seeded records`,
            ]
              .filter(Boolean)
              .join(" and ") || "reference material"
          }${sources.needsInput ? ` · ${sources.needsInput} to answer after the build` : ""}`
        : scratch
          ? "Your description"
          : "The agent alone"}
      {canAdd && (
        <ButtonBase
          onClick={(e) => setAnchor(e.currentTarget)}
          sx={{
            typography: "s2",
            fontWeight: 600,
            textDecoration: "underline",
            ml: 1,
            verticalAlign: "baseline",
          }}
        >
          {files.length ? "Add another" : "Add files"}
        </ButtonBase>
      )}
    </>
  );
  const rows = [
    ...(canAdd || files.length ? [["Sources", sourcesValue]] : []),
    [
      "World",
      `${(env?.seed?.tables || []).length} tables · ${(env?.seed?.tables || []).reduce((n, t) => n + (t.rows || 0), 0).toLocaleString()} rows`,
    ],
    ["Tools", `${(env?.tools || []).length}`],
    [
      "Rules",
      `${(env?.rules || []).length}${sources?.rules ? ` + ${sources.rules} from your files` : ""}`,
    ],
    [
      "People",
      `${(env?.actors || []).length ? `Caller + ${(env?.actors || []).length}` : "Caller only"}`,
    ],
    ["Scenarios", `${draftCount} to write`],
  ];
  const status = parked ? "Parked" : waiting ? "Awaiting approval" : "Approved";
  /* Framed: the full-width card above every tab, so the one thing to do
     shows wherever the person is — chat folded or not, any tab. */
  if (framed)
    return (
      <Box sx={{ px: 2, pt: 1.5, flexShrink: 0 }}>
        <Box
          sx={{
            border: "1px solid",
            borderColor: "divider",
            borderRadius: "8px",
            bgcolor: "background.paper",
            overflow: "hidden",
            "& > div": { borderBottom: 0 },
          }}
        >
          <DesignReview
            env={env}
            draftCount={draftCount}
            waiting={waiting}
            parked={parked}
            since={since}
            autoMode={autoMode}
            onApprove={onApprove}
            onContinue={onContinue}
            sources={sources}
            onAddFiles={onAddFiles}
            onAddOrg={onAddOrg}
            question={question}
          />
        </Box>
      </Box>
    );
  /* Compact: title, status and the one action on a row; the proposal as
     one wrapping line of facts; one line on what happens next. */
  const note = parked
    ? `Waiting on you since ${new Date(since).toLocaleTimeString(undefined, { hour: "numeric", minute: "2-digit" })}. Continue picks up this same proposal.`
    : waiting && question
      ? `One question first, in the builder chat: ${question}`
      : waiting
        ? "Nothing is built until you approve. To change something first, ask the builder."
        : "Approved — the build is running from this design.";
  return (
    <Box
      sx={{
        flexShrink: 0,
        borderBottom: "1px solid",
        borderColor: "divider",
        px: 2,
        py: 1,
      }}
    >
      <Stack direction="row" alignItems="center" spacing={1}>
        <Typography sx={{ typography: "s2", fontWeight: 600 }}>
          Review design
        </Typography>
        <Typography
          sx={{
            typography: "s3",
            color: parked
              ? "status.warning.fg"
              : waiting
                ? "text.secondary"
                : "status.success.fg",
          }}
        >
          {status}
        </Typography>
        <Box flex={1} />
        {canAdd && (
          <AddSourceMenu
            anchorEl={anchor}
            onClose={() => setAnchor(null)}
            onAddFiles={(f) => onAddFiles(f)}
            onAddOrg={(o) => {
              setAnchor(null);
              onAddOrg(o);
            }}
            added={files.filter((f) => f.kb).map((f) => f.name)}
          />
        )}
        {parked ? (
          <Button
            size="small"
            variant="outlined"
            onClick={onContinue}
            sx={{ color: "text.primary", borderColor: "divider" }}
          >
            Continue
          </Button>
        ) : (
          waiting && (
            <Tooltip
              arrow
              title={
                question
                  ? "Answer the builder's question first — it won't guess a policy."
                  : autoMode
                    ? "Auto mode runs approved steps without stopping. It never approves a design for you."
                    : ""
              }
            >
              <span>
                <Button
                  size="small"
                  variant="contained"
                  color="primary"
                  disabled={!!question}
                  onClick={onApprove}
                  startIcon={
                    <Iconify icon="solar:check-circle-linear" width={14} />
                  }
                >
                  Approve and build
                </Button>
              </span>
            </Tooltip>
          )
        )}
      </Stack>

      {waiting && (
        <Stack
          direction="row"
          flexWrap="wrap"
          columnGap={2.5}
          rowGap={0.25}
          sx={{ mt: 0.75 }}
        >
          {rows.map(([k, v]) => (
            <Typography key={k} component="div" sx={{ typography: "s2" }}>
              <Box component="span" sx={{ color: "text.secondary", mr: 0.75 }}>
                {k}
              </Box>
              <Box component="span" sx={{ color: "text.primary" }}>
                {v}
              </Box>
            </Typography>
          ))}
        </Stack>
      )}

      <Typography sx={{ typography: "s3", color: "text.tertiary", mt: 0.5 }}>
        {note}
      </Typography>
    </Box>
  );
}
DesignReview.propTypes = {
  env: PropTypes.object,
  draftCount: PropTypes.number,
  waiting: PropTypes.bool,
  parked: PropTypes.bool,
  since: PropTypes.number,
  autoMode: PropTypes.bool,
  onApprove: PropTypes.func,
  onContinue: PropTypes.func,
  framed: PropTypes.bool,
  sources: PropTypes.object,
  onAddFiles: PropTypes.func,
  onAddOrg: PropTypes.func,
  /* The builder's open question — Approve waits for its answer. */
  question: PropTypes.string,
};
