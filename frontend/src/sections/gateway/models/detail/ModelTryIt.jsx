import React, { useCallback, useEffect, useId, useRef, useState } from "react";
import PropTypes from "prop-types";
import { Alert, Box, Button, FormControlLabel, Link, Switch, TextField, Typography } from "@mui/material";
import { LoadingButton } from "@mui/lab";
import { useQuery } from "@tanstack/react-query";
import { useLocation, useNavigate } from "react-router-dom";

import { RouterLink } from "src/routes/components";
import { paths } from "src/routes/paths";
import { getGatewayAuthenticationErrorMessage } from "src/utils/errorUtils";
import GatewayAdminOnly from "../../components/GatewayAdminOnly";
import ManagedRefusalAlert from "../../components/ManagedRefusalAlert";
import { readManagedFailure } from "../../utils/managedRefusals";
import { GatewayEmptyState } from "../../components/frame";
import { gatewayTestBody, sendGatewayTest, streamGatewayTest, testsUnavailable } from "../../components/gateway-test-request";
import { useGatewayAdmin } from "../../hooks/useGatewayAdmin";
import { formatCost } from "../../utils/formatters";
import { copyableMessages, PROMPTS_EXECUTOR_NOTICE } from "../../playground/handoff";
import { resolvePromptsModel } from "../../playground/resolvePromptsModel";
import { requestFailureFacts } from "../../components/requestDiagnostics";
import { callTarget, formatLatency, promptsHandoffPath, readStreamResult, readTryResult } from "./modelFacts";
import { BILLING_MODE_LABEL, billingModeLabel } from "../../utils/providerLabel";
import { buildChatRequestSnapshot } from "../requestSnapshot";
import { modelPath } from "../modelLinks";
import TryParameters from "./TryParameters";
import { PARAM_FIELDS, paramErrors, takes } from "./tryParams";
import { CopyButton, Mono, Section } from "./Section";

export const createTryState = () => ({
  prompt: "Write one sentence about the sea.", system: "", params: {}, stream: true,
  result: null, failure: null, running: false, streaming: false,
  submittedSnapshot: null, chatChoice: false,
});

function Meta({ label, children }) {
  return (
    <Box sx={{ minWidth: 0 }}>
      <Typography typography="s2" color="text.secondary" component="div">{label}</Typography>
      <Typography typography="s1" component="div" sx={{ overflowWrap: "anywhere" }}>{children}</Typography>
    </Box>
  );
}
Meta.propTypes = { label: PropTypes.string.isRequired, children: PropTypes.node };

function CompletionUnknownNotice({ facts }) {
  if (facts?.outcome !== "unknown") return null;
  return (
    <Typography typography="s2" color="text.secondary">
      Completion unknown. This gateway test may have run and been billed. {" "}
      <Link component={RouterLink} href={`${paths.dashboard.gateway.logs}${facts.requestId ? `?request_id=${encodeURIComponent(facts.requestId)}` : ""}`}>Check Logs</Link>
      {" "}before running it again. The console will not retry automatically.
    </Typography>
  );
}
CompletionUnknownNotice.propTypes = { facts: PropTypes.object };

function FailureDiagnostics({ facts }) {
  const [expanded, setExpanded] = useState(false);
  const evidence = JSON.stringify(facts, null, 2);
  return (
    <Box sx={{ display: "flex", flexDirection: "column", gap: 1 }}>
      <Box component="details" onToggle={(event) => setExpanded(event.currentTarget.open)} sx={{ border: 1, borderColor: "divider", borderRadius: 1, p: 1.5, minWidth: 0 }}>
        <Typography component="summary" typography="s2" sx={{ cursor: "pointer" }}>Request diagnostics</Typography>
        {expanded && (
          <>
            <Typography typography="s2" color="text.secondary" sx={{ mt: 1 }}>Only evidence received by the console is included. A refusal response does not prove the request was not billed.</Typography>
            <Box component="pre" aria-label="Received request evidence" sx={{ m: 0, my: 1, whiteSpace: "pre-wrap", overflowWrap: "anywhere", fontSize: "0.75rem" }}>{evidence}</Box>
            <CopyButton text={evidence} label="Copy diagnostics" message="Diagnostics copied" />
          </>
        )}
      </Box>
    </Box>
  );
}
FailureDiagnostics.propTypes = { facts: PropTypes.object.isRequired };

// Some recognized routing and timeout remedies use category "other" too.
// Only the classifier's actionless fallback should give way to generic copy.
const hasMappedRefusal = (refusal) => Boolean(refusal && (refusal.category !== "other" || refusal.action));

function Result({ result }) {
  const refused = result.status != null && (result.status < 200 || result.status >= 300);
  const mappedRefusal = hasMappedRefusal(result.refusal);
  const settled = !result.streaming && !result.stopped;
  return (
    <Box sx={{ display: "flex", flexDirection: "column", gap: 1.5 }}>
      {!result.ok && (
        <>
          {mappedRefusal ? (
            <ManagedRefusalAlert refusal={result.refusal} status={result.status} />
          ) : (
            <Alert severity={result.blocked ? "warning" : "error"}>
              <Typography typography="s2" fontWeight={600}>{result.blocked ? "Blocked by a guardrail" : "Request failed"}{refused ? ` · HTTP ${result.status}` : ""}</Typography>
              <Typography typography="s2">{result.error}</Typography>
            </Alert>
          )}
          <CompletionUnknownNotice facts={result.diagnostics} />
          {!mappedRefusal && result.diagnostics && <FailureDiagnostics facts={result.diagnostics} />}
        </>
      )}
      {(result.ok || result.answer) && (
        <Box aria-live="polite" aria-busy={result.streaming} sx={{ bgcolor: "background.neutral", borderRadius: 1, p: 1.5, whiteSpace: "pre-wrap", overflowWrap: "anywhere" }}>
          <Typography typography="s1" color={result.answer ? "text.primary" : "text.secondary"}>
            {result.answer || (result.streaming ? "Waiting for the first words…" : "(empty answer)")}
          </Typography>
        </Box>
      )}
      {result.stopped && <Typography typography="s2" color="text.secondary">Stopped before the answer was complete. A partial request may still be billed; check Logs before running it again.</Typography>}
      {result.unstreamed && <Typography typography="s2" color="text.secondary">This server does not stream Try it, so the answer arrived in one piece.</Typography>}
      {result.ok && settled && (
        <Box sx={{ display: "grid", gap: 1.5, gridTemplateColumns: { xs: "repeat(2, minmax(0, 1fr))", md: "repeat(5, minmax(0, 1fr))" } }}>
          <Meta label="Latency">{result.latencyMs !== "" ? formatLatency(result.latencyMs) : "—"}</Meta>
          <Meta label="Tokens">{result.inputTokens != null ? `${result.inputTokens} in · ${result.outputTokens ?? 0} out` : "—"}</Meta>
          <Meta label="Cost">{result.cost !== "" ? formatCost(result.cost) : "—"}</Meta>
          <Meta label={BILLING_MODE_LABEL}>{billingModeLabel(result.billingMode) || "—"}</Meta>
          <Meta label="Request id">
            {result.requestId ? (
              <Box component="span" sx={{ display: "inline-flex", alignItems: "center", gap: 0.5 }}>
                <Mono>{result.requestId}</Mono>
                <CopyButton text={result.requestId} label="Copy request id" message="Request id copied" />
              </Box>
            ) : "—"}
          </Meta>
        </Box>
      )}
      {result.requestId && !result.streaming && (
        <Typography typography="s2">
          <Link component={RouterLink} href={`${paths.dashboard.gateway.logs}?request_id=${encodeURIComponent(result.requestId)}`}>View in logs</Link>
        </Typography>
      )}
    </Box>
  );
}
Result.propTypes = { result: PropTypes.object.isRequired };

function NextActions({ openApi, providers = false }) {
  return (
    <Box sx={{ display: "flex", flexWrap: "wrap", gap: 1 }}>
      <Button variant="outlined" size="small" onClick={openApi}>Open API instructions</Button>
      {providers && <Button variant="outlined" size="small" component={RouterLink} href={paths.dashboard.gateway.providers}>Go to Providers</Button>}
    </Box>
  );
}
NextActions.propTypes = { openApi: PropTypes.func.isRequired, providers: PropTypes.bool };

// This release runs no test requests: no prompt box, one way to call the model.
// After a refused Run the state replaces the form, so it is announced.
function TryItUnavailable({ openApi, announce = false }) {
  return (
    <Box role={announce ? "alert" : undefined} sx={{ display: "flex", flexDirection: "column" }}>
      <GatewayEmptyState
        icon="catalog"
        titleComponent="h2"
        title="Try it is not available in this release"
        description="Call the model with your API key instead."
        actionLabel="Open API instructions"
        onAction={openApi}
      />
    </Box>
  );
}
TryItUnavailable.propTypes = { openApi: PropTypes.func.isRequired, announce: PropTypes.bool };

// The server answered with an HTTP error status, so no stream began: nothing
// was relayed and there is no partial answer to keep.
const refusedBeforeStream = (error) => (error?.statusCode ?? error?.response?.status) >= 400;

function usePromptsModel(model) {
  return useQuery({
    queryKey: ["agentcc-prompts-model", model],
    queryFn: () => resolvePromptsModel(model),
    enabled: Boolean(model), staleTime: 300000, retry: false, refetchOnWindowFocus: false, meta: { errorHandled: true },
  });
}
const isCancel = (error) => error?.name === "CanceledError" || error?.code === "ERR_CANCELED";

export default function ModelTryIt({ row, gatewayId, state, onStateChange, onOpenApi, onUseRequest, delegatedPlaygroundAvailable }) {
  // Standalone callers keep their existing interface; the detail owns this state.
  const [localState, setLocalState] = useState(createTryState);
  const { prompt, system, params, stream, result, failure, running, streaming, submittedSnapshot, chatChoice } = state || localState;
  const setState = onStateChange || setLocalState;
  const change = (patch) => setState((previous) => ({ ...previous, ...patch }));
  const active = useRef(null);
  const navigate = useNavigate();
  const location = useLocation();
  const openApi = onOpenApi || (() => navigate(modelPath(row.id, "api"), { replace: true, state: location.state }));
  const { canEdit } = useGatewayAdmin();
  const target = callTarget(row);
  const unknownType = row.uncatalogued || !row.type || row.type === "unknown";
  const chatAllowed = unknownType ? chatChoice : row.type === "chat";
  const promptsModel = usePromptsModel(target && chatAllowed ? target.model : "");
  const promptsNoticeId = useId();

  const stop = useCallback(() => {
    const request = active.current;
    if (!request?.streaming || request.controller.signal.aborted) return;
    // Retire callbacks before aborting: a late chunk cannot replace saved work.
    active.current = null;
    request.controller.abort();
    setState((previous) => ({
      ...previous, running: false, streaming: false,
      result: { ...(previous.result || { ok: true, answer: "" }), streaming: false, stopped: true },
    }));
  }, [setState]);
  useEffect(() => stop, [stop]);

  const invalid = Object.keys(paramErrors(params, row)).length > 0;
  const submit = async () => {
    if (running || active.current || !canEdit || !target || !chatAllowed || invalid || !prompt.trim() || (target.managed && !row.managedOn)) return;
    const supported = Object.fromEntries(PARAM_FIELDS.filter((field) => takes(row, field)).map(({ name }) => [name, params[name]]));
    const body = gatewayTestBody({ prompt, model: target.model, systemPrompt: system, params: supported });
    // Snapshot the normalized values actually sent, not the mutable editor.
    const snapshot = buildChatRequestSnapshot({
      ...body, stream,
      messages: [
        ...(body.system_prompt ? [{ role: "system", content: body.system_prompt }] : []),
        { role: "user", content: body.prompt },
      ],
    });
    const request = { controller: new AbortController(), streaming: stream };
    active.current = request;
    const publish = (patch) => {
      if (active.current === request) change(patch);
    };
    change({ running: true, streaming: stream, failure: null, result: null, submittedSnapshot: snapshot });
    try {
      if (stream) {
        try {
          const events = await streamGatewayTest(gatewayId, body, {
            signal: request.controller.signal,
            onEvents: (eventsRead) => publish({ result: readStreamResult(eventsRead) }),
          });
          publish({ result: readStreamResult(events, { finished: true }) });
        } catch (error) {
          // Only the legacy unstructured missing-endpoint response permits a
          // fallback. Coded refusals/uncertain outcomes never send twice.
          const missingEndpoint = error?.statusCode === 404 && error?.detail === "Not found." &&
            error?.code === undefined && !error?.error && !error?.result && !error?.response;
          if (!missingEndpoint || request.controller.signal.aborted) throw error;
          request.streaming = false;
          publish({ streaming: false, submittedSnapshot: { ...snapshot, stream: false } });
          publish({ result: { ...readTryResult(await sendGatewayTest(gatewayId, body)), unstreamed: true } });
        }
      } else {
        // This endpoint cannot cancel. Keep its pending state above tabs and
        // deliver its one answer there instead of offering a duplicate run.
        publish({ result: readTryResult(await sendGatewayTest(gatewayId, body)) });
      }
    } catch (error) {
      if (active.current === request) {
        if (isCancel(error) || request.controller.signal.aborted) stop();
        else setState((previous) => ({
          ...previous, failure: error,
          // A refusal's body can reach the stream reader as progress with no
          // events; that placeholder is not a cut answer, so drop it.
          result: refusedBeforeStream(error)
            ? null
            : previous.result?.streaming
              ? { ...previous.result, streaming: false, stopped: true }
              : previous.result,
        }));
      }
    } finally {
      if (active.current === request) {
        publish({ running: false, streaming: false });
        active.current = null;
      }
    }
  };

  if (!target || (target.managed && !row.managedOn) || (!unknownType && !chatAllowed)) {
    const creditsOff = target?.managed && !row.managedOn;
    return (
      <Box sx={{ display: "flex", flexDirection: "column", gap: 1.5 }}>
        <GatewayEmptyState
          icon="catalog"
          title={!target ? "This model cannot be tried here" : creditsOff ? "Future AGI credits are off" : "Try it sends chat requests only"}
          description={!target ? row.accessReason : creditsOff ? "Future AGI credits are off for your organization." : "Open API instructions for this model's request kind and supported examples."}
        />
        {creditsOff && <Typography typography="s2" color="text.secondary">Use your own provider key if you have one, or ask your organization admin about available billing options.</Typography>}
        <NextActions openApi={openApi} providers={!target || creditsOff} />
      </Box>
    );
  }
  const failureFacts = failure ? requestFailureFacts(failure) : null;
  if (testsUnavailable(delegatedPlaygroundAvailable, failureFacts) && !running) {
    return <TryItUnavailable openApi={openApi} announce={Boolean(failure)} />;
  }
  const disclosure = (
    <Alert severity="info">
      <Typography typography="s2" fontWeight={600}>Type not reported</Typography>
      Your provider did not report a model type; chat compatibility has not been verified.
      Preparing a chat test does not send a request. Run sends a billed, logged chat request.
    </Alert>
  );
  if (unknownType && !chatChoice) {
    return (
      <Box sx={{ display: "flex", flexDirection: "column", gap: 1.5 }}>
        {disclosure}
        <NextActions openApi={openApi} />
        <Button variant="outlined" size="small" sx={{ alignSelf: "flex-start" }} onClick={() => change({ chatChoice: true })}>Prepare a chat test</Button>
      </Box>
    );
  }

  const authenticationMessage = getGatewayAuthenticationErrorMessage(failure);
  const failureRefusal = authenticationMessage ? null : readManagedFailure(failure);
  const mappedFailure = hasMappedRefusal(failureRefusal);
  return (
    <Section title="Try it" note={`One request to ${target.model} through this gateway, ${target.managed ? "billed to your Future AGI credits" : "billed by your provider"} and logged like any other.`}>
      {unknownType && disclosure}
      <Typography typography="s2" color="text.secondary">Your draft and result stay here while you switch tabs. Leaving this tab stops an active stream; changing models clears this work.</Typography>
      <TextField label="System prompt (optional)" value={system} onChange={(e) => change({ system: e.target.value })} multiline minRows={1} size="small" fullWidth />
      <TextField label="Prompt" value={prompt} onChange={(e) => change({ prompt: e.target.value })} multiline minRows={3} size="small" fullWidth />
      <Box sx={{ display: "flex", gap: 1.5, flexWrap: "wrap", alignItems: "flex-start" }}>
        <TryParameters row={row} values={params} onChange={(values) => change({ params: values })} />
        <FormControlLabel
          sx={{ ml: 0, mt: 0.5 }}
          control={<Switch size="small" checked={stream} onChange={(e) => change({ stream: e.target.checked })} />}
          label={<Typography typography="s2">Stream the answer</Typography>}
        />
      </Box>
      <Box sx={{ display: "flex", gap: 1, flexWrap: "wrap", alignItems: "center" }}>
        {running && streaming ? <Button variant="outlined" size="small" onClick={stop}>Stop</Button> : (
          <GatewayAdminOnly allowed={canEdit}>
            <LoadingButton variant="contained" color="primary" size="small" loading={running} disabled={!canEdit || !prompt.trim() || invalid || running} onClick={submit}>Run</LoadingButton>
          </GatewayAdminOnly>
        )}
        {Boolean(promptsModel.data) && !promptsModel.isError && (
          <Box sx={{ display: "flex", flexDirection: "column", alignItems: "flex-start", gap: 0.5 }}>
            <Typography id={promptsNoticeId} typography="s2" color="text.secondary">{PROMPTS_EXECUTOR_NOTICE}</Typography>
            <Button variant="outlined" size="small" component={RouterLink} aria-describedby={promptsNoticeId} href={promptsHandoffPath({ model: target.model, prompt, system })}>Open in Prompts</Button>
          </Box>
        )}
      </Box>
      {promptsModel.isError && (
        <Box sx={{ display: "flex", flexWrap: "wrap", alignItems: "center", gap: 1 }}>
          <Typography typography="s2" color="text.secondary">Could not check Prompts models. Your draft is still here.</Typography>
          <Button size="small" variant="outlined" disabled={promptsModel.isFetching} onClick={() => promptsModel.refetch()}>Retry Prompts lookup</Button>
          <CopyButton text={copyableMessages({ prompt, system })} label="Copy messages" message="Messages copied" />
        </Box>
      )}
      {!canEdit && (
        <>
          <Typography typography="s2" color="text.secondary" component="p">Owner or Admin only: only an organization Owner or Admin can run a test request through the gateway.</Typography>
          <NextActions openApi={openApi} />
        </>
      )}
      {failure && (
        <>
          {mappedFailure ? (
            <ManagedRefusalAlert refusal={failureRefusal} status={failure?.response?.status ?? failure?.statusCode} />
          ) : (
            <Alert severity="error">
              <Typography typography="s2" fontWeight={600}>Request failed</Typography>
              <Typography typography="s2">{authenticationMessage || failureFacts.message}</Typography>
            </Alert>
          )}
          <CompletionUnknownNotice facts={failureFacts} />
          {!mappedFailure && <FailureDiagnostics facts={failureFacts} />}
        </>
      )}
      {result && <Result result={result} />}
      {result?.ok && !result.blocked && !result.stopped && !result.streaming && !running && submittedSnapshot && onUseRequest && (
        <Button variant="outlined" size="small" sx={{ alignSelf: "flex-start" }} onClick={() => onUseRequest(submittedSnapshot)}>Use this request in API</Button>
      )}
    </Section>
  );
}
ModelTryIt.propTypes = {
  row: PropTypes.object.isRequired,
  gatewayId: PropTypes.string,
  state: PropTypes.object,
  onStateChange: PropTypes.func,
  onOpenApi: PropTypes.func,
  onUseRequest: PropTypes.func,
  // The gateway read's delegated_playground_available; undefined: not said.
  delegatedPlaygroundAvailable: PropTypes.bool,
};
