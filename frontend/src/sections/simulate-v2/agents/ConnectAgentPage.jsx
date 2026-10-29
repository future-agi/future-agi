import { useCallback, useMemo, useState } from "react";
import { Helmet } from "react-helmet-async";
import { useNavigate, useSearchParams } from "react-router-dom";
import { Box, IconButton, Stack, Typography } from "@mui/material";
import Iconify from "src/components/iconify";
import { paths } from "src/routes/paths";
import { observeProjectById } from "../_mock/connectAgent";
import { GITHUB_REPOS } from "../_mock/integrations";
import ConnectAgentPicker from "./connect/ConnectAgentPicker";
import IssueConnectCard from "./connect/IssueConnectCard";
import { detectedRequestOf } from "./connect/detectedRequest";
import { useSaveConnectedAgent } from "./connect/useSaveConnectedAgent";
import { TextLink } from "./connect/connectBits";
import { ConnectSubmitContext, CONNECT_LABEL } from "./connect/connectSubmit";

/* Only a path inside the app — never another site. */
const safeReturn = (url) =>
  typeof url === "string" && url.startsWith("/") && !url.startsWith("//")
    ? url
    : null;

/**
 * New agent, from the Agents page: point us at one as it runs in production
 * (we make the copy simulations drive) or build one here, then set what
 * improvements may change. No environment gets built — the agent joins the
 * Connected agents list as v1, ready to be tested in any environment.
 *
 * From an Error Feed issue's "Connect agent to fix" it opens with
 * `?project=<id>&issue=<id>&return=<url>`: the Observe step picks that
 * project, and saving goes back to the issue. Connecting an agent that's
 * already in Agents (same repository, platform ID or endpoint) adds the
 * project to that record, never a second one. When it's already there,
 * "It's already in Agents" skips re-entering it: pick it, and the project is
 * added to its links — the same link its Connections tab shows.
 */
export default function ConnectAgentPage() {
  const navigate = useNavigate();
  const [params] = useSearchParams();
  const prefill = useMemo(() => {
    const project = params.get("project");
    const issue = params.get("issue");
    /* From a tracing project's own page: its name, and the way back is there. */
    const projectName = params.get("projectName");
    return project || issue
      ? {
          project: project || null,
          issue: issue || null,
          projectName: projectName || null,
        }
      : null;
  }, [params]);
  /*
    Where to go after connecting. `{agent}` in it is replaced with the new
    agent's id — how an environment build picks up the agent it sent here to
    be connected. Leaving without connecting drops that part.
  */
  const returnTo = safeReturn(params.get("return"));
  const returnBare = returnTo
    ? returnTo.replace(/[?&][a-z]+=\{agent\}/i, "")
    : null;
  const leave = useCallback(
    () => navigate(returnBare || `${paths.dashboard.agents}?tab=connected`),
    [navigate, returnBare],
  );
  /*
    Opened on one source — from an environment build's directory or paste,
    or from Integrations → GitHub with a scanned repository:
      ?src=<flow kind>&init=<json>&item=<directory id>&name=…&logo=…
      ?repo=<owner/name>&repoAgent=<scanned agent id>
  */
  const [request] = useState(() => {
    const src = params.get("src");
    if (!src) return null;
    let init = null;
    try {
      init = params.get("init") ? JSON.parse(params.get("init")) : null;
    } catch {
      init = null;
    }
    return {
      kind: src,
      initial: init,
      id: params.get("item") || src,
      meta: {
        name: params.get("name") || null,
        logo: params.get("logo") || null,
      },
      at: Date.now(),
    };
  });
  const preset = useMemo(() => {
    const repo = params.get("repo");
    const repoAgent = params.get("repoAgent");
    if (!repo || !repoAgent) return null;
    const scanned = GITHUB_REPOS.find((r) => r.name === repo)?.agents.find(
      (a) => a.id === repoAgent,
    );
    return scanned ? { repo, agent: scanned } : null;
  }, [params]);

  const saveAgent = useSaveConnectedAgent();
  const onConnect = useCallback(
    (source) => {
      const { id, exists } = saveAgent(source);
      if (returnTo) {
        /* Back to build an environment: the scenario count asked on the form comes too. */
        const to = returnTo.replace("{agent}", encodeURIComponent(id));
        navigate(
          source.scenarioCount
            ? `${to}${to.includes("?") ? "&" : "?"}count=${source.scenarioCount}`
            : to,
        );
        return;
      }
      /* Straight to the agent — its page has Finish setting up for a new one. */
      navigate(
        `${paths.dashboard.agents}/connected/${id}${exists ? "" : "?setup=1"}`,
      );
    },
    [saveAgent, navigate, returnTo],
  );

  const project = prefill?.project ? observeProjectById(prefill.project) : null;
  /*
    What the project's traces already say about the agent behind them. Then
    there's one right way in, offered first — the full catalogue sits behind
    "Connect another way":
      traceai   → its repository (code agents come in through git; we read
                  the framework and find the agent there)
      call-logs → that platform's assistant, by the ID in the logs, with the
                  key Observe's integration already holds (by reference)
  */
  const traced = project?.traced || null;
  const [showAll, setShowAll] = useState(false);
  const [detectedRequest, setRequest] = useState(null);
  return (
    <Box sx={{ height: "100%", overflow: "auto", px: 2, pt: 2, pb: 4 }}>
      <Helmet>
        <title>New agent · Agents | Future AGI</title>
      </Helmet>
      <Stack
        direction="row"
        alignItems="flex-start"
        spacing={1}
        sx={{ mb: 2.5 }}
      >
        <IconButton size="small" onClick={leave} sx={{ mt: 0.25 }}>
          <Iconify icon="solar:alt-arrow-left-linear" width={20} />
        </IconButton>
        <Box>
          <Typography typography="m2" fontWeight="fontWeightMedium">
            New agent
          </Typography>
          <Typography sx={{ typography: "s1", color: "text.secondary" }}>
            Connect it from where it runs today, or build one here.
          </Typography>
        </Box>
      </Stack>
      {/* Sent here from an environment build: say why, and that it comes back. */}
      {!prefill &&
        returnTo?.startsWith(paths.dashboard.simulate.environments) && (
          <Stack
            direction="row"
            alignItems="center"
            spacing={1.25}
            sx={{
              mb: 2,
              px: 1.75,
              py: 1.125,
              borderRadius: 1,
              border: "1px solid",
              borderColor: "divider",
              bgcolor: "background.paper",
            }}
          >
            <Iconify
              icon="solar:box-minimalistic-linear"
              width={16}
              sx={{ color: "text.secondary", flexShrink: 0 }}
            />
            <Typography sx={{ typography: "s2", flex: 1, minWidth: 0 }}>
              Connecting an agent for a new environment — once it&apos;s
              connected, its environment starts building.
            </Typography>
            <TextLink onClick={leave}>Back to Environments</TextLink>
          </Stack>
        )}
      {/* Sent here from New improvement: say why, and that it comes back. */}
      {!prefill &&
        returnTo?.startsWith(paths.dashboard.simulate.improvements) && (
          <Stack
            direction="row"
            alignItems="center"
            spacing={1.25}
            sx={{
              mb: 2,
              px: 1.75,
              py: 1.125,
              borderRadius: 1,
              border: "1px solid",
              borderColor: "divider",
              bgcolor: "background.paper",
            }}
          >
            <Iconify
              icon="solar:graph-up-linear"
              width={16}
              sx={{ color: "text.secondary", flexShrink: 0 }}
            />
            <Typography sx={{ typography: "s2", flex: 1, minWidth: 0 }}>
              Connecting an agent to improve — once it&apos;s connected, New
              improvement opens with it picked. GitHub and Upload a folder can
              be changed and proven; the others end in a suggested change.
            </Typography>
            <TextLink onClick={leave}>Back to Improvements</TextLink>
          </Stack>
        )}
      {prefill && traced && (
        <Box sx={{ mb: 2.5 }}>
          <IssueConnectCard
            issueId={prefill.issue}
            project={project}
            onConnectDetected={() => setRequest(detectedRequestOf(traced))}
            onAnotherWay={() => setShowAll((v) => !v)}
            anotherWayLabel={
              showAll ? "Hide other ways" : "Connect another way"
            }
            aside={
              returnTo ? (
                <TextLink onClick={() => navigate(returnTo)}>
                  Back to the issue
                </TextLink>
              ) : null
            }
          />
        </Box>
      )}
      {prefill && !traced && (
        <Stack
          direction="row"
          alignItems="center"
          spacing={1.25}
          sx={{
            mb: 2,
            px: 1.75,
            py: 1.125,
            borderRadius: 1,
            border: "1px solid",
            borderColor: "divider",
            bgcolor: "background.paper",
          }}
        >
          <Iconify
            icon="solar:bug-linear"
            width={16}
            sx={{ color: "text.secondary", flexShrink: 0 }}
          />
          <Box flex={1} minWidth={0}>
            <Typography sx={{ typography: "s2", fontWeight: 600 }}>
              {prefill.issue
                ? `Connecting the agent behind ${prefill.issue}`
                : prefill.projectName
                  ? `Connecting the agent behind ${prefill.projectName}`
                  : "Connecting the agent behind an Error Feed issue"}
            </Typography>
            <Typography sx={{ typography: "s3", color: "text.subtitle" }}>
              {prefill.project
                ? `We'll link ${project ? project.name : prefill.projectName || prefill.project} to it, so its Error Feed issues can be fixed.`
                : "Pick the Observe project its traces land in."}
            </Typography>
          </Box>
          {returnTo && (
            <TextLink onClick={() => navigate(returnTo)}>
              {prefill.issue || !prefill.projectName
                ? "Back to the issue"
                : "Back to the project"}
            </TextLink>
          )}
        </Stack>
      )}
      {/* From Environments the connect button also starts the build, and says so. */}
      <ConnectSubmitContext.Provider
        value={
          !prefill &&
          returnTo?.startsWith(paths.dashboard.simulate.environments)
            ? "Connect and build environment"
            : CONNECT_LABEL
        }
      >
        <ConnectAgentPicker
          onConnect={onConnect}
          prefill={prefill}
          preset={preset}
          request={detectedRequest || request}
          catalogue={!traced || showAll}
        />
      </ConnectSubmitContext.Provider>
    </Box>
  );
}
