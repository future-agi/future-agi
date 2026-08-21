import { Box } from "@mui/material";
import { useCallback, useMemo } from "react";
import { Helmet } from "react-helmet-async";
import { useNavigate } from "react-router-dom";

import {
  useAlkSessions,
  useCreateAlkSession,
} from "src/api/al-environment/alEnvironment";
import { paths } from "src/routes/paths";
import EnvironmentsListView from "src/sections/rl-environments/EnvironmentsListView";

/**
 * A session as the list renders it. The harness nests its counts under `has` and calls the
 * session `id`, so without this the rows arrive with every column undefined and a list of
 * real environments reads as an empty one.
 */
const asEnvironment = (session) => ({
  session_id: session.id,
  agent: session.agent,
  title: session.title,
  // The harness titles a session with the agent's one-liner once it knows one.
  one_liner: session.title,
  created: session.created,
  updated: session.updated,
  stage: session.stage,
  sub_goals: session.has?.sub_goals ?? 0,
  scenarios: session.has?.scenarios ?? 0,
  runs: session.has?.runs ?? 0,
  runs_passed: session.has?.runs_passed ?? 0,
});

const RlEnvironments = () => {
  const navigate = useNavigate();

  const createSession = useCreateAlkSession();
  const { sessions, isLoading } = useAlkSessions();
  const environments = useMemo(
    () => (sessions ?? []).map(asEnvironment),
    [sessions],
  );

  const handleOpen = useCallback(
    (sessionId) =>
      navigate(paths.dashboard.simulate.alEnvironmentDetail(sessionId)),
    [navigate],
  );

  // Creating returns the fresh status, so the new session's id comes back with it and there
  // is nothing to look up before navigating.
  const handleAdd = useCallback(
    () =>
      createSession.mutate("", {
        onSuccess: (status) => {
          if (status?.session?.id) {
            navigate(
              paths.dashboard.simulate.alEnvironmentDetail(status.session.id),
            );
          }
        },
      }),
    [createSession, navigate],
  );

  return (
    <>
      <Helmet>
        <title>RL Environments</title>
      </Helmet>
      <Box
        sx={{
          backgroundColor: "background.default",
          height: "100%",
          display: "flex",
          flexDirection: "column",
          overflow: "hidden",
        }}
      >
        <EnvironmentsListView
          environments={environments}
          isLoading={isLoading}
          onOpen={handleOpen}
          onAdd={handleAdd}
        />
      </Box>
    </>
  );
};

export default RlEnvironments;
