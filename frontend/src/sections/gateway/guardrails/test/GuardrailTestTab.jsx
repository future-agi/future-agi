import React, { useState } from "react";
import PropTypes from "prop-types";
import Box from "@mui/material/Box";
import Button from "@mui/material/Button";
import Chip from "@mui/material/Chip";
import CircularProgress from "@mui/material/CircularProgress";
import TextField from "@mui/material/TextField";
import Typography from "@mui/material/Typography";
import { useMutation } from "@tanstack/react-query";

import { getSafeActionErrorMessage } from "src/utils/errorUtils";

import {
  GatewayEmptyState,
  GatewayGrid,
  GatewayStatusChip,
} from "../../components/frame";
import GatewayAdminOnly from "../../components/GatewayAdminOnly";
import WhyDisabled from "../../components/WhyDisabled";
import { useGatewayAdmin } from "../../hooks/useGatewayAdmin";
import { GUARDRAILS_OFF_REASON } from "../../utils/guardrailPipeline";
import { useGuardrailsOff } from "../hooks/useGuardrailsOff";
import {
  gatewayTestBody,
  sendGatewayTest,
} from "../../components/gateway-test-request";
import { EXAMPLES } from "./examples";
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

/**
 * Send one prompt through this gateway with a temporary key (Django creates
 * and revokes it) and read what the guardrails did from the gateway's answer.
 * The test runs through the gateway's admin path: only an Owner or Admin may
 * run one (useGatewayAdmin).
 */
export default function GuardrailTestTab({ gatewayId }) {
  const { canEdit } = useGatewayAdmin();
  // A gateway with guardrails off runs no check, so there is nothing to test.
  const off = useGuardrailsOff(gatewayId);
  const [prompt, setPrompt] = useState("");
  const [system, setSystem] = useState("");
  const [model, setModel] = useState("");
  const [history, setHistory] = useState([]);

  const test = useMutation({
    mutationFn: async (body) =>
      interpretTestResult(await sendGatewayTest(gatewayId, body)),
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
    meta: { errorHandled: true },
  });

  const run = () =>
    test.mutate(gatewayTestBody({ prompt, model, systemPrompt: system }));

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
            if (prompt.trim() && !test.isPending && canEdit && !off) run();
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
          <TextField
            label="Model"
            size="small"
            fullWidth
            value={model}
            onChange={(event) => setModel(event.target.value)}
            placeholder="e.g. gpt-4o-mini"
            helperText="Leave blank to use the first model of your first provider."
          />
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
                  disabled={
                    !prompt.trim() ||
                    test.isPending ||
                    !gatewayId ||
                    !canEdit ||
                    off
                  }
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
          {test.isError && (
            <Box
              role="alert"
              sx={{ display: "flex", flexDirection: "column", gap: 0.5 }}
            >
              <GatewayStatusChip tone="error" label="Not sent" />
              <Typography typography="s1" color="text.primary">
                {getSafeActionErrorMessage(
                  test.error,
                  "The test request could not be sent.",
                )}
              </Typography>
            </Box>
          )}
          {test.isSuccess && <TestResult result={test.data} />}
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
};
