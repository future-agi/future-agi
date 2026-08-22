import { useEffect, useRef, useState } from "react";
import { Box, Button, IconButton, Stack, Tab, Tabs, Typography } from "@mui/material";
import {
  useAlkContract,
  useAlkHistory,
  useAlkScenarios,
  useAlkSessions,
  useAlkSimulation,
  useAlkRuns,
  useAlkSimulations,
  useAlkGeneration,
  useAlkStatus,
  useAlkSubgoals,
  useAlkWorld,
  useCreateAlkSession,
  useDeleteAlkSession,
  useOpenAlkSession,
  useSetAlkStage,
} from "src/api/al-environment/alEnvironment";
import { useNavigate, useParams } from "react-router-dom";
import { enqueueSnackbar } from "src/components/snackbar";
import { RouterLink } from "src/routes/components";
import { paths } from "src/routes/paths";
import { alkBaseUrl } from "src/api/al-environment/client";
import { useAlkConversation } from "src/api/al-environment/useAlkConversation";
import { ALK_MONO } from "./alkTokens";
import Composer from "./Composer";
import HarnessUnreachable from "./HarnessUnreachable";
import SessionPicker from "./SessionPicker";
import StageRoadmap from "./StageRoadmap";
import { ALK_STAGES } from "./stages";
import StatusReadout from "./StatusReadout";
import TranscriptPane from "./TranscriptPane";
import ContractTab from "./tabs/ContractTab";
import EnvironmentTab from "./tabs/EnvironmentTab";
import ScenariosTab from "./tabs/ScenariosTab";
import RunsTab from "./tabs/RunsTab";

/** Each tab carries what it holds, so the reader can see where the work has got to. */
const TABS = [
  {
    value: "contract",
    label: "Contract",
    count: (s) => (s?.have?.contract ? "✓" : ""),
  },
  {
    value: "world",
    label: "Environment",
    count: (s) => (s?.have?.world ? "✓" : ""),
  },
  {
    value: "scenarios",
    label: "Scenarios",
    count: (s) => s?.have?.scenarios || "",
  },
  { value: "runs", label: "Runs", count: (s) => s?.have?.runs || "" },
];

const AlEnvironmentView = () => {
  const [tab, setTab] = useState("contract");
  const [selectedRunId, setSelectedRunId] = useState(null);

  const { sessionId } = useParams();
  const navigate = useNavigate();

  const { status, isError, refetch } = useAlkStatus();
  const { generation } = useAlkGeneration();
  const hasSession = Boolean(status?.session);
  const openId = status?.session?.id;
  // Added by the backend proxy at the top level of the status object, beside `busy` — not
  // inside `session`. Absent entirely when talking to the harness directly, hence nullable.
  const runTestId = status?.run_test_id;

  const { sessions, openSessionId } = useAlkSessions();
  const { messages } = useAlkHistory(hasSession);
  const { contract } = useAlkContract(hasSession);
  const { world } = useAlkWorld(hasSession);
  const { subgoals } = useAlkSubgoals(hasSession);
  const { scenarios } = useAlkScenarios(hasSession);
  const { runs } = useAlkSimulations(hasSession);
  // Sessions whose results predate the simulations format still have readable runs.
  const { legacyRuns } = useAlkRuns(hasSession);
  const { run } = useAlkSimulation(selectedRunId);

  const createSession = useCreateAlkSession();
  const openSession = useOpenAlkSession();
  const deleteSession = useDeleteAlkSession();
  const setStage = useSetAlkStage();
  const conversation = useAlkConversation();

  /**
   * The URL is what decides which session is open, so a refresh or a shared link lands on the
   * same environment. The harness holds one conversation at a time, so arriving at a different
   * id has to open it — guarded by a ref because status refetches constantly and a second open
   * mid-flight would be refused with a 409.
   */
  const opening = useRef(null);
  const refused = useRef(null);
  const wanted = useRef(sessionId);
  wanted.current = sessionId;

  // One attempt per navigation. Arriving at the same id again — a retry, a fresh link — is a
  // new attempt; a status refetch is not.
  useEffect(() => {
    refused.current = null;
  }, [sessionId]);

  useEffect(() => {
    if (!sessionId || !status || status.busy) return;
    if (openId === sessionId) return;
    // Any open in flight blocks the next one, not just one for the same id. The harness takes
    // a single request at a time and answers 409 to the second — which would look like the
    // session was bad and bounce the operator off a perfectly good environment. When the first
    // settles, status changes and this runs again for whatever the URL now asks for.
    if (opening.current) return;
    // Without remembering the id that failed this keeps firing until the redirect lands, which
    // is the repainting loop it exists to end.
    if (refused.current === sessionId) return;

    const target = sessionId;
    opening.current = target;
    openSession.mutate(target, {
      onSuccess: conversation.clearLive,
      onError: (failed) => {
        refused.current = target;
        // The answer arrives after a navigation may already have moved on. Reporting a
        // failure for an environment nobody is looking at any more would be noise.
        if (wanted.current !== target) return;
        const gone = failed?.response?.status === 404;
        enqueueSnackbar(
          gone
            ? `That environment no longer exists (${target})`
            : failed?.response?.data?.error ||
                "Could not open that environment",
          { variant: "error" },
        );
        // Only a missing environment has nowhere to go. A refusal or a transient failure
        // leaves a perfectly valid URL, so stay on it and say what happened.
        if (gone)
          navigate(paths.dashboard.simulate.alEnvironment, { replace: true });
      },
      onSettled: () => {
        opening.current = null;
      },
    });
  }, [
    sessionId,
    openId,
    status,
    openSession,
    conversation.clearLive,
    navigate,
  ]);

  /** Changing session is a navigation; opening it is what the URL change then causes. */
  const goToSession = (id) =>
    navigate(paths.dashboard.simulate.alEnvironmentDetail(id));

  if (isError) {
    return (
      <HarnessUnreachable
        baseUrl={alkBaseUrl(import.meta.env)}
        onRetry={refetch}
      />
    );
  }

  /**
   * The harness holds one conversation at a time and refuses to swap it while a stage is
   * running, so the URL can name one environment while the server still has another open.
   * Say so, rather than drawing the open one's contract, world and scenarios underneath a URL
   * that claims they belong to something else — which reads as this environment's own work.
   */
  if (sessionId && openId && sessionId !== openId) {
    return (
      <Stack
        spacing={1}
        sx={{ height: "100%", alignItems: "center", justifyContent: "center", p: 4 }}
      >
        <Typography sx={{ fontFamily: ALK_MONO }}>
          {status?.busy
            ? `${openId} is still running`
            : `${openId} is the open environment`}
        </Typography>
        <Typography variant="body2" color="text.secondary" sx={{ textAlign: "center" }}>
          Only one environment can be open at a time.
          {status?.busy ? " Wait for it to finish, or stop it, then come back." : ""}
        </Typography>
        <Stack direction="row" spacing={1} sx={{ pt: 1 }}>
          <Button size="small" variant="outlined" onClick={() => goToSession(openId)}>
            Go to {openId}
          </Button>
          {!status?.busy && (
            <Button
              size="small"
              variant="contained"
              onClick={() => openSession.mutate(sessionId)}
            >
              Open this one
            </Button>
          )}
        </Stack>
      </Stack>
    );
  }

  /**
   * The harness answers 409 rather than interleaving when a stage is already running, and its
   * body carries the reason. Show that sentence rather than a generic failure — it is the only
   * thing that tells the operator to simply wait.
   */
  const refusalSource = [setStage, createSession, openSession, deleteSession].find(
    (one) => one.error,
  );
  const refusal = refusalSource?.error;
  // Conversation errors are rendered in the thread by TranscriptPane; only refusals from the
  // session and stage controls need saying up here, next to the controls that caused them.
  const refusalMessage =
    refusal?.response?.data?.error || refusal?.message || "";

  // The live half is only shown while a turn is running. The hook stays "streaming" until the
  // finished turn has been refetched into history, so the handover happens with neither a gap
  // nor a moment where history and `live` both hold the same turn.
  const transcript = conversation.streaming
    ? [...messages, ...conversation.live]
    : // Once the turn is in history the live copy would double it — except the error lines,
      // which we author ourselves and the harness never writes down.
      [...messages, ...conversation.live.filter((one) => one.role === "error")];

  /** The roadmap is navigation as well as a control: opening a finished stage shows its output. */
  const selectStage = (stageKey) => {
    const stage = ALK_STAGES.find((one) => one.key === stageKey);
    if (stage?.tab) setTab(stage.tab);
    setStage.mutate(stageKey);
  };

  return (
    <Stack sx={{ height: "100%" }}>
      <Stack
        direction="row"
        alignItems="center"
        justifyContent="space-between"
        spacing={2}
        sx={{
          px: 2,
          py: 1.5,
          flexWrap: "wrap",
          gap: 1,
          bgcolor: "background.paper",
          // Edge to edge like every other rule on the page, so the header and the
          // tabs row read as one system instead of two.
          borderBottom: "1px solid",
          borderColor: "divider",
        }}
      >
        <Stack direction="row" spacing={1.5} alignItems="center">
          <Box
            component={RouterLink}
            href={paths.dashboard.simulate.alEnvironment}
            aria-label="all environments"
            sx={{
              fontFamily: ALK_MONO,
              fontSize: 26,
              lineHeight: 1,
              color: "text.secondary",
              textDecoration: "none",
              display: "flex",
              alignItems: "center",
              "&:hover": { color: "text.primary" },
            }}
          >
            ‹
          </Box>

          <SessionPicker
            sessions={sessions}
            openSessionId={openSessionId}
            busy={Boolean(status?.busy)}
            // Opening changes which conversation is open, so the turn still on screen
            // belongs to the old one. Stored history refetches itself; the live half has to be
            // dropped explicitly or the previous session's messages hang around under the new one.
            onOpen={goToSession}
          />
        </Stack>
        <StageRoadmap status={status} onSelectStage={selectStage} />
        <StatusReadout
          spentUsd={status?.spent_usd}
          busy={Boolean(status?.busy)}
        />
      </Stack>

      {refusalMessage && (
        <Stack
          direction="row"
          alignItems="center"
          spacing={1}
          sx={{
            mx: 2,
            my: 1,
            pl: 1.25,
            borderLeft: "2px solid",
            borderColor: "accent.fail",
          }}
        >
          <Typography
            sx={{ fontFamily: ALK_MONO, fontSize: 12.5, color: "error.main", flexGrow: 1 }}
          >
            {refusalMessage}
          </Typography>
          <IconButton
            size="small"
            aria-label="dismiss"
            // Resetting the mutation clears its stored error, which is all the banner reads.
            onClick={() => refusalSource?.reset()}
            sx={{ color: "error.main", p: 0.25 }}
          >
            <Box component="span" sx={{ fontSize: 14, lineHeight: 1 }}>✕</Box>
          </IconButton>
        </Stack>
      )}

      <Stack direction="row" sx={{ flexGrow: 1, minHeight: 0 }}>
        <Box
          data-testid="alk-transcript-pane"
          sx={{
            // flexShrink 0 keeps the split fixed. Without it the pane shrinks by whatever the
            // active tab's content happens to be wide, so the transcript jumped between tabs.
            width: "40%",
            minWidth: 320,
            flexShrink: 0,
            display: "flex",
            flexDirection: "column",
            bgcolor: "background.paper",
            borderRight: "1px solid",
            borderColor: "divider",
          }}
        >
          <Stack sx={{ height: "100%" }}>
            <Box sx={{ flexGrow: 1, minHeight: 0, overflowY: "auto" }}>
              <TranscriptPane
                messages={transcript}
                hasSession={hasSession}
                thinking={conversation.thinking}
                spentUsd={status?.spent_usd}
                generation={generation}
                onDismissError={conversation.dismissLive}
              />
            </Box>
            <Composer
              onSay={conversation.say}
              onRun={conversation.runScenarios}
              onStop={conversation.stop}
              streaming={conversation.streaming}
              status={status}
              sessionId={status?.session?.id}
            />
          </Stack>
        </Box>

        <Box
          data-testid="alk-artifact-pane"
          // flexBasis 0 so the tab content's intrinsic width never feeds back into the split.
          sx={{
            flexGrow: 1,
            flexBasis: 0,
            minWidth: 0,
            display: "flex",
            flexDirection: "column",
          }}
        >
          <Stack
            direction="row"
            alignItems="center"
            sx={{
              backgroundColor: "background.paper",
              borderBottom: (theme) => `1px solid ${theme.palette.divider}`,
              pr: 2,
            }}
          >
            <Tabs
              value={tab}
              onChange={(event, next) => setTab(next)}
              // The theme defaults every Tabs to variant="scrollable", which draws ‹ › buttons
              // even though four tabs always fit.
              variant="standard"
              scrollButtons={false}
              sx={{
                px: 2,
                minHeight: 40,
                flexGrow: 1,
                // The theme spaces tabs with `&:not(:last-of-type) { marginRight }`, so the
                // override has to match that selector to win. The reference's tabs sit next to
                // each other and are spaced by their own padding instead.
                "& .MuiTab-root:not(:last-of-type)": { marginRight: 0 },
                "& .MuiTab-root": {
                  minHeight: 40,
                  minWidth: 0,
                  paddingLeft: "14px",
                  paddingRight: "14px",
                  fontFamily: ALK_MONO,
                  fontSize: 11.8,
                  letterSpacing: "0.04em",
                  textTransform: "none",
                },
              }}
            >
              {TABS.map((one) => (
                <Tab
                  key={one.value}
                  value={one.value}
                  label={
                    <Box
                      component="span"
                      sx={{
                        display: "inline-flex",
                        gap: 0.6,
                        alignItems: "baseline",
                      }}
                    >
                      {one.label}
                      {one.count(status) && (
                        <Box component="span" sx={{ opacity: 0.55 }}>
                          {one.count(status)}
                        </Box>
                      )}
                    </Box>
                  }
                />
              ))}
            </Tabs>

            {/* The harness reports the platform run this session belongs to. Until it does,
                there is nowhere to send anyone, so the button says so rather than guessing. */}
            <Button
              size="small"
              variant="contained"
              disabled={!runTestId}
              {...(runTestId
                ? {
                    component: RouterLink,
                    href: paths.dashboard.simulate.testRuns(runTestId),
                  }
                : {})}
              title={
                runTestId
                  ? "Open this session's simulation runs on the platform"
                  : "Available once this session has reported a run"
              }
            >
              View Simulations
            </Button>
          </Stack>
          <Box
            sx={{
              flexGrow: 1,
              overflowY: "auto",
              // No card here: each tab draws its own panes, and wrapping them in another
              // bordered box produced a card inside a card.
              p: 2,
            }}
          >
            {tab === "contract" && <ContractTab contract={contract} />}
            {tab === "world" && (
              <EnvironmentTab world={world} subgoals={subgoals} />
            )}
            {tab === "scenarios" && (
              <ScenariosTab
                scenarios={scenarios}
                // The per-scenario records from /api/runs, not the simulation summaries:
                // this is looked up by scenario name, and a summary has no `scenario`.
                runs={legacyRuns}
                hasWorld={Boolean(status?.have?.world)}
                onSay={conversation.say}
                // Nothing to jump to while the Runs tab is hidden.
                // onSeeRun={() => setTab("runs")}
              />
            )}
            {tab === "runs" && (
              <RunsTab
                runs={runs}
                selectedRunId={selectedRunId}
                onSelectRun={setSelectedRunId}
                run={run}
                legacyRuns={legacyRuns}
              />
            )}
          </Box>
        </Box>
      </Stack>
    </Stack>
  );
};

export default AlEnvironmentView;
