import React, { useEffect, useRef, useState } from "react";
import PropTypes from "prop-types";
import {
  Alert,
  Box,
  Button,
  FormControlLabel,
  Link,
  Switch,
  TextField,
  Typography,
} from "@mui/material";
import { LoadingButton } from "@mui/lab";
import { useQuery } from "@tanstack/react-query";

import { RouterLink } from "src/routes/components";
import { paths } from "src/routes/paths";
import axios, { endpoints } from "src/utils/axios";

import GatewayAdminOnly from "../../components/GatewayAdminOnly";
import { GatewayEmptyState } from "../../components/frame";
import {
  gatewayTestBody,
  sendGatewayTest,
  streamGatewayTest,
} from "../../components/gateway-test-request";
import { useGatewayAdmin } from "../../hooks/useGatewayAdmin";
import { formatCost } from "../../utils/formatters";
import { workbenchModelFor } from "../../playground/handoff";
import {
  callTarget,
  formatLatency,
  promptsHandoffPath,
  readStreamResult,
  readTryResult,
} from "./modelFacts";
import {
  BILLING_MODE_LABEL,
  billingModeLabel,
} from "../../utils/providerLabel";
import TryParameters from "./TryParameters";
import { paramErrors } from "./tryParams";
import { CopyButton, Mono, Section } from "./Section";

const DEFAULT_PROMPT = "Write one sentence about the sea.";

function Meta({ label, children }) {
  return (
    <Box sx={{ minWidth: 0 }}>
      <Typography typography="s2" color="text.secondary" component="div">
        {label}
      </Typography>
      <Typography
        typography="s1"
        component="div"
        sx={{ overflowWrap: "anywhere" }}
      >
        {children}
      </Typography>
    </Box>
  );
}

Meta.propTypes = {
  label: PropTypes.string.isRequired,
  children: PropTypes.node,
};

function Answer({ text, streaming }) {
  return (
    <Box
      aria-live="polite"
      aria-busy={streaming}
      sx={{
        bgcolor: "background.neutral",
        borderRadius: 1,
        p: 1.5,
        whiteSpace: "pre-wrap",
        overflowWrap: "anywhere",
      }}
    >
      <Typography
        typography="s1"
        color={text ? "text.primary" : "text.secondary"}
      >
        {text ||
          (streaming ? "Waiting for the first words…" : "(empty answer)")}
      </Typography>
    </Box>
  );
}

Answer.propTypes = { text: PropTypes.string, streaming: PropTypes.bool };

function Result({ result }) {
  const refused =
    result.status != null && (result.status < 200 || result.status >= 300);
  const settled = !result.streaming && !result.stopped;
  return (
    <Box sx={{ display: "flex", flexDirection: "column", gap: 1.5 }}>
      {!result.ok && (
        <Alert severity={result.blocked ? "warning" : "error"}>
          {result.blocked ? "Blocked by a guardrail. " : ""}
          {refused ? `HTTP ${result.status}: ` : ""}
          {result.error}
        </Alert>
      )}
      {(result.ok || result.answer) && (
        <Answer text={result.answer} streaming={result.streaming} />
      )}
      {result.stopped && (
        <Typography typography="s2" color="text.secondary">
          Stopped before the answer was complete.
        </Typography>
      )}
      {result.unstreamed && (
        <Typography typography="s2" color="text.secondary">
          This server does not stream Try it, so the answer arrived in one
          piece.
        </Typography>
      )}
      {result.ok && settled && (
        <Box
          sx={{
            display: "grid",
            gap: 1.5,
            gridTemplateColumns: {
              xs: "repeat(2, minmax(0, 1fr))",
              md: "repeat(5, minmax(0, 1fr))",
            },
          }}
        >
          <Meta label="Latency">
            {result.latencyMs !== "" ? formatLatency(result.latencyMs) : "—"}
          </Meta>
          <Meta label="Tokens">
            {result.inputTokens != null
              ? `${result.inputTokens} in · ${result.outputTokens ?? 0} out`
              : "—"}
          </Meta>
          <Meta label="Cost">
            {result.cost !== "" ? formatCost(result.cost) : "—"}
          </Meta>
          <Meta label={BILLING_MODE_LABEL}>
            {billingModeLabel(result.billingMode) || "—"}
          </Meta>
          <Meta label="Request id">
            {result.requestId ? (
              <Box
                component="span"
                sx={{ display: "inline-flex", alignItems: "center", gap: 0.5 }}
              >
                <Mono>{result.requestId}</Mono>
                <CopyButton
                  text={result.requestId}
                  label="Copy request id"
                  message="Request id copied"
                />
              </Box>
            ) : (
              "—"
            )}
          </Meta>
        </Box>
      )}
      {result.requestId && !result.streaming && (
        <Typography typography="s2">
          <Link
            component={RouterLink}
            href={`${paths.dashboard.gateway.logs}?request_id=${encodeURIComponent(result.requestId)}`}
          >
            View in logs
          </Link>
        </Typography>
      )}
    </Box>
  );
}

Result.propTypes = { result: PropTypes.object.isRequired };

// Whether the Prompts model list knows this model, looked up the way the
// handoff (playground/PlaygroundHandoff.jsx) does: search the list for the
// id after its first "/", then match it. When it does not, the handoff
// carries the prompt but not the model; the button's label and tooltip say
// so.
function usePromptsModel(model) {
  return useQuery({
    queryKey: ["agentcc-prompts-model", model],
    queryFn: async () => {
      const slash = model.indexOf("/");
      const search = slash < 0 ? model : model.slice(slash + 1);
      const { data } = await axios.get(endpoints.develop.modelList, {
        params: { page: 1, search },
      });
      return workbenchModelFor(model, data?.results || []) || false;
    },
    enabled: Boolean(model),
    staleTime: 300000,
    meta: { errorHandled: true },
  });
}

const isCancel = (error) =>
  error?.name === "CanceledError" || error?.code === "ERR_CANCELED";

// One quick request to this model through the gateway's test endpoint (the
// one the Guardrails test uses): a prompt, an optional system prompt and
// sampling parameters, the answer streamed by default. It is a real request,
// billed and logged like any other. Anything more goes to the Prompts
// playground, the one full playground.
export default function ModelTryIt({ row, gatewayId }) {
  const [prompt, setPrompt] = useState(DEFAULT_PROMPT);
  const [system, setSystem] = useState("");
  const [params, setParams] = useState({});
  const [stream, setStream] = useState(true);
  const [result, setResult] = useState(null);
  const [failure, setFailure] = useState(null);
  const [running, setRunning] = useState(false);
  const abort = useRef(null);
  // A test request runs through the gateway's admin path
  // (test-playground, test-playground-stream): Owner or Admin only, as the
  // backend's is_gateway_admin decides.
  const { canEdit } = useGatewayAdmin();
  const target = callTarget(row);
  const promptsModel = usePromptsModel(
    target && row.type === "chat" ? target.model : "",
  );

  // Leaving the page stops a stream; the server then revokes its key.
  useEffect(() => () => abort.current?.abort(), []);

  if (!target) {
    return (
      <GatewayEmptyState
        icon="catalog"
        title="This model cannot be tried here"
        description={row.accessReason}
      />
    );
  }
  if (target.managed && !row.managedOn) {
    return (
      <GatewayEmptyState
        icon="catalog"
        title="Future AGI credits are off"
        description="Future AGI credits are off for your organization."
      />
    );
  }
  if (row.type !== "chat") {
    return (
      <GatewayEmptyState
        icon="catalog"
        title="Try it runs chat models"
        description="Use the API tab for a ready-to-run request to this model."
      />
    );
  }

  const invalid = Object.keys(paramErrors(params, row)).length > 0;

  const submit = async () => {
    const body = gatewayTestBody({
      prompt,
      model: target.model,
      systemPrompt: system,
      params,
    });
    const controller = new AbortController();
    abort.current = controller;
    setRunning(true);
    setFailure(null);
    setResult(null);
    try {
      if (stream) {
        try {
          const events = await streamGatewayTest(gatewayId, body, {
            signal: controller.signal,
            onEvents: (read) => setResult(readStreamResult(read)),
          });
          setResult(readStreamResult(events, { finished: true }));
        } catch (error) {
          // A server older than the streamed Try it has no such endpoint:
          // ask once for the answer in one piece, and say so.
          if (error?.statusCode !== 404 || controller.signal.aborted) {
            throw error;
          }
          setResult({
            ...readTryResult(await sendGatewayTest(gatewayId, body)),
            unstreamed: true,
          });
        }
      } else {
        setResult(readTryResult(await sendGatewayTest(gatewayId, body)));
      }
    } catch (error) {
      if (isCancel(error) || controller.signal.aborted) {
        setResult((shown) =>
          shown ? { ...shown, streaming: false, stopped: true } : null,
        );
      } else {
        setFailure(error);
      }
    } finally {
      abort.current = null;
      setRunning(false);
    }
  };

  // Prompts can run this model only when its model list has it: then Open
  // in Prompts carries the model and the prompt. A model it does not list
  // gets no handoff (a Prompts draft would open with no model).
  const listed = Boolean(promptsModel.data);

  return (
    <Section
      title="Try it"
      note={`One request to ${target.model} through this gateway, ${target.managed ? "billed to your Future AGI credits" : "billed by your provider"} and logged like any other.`}
    >
      <TextField
        label="System prompt (optional)"
        value={system}
        onChange={(e) => setSystem(e.target.value)}
        multiline
        minRows={1}
        size="small"
        fullWidth
      />
      <TextField
        label="Prompt"
        value={prompt}
        onChange={(e) => setPrompt(e.target.value)}
        multiline
        minRows={3}
        size="small"
        fullWidth
      />
      <Box
        sx={{
          display: "flex",
          gap: 1.5,
          flexWrap: "wrap",
          alignItems: "flex-start",
        }}
      >
        <TryParameters row={row} values={params} onChange={setParams} />
        <FormControlLabel
          sx={{ ml: 0, mt: 0.5 }}
          control={
            <Switch
              size="small"
              checked={stream}
              onChange={(e) => setStream(e.target.checked)}
            />
          }
          label={<Typography typography="s2">Stream the answer</Typography>}
        />
      </Box>
      <Box
        sx={{ display: "flex", gap: 1, flexWrap: "wrap", alignItems: "center" }}
      >
        {running && stream ? (
          <Button
            variant="outlined"
            size="small"
            onClick={() => abort.current?.abort()}
          >
            Stop
          </Button>
        ) : (
          <GatewayAdminOnly allowed={canEdit}>
            <LoadingButton
              variant="contained"
              color="primary"
              size="small"
              loading={running}
              disabled={!canEdit || !prompt.trim() || invalid}
              onClick={submit}
            >
              Run
            </LoadingButton>
          </GatewayAdminOnly>
        )}
        {listed && (
          <Button
            variant="outlined"
            size="small"
            component={RouterLink}
            href={promptsHandoffPath({
              model: target.model,
              prompt: prompt.trim(),
              system: system.trim(),
            })}
          >
            Open in Prompts
          </Button>
        )}
      </Box>
      {!canEdit && (
        <Typography typography="s2" color="text.secondary" component="p">
          Owner or Admin only: only an organization Owner or Admin can run a
          test request through the gateway.
        </Typography>
      )}
      {failure && (
        <Alert severity="error">
          {failure?.message || failure?.result || "The request failed."}
        </Alert>
      )}
      {result && <Result result={result} />}
    </Section>
  );
}

ModelTryIt.propTypes = {
  row: PropTypes.object.isRequired,
  gatewayId: PropTypes.string,
};
