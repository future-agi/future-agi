import React, { useEffect, useMemo, useRef, useState } from "react";
import PropTypes from "prop-types";
import Alert from "@mui/material/Alert";
import Box from "@mui/material/Box";
import Button from "@mui/material/Button";
import Chip from "@mui/material/Chip";
import CircularProgress from "@mui/material/CircularProgress";
import Link from "@mui/material/Link";
import MenuItem from "@mui/material/MenuItem";
import TextField from "@mui/material/TextField";
import Typography from "@mui/material/Typography";
import { useMutation } from "@tanstack/react-query";
import { useLocation, useNavigate } from "react-router-dom";

import { RouterLink } from "src/routes/components";
import { paths } from "src/routes/paths";

import {
  GatewayEmptyState,
  GatewayGrid,
  GatewayStatusChip,
} from "../../components/frame";
import GatewayAdminOnly from "../../components/GatewayAdminOnly";
import ManagedRefusalAlert from "../../components/ManagedRefusalAlert";
import { readManagedFailure } from "../../utils/managedRefusals";
import WhyDisabled from "../../components/WhyDisabled";
import { useGatewayAdmin } from "../../hooks/useGatewayAdmin";
import { GUARDRAILS_OFF_REASON } from "../../utils/guardrailPipeline";
import { useGuardrailsOff } from "../hooks/useGuardrailsOff";
import {
  gatewayTestBody,
  sendGatewayTest,
  testsUnavailable,
} from "../../components/gateway-test-request";
import { requestFailureFacts } from "../../components/requestDiagnostics";
import {
  providerReadState,
  useModelProviders,
  useModelReadScope,
} from "../../models/hooks/useCatalogRows";
import { orgModelPageId } from "../../models/orgServed";
import { ruleLabel } from "../catalog/rule-model";
import { EXAMPLES, exampleForCheck } from "./examples";
import { OUTCOMES, interpretTestResult } from "./interpret-result";
import TestResult from "./TestResult";

const HISTORY_COLUMNS = [
  {
    field: "time",
    headerName: "Time",
    valueFormatter: ({ value }) => value.toLocaleTimeString(),
    minWidth: 100,
    maxWidth: 120,
  },
  { field: "prompt", headerName: "Prompt", minWidth: 200, flex: 3 },
  { field: "model", headerName: "Model", minWidth: 140 },
  {
    field: "outcome",
    headerName: "Outcome",
    cellRenderer: ({ value, data }) => (
      <GatewayStatusChip
        tone={OUTCOMES[value].tone}
        label={OUTCOMES[value].short(data)}
        showIcon={false}
      />
    ),
    minWidth: 170,
  },
  {
    field: "statusCode",
    headerName: "Status",
    valueFormatter: ({ value }) => String(value),
    minWidth: 90,
    maxWidth: 110,
  },
];

const MAX_HISTORY = 20;

// What a test's answer shows, by what the checks do.
const WHAT_A_TEST_SHOWS =
  "A block answers HTTP 403 with error type guardrail_error. A check that warns or logs lets the request through: the answer carries x-agentcc-guardrail-triggered: true, with x-agentcc-guardrail-action (warn or log) and x-agentcc-guardrails-triggered (the check names) when the gateway sends them.";
// While the gateway runs no check: what a block and a warn or log do once
// guardrails are on, and no refusal now.
const WHAT_A_TEST_SHOWS_WHEN_OFF =
  "When guardrails are on in the gateway config, a block refuses the request with a guardrail error, and a check that warns or logs lets it through with headers naming the checks that matched.";

// This release runs no test requests: no prompt box, one place to look instead.
// After a refused Run the state replaces the form, so it is announced.
function TestsUnavailable({ announce }) {
  const navigate = useNavigate();
  return (
    <Box
      role={announce ? "alert" : undefined}
      sx={{
        display: "flex",
        flexDirection: "column",
        border: "1px solid",
        borderColor: "divider",
        borderRadius: 1,
        backgroundColor: "background.paper",
      }}
    >
      <GatewayEmptyState
        icon="guardrails"
        titleComponent="h2"
        title="Guardrail tests are not available in this release"
        description="Send a request through this gateway with your API key, then see what your rules did in Logs."
        actionLabel="Open Logs"
        onAction={() => navigate(paths.dashboard.gateway.guardrails.logs)}
      />
    </Box>
  );
}
TestsUnavailable.propTypes = { announce: PropTypes.bool };

/**
 * Send one prompt through this gateway with a temporary key (Django creates
 * and revokes it) and read what the guardrails did from the gateway's answer.
 * The test runs through the gateway's admin path: only an Owner or Admin may
 * run one (useGatewayAdmin). A release without delegated calls runs none
 * (`delegatedPlaygroundAvailable` false, or a refusal saying so).
 */
export default function GuardrailTestTab({
  gatewayId,
  delegatedPlaygroundAvailable,
}) {
  const { canEdit } = useGatewayAdmin();
  // A gateway with guardrails off runs no check, so there is nothing to test.
  const off = useGuardrailsOff(gatewayId);
  const location = useLocation();
  const navigate = useNavigate();
  const consumed = useRef(null);
  const dispatching = useRef(false);
  const scope = useModelReadScope(gatewayId);
  const providers = useModelProviders(gatewayId, scope);
  const access = providerReadState(providers);
  const providerList = providers.data?.providers;
  const models = useMemo(() => {
    const routes = new Map();
    (providerList || []).forEach((provider) => {
      const id = String(provider?.id || provider?.name || "");
      const listedModels = Array.isArray(provider?.models) ? provider.models : [];
      listedModels.forEach((listed) => {
        const route = orgModelPageId([provider], id, listed);
        if (route) {
          routes.set(route, { route, pushed: provider.pushed !== false });
        }
      });
    });
    return [...routes.values()];
  }, [providerList]);
  const [prompt, setPrompt] = useState("");
  const [system, setSystem] = useState("");
  const [selection, setSelection] = useState(null);
  const [trying, setTrying] = useState(null);
  const [history, setHistory] = useState([]);
  const selected =
    selection?.scope === scope
      ? models.find((item) => item.route === selection.route)
      : null;
  const model = selected?.route || "";

  useEffect(() => {
    const handoff = location.state?.guardrailTest;
    if (!handoff || consumed.current === location.key) return;
    consumed.current = location.key;
    const sample =
      handoff.gatewayId === gatewayId && exampleForCheck(handoff.checkName);
    if (sample) {
      setPrompt(sample.value);
      setTrying({ scope, checkName: sample.checkName });
    }
    // Remove only our one-use context, preserving other router state and URL.
    // Refetches and rerenders cannot reapply the sample over the user's edits.
    const state = { ...location.state };
    delete state.guardrailTest;
    navigate(`${location.pathname}${location.search}${location.hash}`, {
      replace: true,
      state: Object.keys(state).length ? state : null,
    });
  }, [location, navigate, gatewayId, scope]);

  const test = useMutation({
    mutationFn: async (body) => {
      const response = await sendGatewayTest(gatewayId, body);
      const result = interpretTestResult(response);
      return result.outcome === "failed"
        ? { ...result, failureFacts: requestFailureFacts(response) }
        : result;
    },
    retry: false,
    onSuccess: (result, body) =>
      setHistory((previous) =>
        [
          {
            id: `${Date.now()}-${previous.length}`,
            time: new Date(),
            prompt: body.prompt,
            model: result.model || body.model || "Default",
            outcome: result.outcome,
            statusCode: result.statusCode,
          },
          ...previous,
        ].slice(0, MAX_HISTORY),
      ),
    onSettled: () => {
      dispatching.current = false;
    },
    meta: { errorHandled: true },
  });

  const failure = test.isError
    ? requestFailureFacts(test.error)
    : test.data?.failureFacts;
  const unknown = failure?.outcome === "unknown";
  const responseRefusal = test.isError
    ? readManagedFailure(test.error)
    : test.data?.refusal;
  // Keep specific remedies, but the generic "refused" fallback is not evidence
  // of a definite outcome when the shared diagnostics say completion is unknown.
  const failureRefusal =
    unknown && responseRefusal?.category === "other" && !responseRefusal.action
      ? null
      : responseRefusal;
  const canRun = Boolean(
    gatewayId &&
    canEdit &&
    !off &&
    prompt.trim() &&
    selected?.pushed &&
    access === "ready" &&
    !test.isPending,
  );
  const run = () => {
    if (!canRun || dispatching.current) return;
    dispatching.current = true;
    test.mutate(gatewayTestBody({ prompt, model, systemPrompt: system }));
  };

  if (
    testsUnavailable(delegatedPlaygroundAvailable, failure) &&
    !test.isPending
  ) {
    return <TestsUnavailable announce={test.isError} />;
  }

  return (
    <Box sx={{ display: "flex", flexDirection: "column", gap: 2 }}>
      <Box
        sx={{
          display: "grid",
          gridTemplateColumns: {
            xs: "minmax(0, 1fr)",
            md: "minmax(0, 5fr) minmax(0, 6fr)",
          },
          gap: 2,
          alignItems: "start",
        }}
      >
        <Box
          component="form"
          aria-label="Test request"
          onSubmit={(event) => {
            event.preventDefault();
            run();
          }}
          sx={{
            display: "flex",
            flexDirection: "column",
            gap: 1.5,
            p: 2,
            border: "1px solid",
            borderColor: "divider",
            borderRadius: 1,
            backgroundColor: "background.paper",
          }}
        >
          {trying?.scope === scope && (
            <Alert severity="info">
              <Typography typography="s1">
                Try {ruleLabel(trying.checkName)}
              </Typography>
              This is an editable example, not proof of a match. Choose a model
              and click Run test when you are ready. Nothing runs automatically.
            </Alert>
          )}
          <Typography typography="s2" color="text.secondary">
            Sends a real request through this gateway with a temporary key. It
            is logged, and billed like any other request.
          </Typography>
          <Typography typography="s3" color="text.secondary">
            {off ? WHAT_A_TEST_SHOWS_WHEN_OFF : WHAT_A_TEST_SHOWS}
          </Typography>
          <Box sx={{ display: "flex", flexWrap: "wrap", gap: 0.75 }}>
            {EXAMPLES.map((example) => (
              <Chip
                key={example.label}
                size="small"
                variant="outlined"
                label={example.label}
                onClick={() => setPrompt(example.value)}
              />
            ))}
          </Box>
          <TextField
            label="Prompt"
            multiline
            minRows={4}
            fullWidth
            required
            value={prompt}
            onChange={(event) => setPrompt(event.target.value)}
          />
          <TextField
            label="System prompt"
            size="small"
            fullWidth
            value={system}
            onChange={(event) => setSystem(event.target.value)}
            placeholder="Optional"
          />
          {access === "error" ? (
            <Alert
              severity="warning"
              action={
                <Button
                  size="small"
                  color="inherit"
                  disabled={providers.isFetching}
                  onClick={() => providers.refetch()}
                >
                  Retry models
                </Button>
              }
            >
              Could not load configured models. Retry the provider read before
              running a test; previously loaded routes may be stale.
            </Alert>
          ) : access === "pending" ? (
            <Typography typography="s2" color="text.secondary" role="status">
              Loading configured models…
            </Typography>
          ) : models.length === 0 ? (
            <Alert severity="info">
              <Typography typography="s1">No models configured</Typography>
              Configure a provider model before testing. An Owner or Admin can
              update providers.{" "}
              <Link
                component={RouterLink}
                href={paths.dashboard.gateway.providers}
              >
                View providers
              </Link>
            </Alert>
          ) : null}
          <TextField
            select
            label="Model"
            size="small"
            fullWidth
            value={model}
            disabled={
              access !== "ready" || models.length === 0 || test.isPending
            }
            onChange={(event) =>
              setSelection({ scope, route: event.target.value })
            }
            SelectProps={{ displayEmpty: true }}
            InputLabelProps={{ shrink: true }}
            helperText="Choose a configured provider/model route. Configuration does not verify chat support or that a request will succeed."
          >
            <MenuItem value="" disabled>
              Choose a model
            </MenuItem>
            {models.map((item) => (
              <MenuItem
                key={item.route}
                value={item.route}
                disabled={!item.pushed}
              >
                {item.route}{item.pushed ? "" : " (Not synced)"}
              </MenuItem>
            ))}
          </TextField>
          <Box>
            <GatewayAdminOnly allowed={canEdit}>
              <WhyDisabled
                reason={canEdit && off ? GUARDRAILS_OFF_REASON : null}
              >
                <Button
                  type="submit"
                  variant="contained"
                  color="primary"
                  size="small"
                  disabled={!canRun}
                  startIcon={
                    test.isPending ? (
                      <CircularProgress size={14} color="inherit" />
                    ) : null
                  }
                >
                  {test.isPending ? "Running" : "Run test"}
                </Button>
              </WhyDisabled>
            </GatewayAdminOnly>
          </Box>
        </Box>

        <Box
          sx={{
            p: 2,
            border: "1px solid",
            borderColor: "divider",
            borderRadius: 1,
            backgroundColor: "background.paper",
            minHeight: { md: 320 },
            display: "flex",
            flexDirection: "column",
          }}
        >
          {(test.isError || unknown) && (
            <Box
              role={failureRefusal ? undefined : "alert"}
              sx={{ display: "flex", flexDirection: "column", gap: 1 }}
            >
              <GatewayStatusChip
                tone={unknown ? "warning" : "error"}
                label={unknown ? "Completion unknown" : "Request refused"}
              />
              {unknown && (
                <Typography typography="s1" color="text.primary">
                  The gateway may have received this request. Completion and
                  billing could not be confirmed. Check Logs before running
                  again; another test may be billed.
                </Typography>
              )}
              {failureRefusal ? (
                <ManagedRefusalAlert
                  refusal={failureRefusal}
                  status={failure.status}
                />
              ) : (
                <>
                  {failure.status && (
                    <Typography typography="s2" color="text.secondary">
                      HTTP {failure.status}
                    </Typography>
                  )}
                  <Typography
                    typography="s1"
                    color="text.primary"
                    sx={{ overflowWrap: "anywhere" }}
                  >
                    {failure.message}
                  </Typography>
                </>
              )}
              <Link
                component={RouterLink}
                href={
                  failure.requestId
                    ? `${paths.dashboard.gateway.logs}?request_id=${encodeURIComponent(failure.requestId)}`
                    : paths.dashboard.gateway.logs
                }
                typography="s2"
              >
                Open Logs
              </Link>
            </Box>
          )}
          {test.isSuccess && !unknown && <TestResult result={test.data} />}
          {test.isIdle && (
            <GatewayEmptyState
              icon="guardrails"
              title="Run a prompt to see what your rules do"
              description="The result shows whether a rule blocked it, matched and let it through, or nothing matched."
              sx={{ py: { xs: 4, sm: 6 } }}
            />
          )}
          {test.isPending && (
            <Typography typography="s2" color="text.secondary" role="status">
              Waiting for the gateway…
            </Typography>
          )}
        </Box>
      </Box>

      {history.length > 0 && (
        <Box
          component="section"
          aria-label="Recent tests"
          sx={{ display: "flex", flexDirection: "column", gap: 1 }}
        >
          <Typography typography="s1" fontWeight="fontWeightMedium">
            Recent tests
          </Typography>
          <GatewayGrid
            rowData={history}
            columnDefs={HISTORY_COLUMNS}
            getRowId={(params) => params.data.id}
            stateKey="gateway-guardrail-tests"
            height="auto"
            domLayout="autoHeight"
          />
        </Box>
      )}
    </Box>
  );
}

GuardrailTestTab.propTypes = {
  gatewayId: PropTypes.string,
  // The gateway read's delegated_playground_available; undefined: not said.
  delegatedPlaygroundAvailable: PropTypes.bool,
};
