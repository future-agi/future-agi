import React, {
  useCallback,
  useEffect,
  useLayoutEffect,
  useRef,
  useState,
} from "react";
import PropTypes from "prop-types";
import { Box, Button, Modal, Portal, Typography } from "@mui/material";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { useOrganization } from "src/contexts/OrganizationContext";
import { useWorkspace } from "src/contexts/WorkspaceContext";
import { useGetTraceDetail } from "src/api/project/trace-detail";
import TraceDetailDrawerV2 from "src/components/traceDetail/TraceDetailDrawerV2";
import VoiceDetailDrawerV2 from "src/components/VoiceDetailDrawerV2/VoiceDetailDrawerV2";
import { useVoiceCallDetail } from "src/sections/agents/helper";
import SourceNavigationRow from "./SourceNavigationRow";
import { useEvalLogSourceNavigation } from "./useEvalLogSourceNavigation";

const errorStatus = (error) => error?.statusCode ?? error?.response?.status;
const temporary = { status: "temporarily_unavailable" };
const invalid = { status: "invalid_reference" };
const isReady = (nav) =>
  nav?.status === "ready" &&
  ["trace", "voice_call"].includes(nav.kind) &&
  [nav.project_id, nav.trace_id, nav.span_id].every(
    (id) => typeof id === "string" && id.length > 0,
  );

function VoiceBody({ pinned, onClose, onState }) {
  const query = useVoiceCallDetail(pinned.trace_id, {
    enabled: true,
    projectId: pinned.project_id,
  });
  const mismatched =
    (!!query.data?.trace_id && query.data.trace_id !== pinned.trace_id) ||
    (!!query.data?.project_id && query.data.project_id !== pinned.project_id);
  useEffect(() => {
    if (query.isPending) return;
    onState(
      query.isError
        ? { isError: true, error: query.error }
        : { nav: mismatched ? invalid : query.data ? pinned : temporary },
    );
  }, [
    query.isPending,
    query.isError,
    query.error,
    query.data,
    mismatched,
    pinned,
    onState,
  ]);
  if (query.isError)
    return (
      <SourceNavigationRow
        isError
        error={query.error}
        disabled={query.isFetching}
        onRetry={() => query.refetch({ cancelRefetch: false })}
      />
    );
  if (query.isPending) return <SourceNavigationRow isPending />;
  if (mismatched) return <SourceNavigationRow nav={invalid} />;
  if (!query.data)
    return (
      <SourceNavigationRow
        nav={temporary}
        onRetry={() => query.refetch({ cancelRefetch: false })}
      />
    );
  const data = {
    ...query.data,
    id: query.data.id || pinned.trace_id,
    trace_id: query.data.trace_id || pinned.trace_id,
    project_id: pinned.project_id,
    module: "project",
  };
  return (
    <VoiceDetailDrawerV2
      data={data}
      embedded
      onClose={onClose}
      hidePathTabs
      hideAnnotationTab={false}
    />
  );
}

function TraceBody({ pinned, onClose, revalidate, onViewerReady, onState }) {
  // Shares the viewer's exact key inside this host's private query client.
  const query = useGetTraceDetail(pinned.trace_id, {
    projectId: pinned.project_id,
  });
  const [validation, setValidation] = useState(null);
  const status = errorStatus(query.error);
  const mismatched =
    query.isSuccess &&
    !!query.data &&
    (query.data.trace?.id !== pinned.trace_id ||
      query.data.trace?.project !== pinned.project_id ||
      !query.data.observation_spans?.some(
        ({ observation_span: span }) =>
          span?.id === pinned.span_id && span.trace === pinned.trace_id,
      ) ||
      query.data.observation_spans?.some(
        ({ observation_span: span }) =>
          span?.observation_type === "conversation" && !span.parent_span_id,
      ));
  useEffect(() => {
    if (!(query.isError && status === 400) && !mismatched) return undefined;
    let active = true;
    setValidation(null);
    revalidate().then(
      (fresh) => {
        if (active)
          setValidation(
            fresh.status === "ready"
              ? mismatched
                ? invalid
                : temporary
              : fresh,
          );
      },
      () => {
        if (active) setValidation(temporary);
      },
    );
    return () => {
      active = false;
    };
  }, [
    query.isError,
    query.errorUpdatedAt,
    query.dataUpdatedAt,
    status,
    mismatched,
    revalidate,
  ]);

  useEffect(() => {
    if (query.isPending) return;
    if ((query.isError && status === 400) || mismatched)
      onState({ nav: validation || temporary });
    else if (query.isError) onState({ isError: true, error: query.error });
    else onState({ nav: query.data ? pinned : temporary });
  }, [
    query.isPending,
    query.isError,
    query.error,
    query.data,
    status,
    mismatched,
    validation,
    pinned,
    onState,
  ]);

  useLayoutEffect(() => {
    onViewerReady();
  }, [query.isSuccess, query.isError, onViewerReady]);

  if ((query.isError && status === 400) || mismatched) {
    return (
      <SourceNavigationRow
        nav={validation}
        isPending={!validation}
        onRetry={() => query.refetch({ cancelRefetch: false })}
        disabled={query.isFetching}
      />
    );
  }
  if (query.isError) {
    return (
      <SourceNavigationRow
        isError
        error={query.error}
        onRetry={() => query.refetch({ cancelRefetch: false })}
        disabled={query.isFetching}
      />
    );
  }
  if (query.isPending) return <SourceNavigationRow isPending />;
  if (!query.data)
    return (
      <SourceNavigationRow
        nav={temporary}
        onRetry={() => query.refetch({ cancelRefetch: false })}
      />
    );
  return (
    <TraceDetailDrawerV2
      open
      traceId={pinned.trace_id}
      projectId={pinned.project_id}
      initialSpanId={pinned.span_id}
      hideOpenInNewTab
      hasPrev={false}
      hasNext={false}
      onClose={onClose}
    />
  );
}

function SourceDialog({ pinned, onClose, revalidate, onState }) {
  // Reused hooks omit org/workspace in their keys. A new client for each open
  // prevents even a single paint from another scope's cached destination.
  const [client] = useState(
    () =>
      new QueryClient({
        defaultOptions: {
          queries: {
            retry: false,
            gcTime: 0,
            refetchOnWindowFocus: false,
            refetchOnReconnect: false,
            meta: { errorHandled: true },
          },
        },
      }),
  );
  useEffect(() => () => client.clear(), [client]);
  const bodyRef = useRef(null);
  const [chromeContainer, setChromeContainer] = useState(null);
  const locateViewer = useCallback(() => {
    setChromeContainer(
      bodyRef.current?.querySelector(".MuiDrawer-paper") || null,
    );
  }, []);

  return (
    <Modal
      open
      onClose={onClose}
      disableRestoreFocus
      aria-labelledby="eval-log-source-title"
    >
      <Box
        role="dialog"
        aria-modal="true"
        aria-labelledby="eval-log-source-title"
        tabIndex={-1}
        sx={{
          position: "absolute",
          right: 0,
          top: 0,
          height: "100%",
          width: { xs: "100vw", md: "60vw" },
          bgcolor: chromeContainer ? "transparent" : "background.paper",
          pointerEvents: chromeContainer ? "none" : "auto",
          display: "flex",
          flexDirection: "column",
          outline: 0,
          // Only the narrow layout overrides width; desktop resize/fullscreen stay viewer-owned.
          "& .MuiDrawer-paper": {
            pointerEvents: "auto",
            ["@media (max-width: 899px)"]: { width: "100vw !important" },
          },
        }}
      >
        <Portal container={chromeContainer} disablePortal={!chromeContainer}>
          <Box
            sx={{
              order: -1,
              flexShrink: 0,
              display: "flex",
              alignItems: "center",
              gap: 1,
              flexWrap: "wrap",
              p: 1.5,
              borderBottom: "1px solid",
              borderColor: "divider",
              bgcolor: "background.paper",
            }}
          >
            <Button
              type="button"
              size="small"
              variant="outlined"
              onClick={onClose}
            >
              Back to evaluation
            </Button>
            <Typography
              id="eval-log-source-title"
              variant="subtitle2"
              sx={{ flex: 1 }}
            >
              {pinned.kind === "trace" ? "Trace" : "Call"}
            </Typography>
            <Button type="button" size="small" onClick={onClose}>
              Close
            </Button>
          </Box>
        </Portal>
        <Box ref={bodyRef} sx={{ flex: 1, minHeight: 0, overflow: "auto" }}>
          <QueryClientProvider client={client}>
            {pinned.kind === "trace" ? (
              <TraceBody
                pinned={pinned}
                onClose={onClose}
                revalidate={revalidate}
                onViewerReady={locateViewer}
                onState={onState}
              />
            ) : (
              <VoiceBody pinned={pinned} onClose={onClose} onState={onState} />
            )}
          </QueryClientProvider>
        </Box>
      </Box>
    </Modal>
  );
}

function ScopedSourceHost({ logId, onLogUnavailable }) {
  const query = useEvalLogSourceNavigation(logId);
  const [pinned, setPinned] = useState(null);
  const [rowResult, setRowResult] = useState(null);
  const [busy, setBusy] = useState(false);
  const active = useRef(true);
  const inFlight = useRef(false);
  const trigger = useRef(null);
  const statusRef = useRef(null);
  const restoreFocus = useRef(false);
  const destinationResult = useRef(null);
  const recordDestinationState = useCallback((result) => {
    destinationResult.current = result;
  }, []);
  useEffect(() => {
    active.current = true;
    return () => {
      active.current = false;
    };
  }, []);
  useEffect(() => {
    // 401 is the session-expiry path. A 403/404 on the enrichment request is a
    // source outcome, not proof the evaluation log is gone: the view never
    // returns those codes for a source failure, and a source failure must not
    // discard an evaluation the base query already loaded (PRD preserve-the-
    // evaluation rule). The row renders the source-unavailable state itself.
    if (query.isError && errorStatus(query.error) === 401)
      onLogUnavailable?.();
  }, [query.isError, query.error, onLogUnavailable]);
  useEffect(() => {
    if (!pinned && restoreFocus.current) {
      restoreFocus.current = false;
      if (
        rowResult?.nav?.status === "ready" &&
        trigger.current?.isConnected &&
        !trigger.current.disabled
      )
        trigger.current.focus();
      else statusRef.current?.focus();
    }
  }, [pinned, busy, rowResult]);

  const check = async (event, openViewer) => {
    if (inFlight.current) return;
    inFlight.current = true;
    trigger.current = event.currentTarget;
    setBusy(true);
    try {
      const fresh = await query.revalidate();
      if (!active.current) return;
      const nav = fresh.status === "ready" && !isReady(fresh) ? invalid : fresh;
      setRowResult({ nav });
      if (openViewer && isReady(nav)) {
        destinationResult.current = null;
        setPinned({ ...nav });
      } else restoreFocus.current = true;
    } catch (error) {
      if (!active.current) return;
      setRowResult({ isError: true, error });
      restoreFocus.current = true;
    } finally {
      inFlight.current = false;
      if (active.current) setBusy(false);
    }
  };
  const close = () => {
    restoreFocus.current = true;
    if (destinationResult.current) setRowResult(destinationResult.current);
    setPinned(null);
  };
  return (
    <>
      <SourceNavigationRow
        ref={statusRef}
        {...(rowResult || {
          nav: query.data,
          isPending: query.isPending,
          isError: query.isError,
          error: query.error,
        })}
        disabled={busy}
        onActivate={(event) => check(event, true)}
        onRetry={(event) => check(event, false)}
      />
      {pinned && (
        <SourceDialog
          pinned={pinned}
          onClose={close}
          revalidate={query.revalidate}
          onState={recordDestinationState}
        />
      )}
    </>
  );
}

export default function EvalLogSourceHost(props) {
  const { currentOrganizationId } = useOrganization();
  const { currentWorkspaceId } = useWorkspace();
  // Remount before paint on every entry/scope change, including pending activation.
  return (
    <ScopedSourceHost
      key={JSON.stringify([
        currentOrganizationId,
        currentWorkspaceId,
        props.logId,
      ])}
      {...props}
    />
  );
}

const bodyPropTypes = {
  pinned: PropTypes.object.isRequired,
  onClose: PropTypes.func.isRequired,
  onState: PropTypes.func.isRequired,
};
VoiceBody.propTypes = bodyPropTypes;
TraceBody.propTypes = {
  ...bodyPropTypes,
  revalidate: PropTypes.func.isRequired,
  onViewerReady: PropTypes.func.isRequired,
};
SourceDialog.propTypes = {
  ...bodyPropTypes,
  revalidate: PropTypes.func.isRequired,
};
ScopedSourceHost.propTypes = {
  logId: PropTypes.string.isRequired,
  onLogUnavailable: PropTypes.func,
};
EvalLogSourceHost.propTypes = ScopedSourceHost.propTypes;
