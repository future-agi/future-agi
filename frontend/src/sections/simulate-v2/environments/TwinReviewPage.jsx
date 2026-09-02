import { useEffect, useState } from "react";
import { useNavigate, useParams } from "react-router-dom";
import { Helmet } from "react-helmet-async";
import { Box, CircularProgress } from "@mui/material";
import { paths } from "src/routes/paths";
import { useSimStore, useEnvState } from "../store";
import { protoRunId } from "../_mock/executionAdapter";
import TemplateReviewLayout from "./TemplateReviewLayout";
import TwinProvisioningView from "./TwinProvisioningView";

/**
 * Landing page after the compose flow.
 *
 * Two states:
 *   · twinBacking.status === "provisioning"
 *       → TwinProvisioningView renders inline: chat narrating the
 *         handshake on the left, sandbox spin-up animation and step
 *         timeline on the right. The view flips status to "ready"
 *         when every phase lands.
 *   · twinBacking.status === "ready"
 *       → TemplateReviewLayout renders the real review shape (chat
 *         + Overview/Agents/Contract/Scenarios/Personas/Actors/
 *         Evaluations tabs). "Provision & finish" moves the user
 *         into the full env workspace.
 */
export default function TwinReviewPage() {
  const { envId } = useParams();
  const navigate = useNavigate();
  const { state } = useSimStore();
  const { envState, patch } = useEnvState(envId);
  const env = state.myEnvironments.find((e) => e.id === envId);

  /*
    Grace period for store rehydration. On a hard refresh the reducer
    starts empty and hydrates from localStorage via an effect — the
    first render lands here before that runs and every user-created
    env looks missing. Wait briefly before declaring the env missing;
    if hydration lands, the env appears and this component re-renders.
    If the env is still missing after the grace period, bounce back
    to Environments rather than leaving the user on a dead-end page.
  */
  const [waited, setWaited] = useState(false);
  useEffect(() => {
    if (env) return undefined;
    const t = setTimeout(() => setWaited(true), 800);
    return () => clearTimeout(t);
  }, [env]);
  useEffect(() => {
    if (waited && !env) navigate(paths.dashboard.simulate.environments, { replace: true });
  }, [waited, env, navigate]);

  if (!env) {
    return (
      <Box sx={{ p: 6, display: "grid", placeItems: "center" }}>
        <CircularProgress size={20} />
      </Box>
    );
  }

  const provisioning = envState?.twinBacking?.status === "provisioning";

  const handleProvisioned = () => {
    if (!envState?.twinBacking) return;
    patch({
      twinBacking: { ...envState.twinBacking, status: "ready" },
    });
  };

  return (
    <>
      <Helmet>
        <title>{env.name} · {provisioning ? "Provisioning" : "Review"} | Future AGI</title>
      </Helmet>
      <Box sx={{ height: "calc(100vh - 64px)" }}>
        {provisioning ? (
          <TwinProvisioningView
            env={env}
            envState={envState}
            onDone={handleProvisioned}
          />
        ) : (
          <TemplateReviewLayout
            env={env}
            isTwin
            onBack={() => navigate(paths.dashboard.simulate.environments)}
            /*
              Compose flow ends by kicking off a simulation directly —
              the env is fully set up (agent connected, scenarios
              seeded, evals presetted) so there's nothing left to
              configure. Straight into the run.
            */
            onFinish={() => navigate(
              paths.dashboard.simulate.simulationRun(envId, protoRunId(envId, Date.now().toString(36))),
            )}
          />
        )}
      </Box>
    </>
  );
}
