import { useState } from "react";
import { Box, Button, Stack, Typography } from "@mui/material";
import Iconify from "src/components/iconify";
import PropTypes from "prop-types";
import { observeProjectById, observeSlugOf } from "../../_mock/connectAgent";
import { ObserveProjectStep } from "./ConnectAboutStep";
import { useConnectObserve } from "./connectSubmit";
import { useConnectedAgents } from "../../store";
import { projectLinkOf } from "../../_mock/projectLinks";

/**
 * Where the agent's traces go — one line above every connect flow's button.
 *
 * No agent is saved without a tracing project: it's what ties the agent to
 * its real calls (Tracing, Error Feed) and to the environments that test it.
 * The default is a new project named after the agent, made when it's saved;
 * Change picks the project it already sends to. No traces are needed yet —
 * the project waits for its first one, and building an environment doesn't.
 *
 * Not shown when the connect was opened from a project or an issue: that
 * project is the answer, and the flow already says so.
 */
export default function ConnectProjectLine({
  agentName = "",
  codeProject,
  traced,
}) {
  const ctx = useConnectObserve();
  const [open, setOpen] = useState(false);
  const { agents, state } = useConnectedAgents();
  /* A hosted platform's drawer names its project in its own Enable
     observability row — one place, not two. */
  if (!ctx || ctx.prefillProject || ctx.kind === "hosted") return null;
  /* The code's project, unless another agent has it — then a new one, as the save does. */
  const codeOwner = codeProject
    ? (() => {
        const l = projectLinkOf(codeProject, state, agents);
        return l.status === "linked" ? l.agent?.name || "another agent" : null;
      })()
    : null;
  const { observe, setObserve, kind } = ctx;
  const existing = observe?.mode === "existing" ? observe.id : null;
  const named = observe?.mode === "create" ? observe.id : null;
  /* The default project's name, once the flow knows the agent's. */
  const slug = agentName.trim() ? observeSlugOf(agentName) : null;
  const newOne = slug ? (
    <>
      a new project, <b>{slug}</b>
    </>
  ) : (
    <>
      a <b>new project named after the agent</b>
    </>
  );
  const line = existing ? (
    <>
      Traces go to <b>{observeProjectById(existing)?.name || existing}</b>, the
      project it already sends to.
    </>
  ) : named ? (
    <>
      Traces go to a new project, <b>{named}</b>. It shows in Tracing with its
      first trace.
    </>
  ) : observe?.mode === "existing" ? (
    <>Pick the project its traces already land in.</>
  ) : traced === false ? (
    /* Its framework isn't instrumented by traceAI: no production traces yet. */
    <>
      traceAI doesn&apos;t instrument this framework yet, so its failures come
      from simulations. It still gets {newOne}, ready for traces later.
    </>
  ) : codeProject && codeOwner ? (
    <>
      Its code sends traces to <b>{codeProject}</b>, which {codeOwner} has — so
      they&apos;ll go to {newOne}. Change to take it instead.
    </>
  ) : codeProject ? (
    /* We read the code: it registers traceAI with this project. */
    <>
      Traces go to <b>{codeProject}</b> through traceAI — its code already sends
      them there, and Error Feed watches them.
    </>
  ) : codeProject === null ? (
    <>
      Its code doesn&apos;t send traces yet. Add traceAI and they go to {newOne}
      , where Error Feed watches them.
    </>
  ) : kind === "repo" || kind === "upload" || kind === "framework" ? (
    <>
      Traces come in through traceAI — to the project its code already sends to,
      if we find one, otherwise to {newOne}.
    </>
  ) : (
    <>
      Traces go to the project whose name matches it, if there is one —
      otherwise to {newOne}.
    </>
  );
  return (
    <Box
      data-testid="connect-project-line"
      sx={{
        mt: 1.75,
        border: "1px solid",
        borderColor: "divider",
        borderRadius: 1,
      }}
    >
      <Stack
        direction="row"
        alignItems="center"
        spacing={1.25}
        sx={{ px: 1.5, py: 1.1 }}
      >
        <Iconify
          icon="solar:graph-new-linear"
          width={16}
          sx={{ color: "text.secondary", flexShrink: 0 }}
        />
        <Box flex={1} minWidth={0}>
          <Typography
            sx={{
              typography: "s2",
              color: "text.secondary",
              "& b": { color: "text.primary", fontWeight: 600 },
            }}
          >
            <Box
              component="span"
              sx={{ fontWeight: 600, color: "text.primary" }}
            >
              Tracing project
            </Box>
            {" · "}
            {line}
          </Typography>
        </Box>
        <Button
          variant="outlined"
          size="small"
          onClick={() => setOpen((o) => !o)}
          sx={{ minWidth: 0, flexShrink: 0 }}
        >
          {open ? "Done" : "Change"}
        </Button>
      </Stack>
      {open && (
        <Box
          sx={{
            px: 1.5,
            pt: 1.25,
            pb: 1.5,
            borderTop: "1px solid",
            borderColor: "divider",
          }}
        >
          <ObserveProjectStep
            autoName
            agentName={agentName}
            value={observe}
            onChange={setObserve}
          />
        </Box>
      )}
    </Box>
  );
}

ConnectProjectLine.propTypes = {
  /* The agent's name, when the flow knows it — the default project's name. */
  agentName: PropTypes.string,
  /* false when traceAI doesn't instrument its framework. */
  traced: PropTypes.bool,
  /* From the code read: a project name, null for none, undefined if unread. */
  codeProject: PropTypes.string,
};
