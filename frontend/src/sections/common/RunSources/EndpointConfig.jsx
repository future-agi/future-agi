// Level 3 · section 1 for an HTTP endpoint (R4 lane Rb MUST 3). Slot
// created by lane Ra (frozen props, I1b). Reuses R3's HttpRunForm (URL,
// method, body template, response path, localhost warning) on its own form,
// adds headers by secret reference and `Test on one row` (your browser calls
// the URL with row 1 filled in). W6 B1: no Audio section: only a text
// request body is built (no streaming, audio out or multipart audio in). options-truth L1 G5: headers,
// audio and streaming live in the form (`endpoint.http.*`), not local state,
// and the test calls a non-local host through Future AGI's endpoint test.
import React, { useEffect, useMemo, useRef, useState } from "react";
import PropTypes from "prop-types";
import { useForm, useWatch } from "react-hook-form";
import {
  Box,
  Button,
  CircularProgress,
  Stack,
  Typography,
} from "@mui/material";
import Iconify from "src/components/iconify";
import CustomTooltip from "src/components/tooltip";
import { fmtMs } from "src/sections/common/format/fmt";
import { Section } from "src/sections/common/ux/oneview";
import HttpRunForm from "src/sections/develop-detail/Experiment/HttpRunForm";
import { useExperimentForm } from "src/sections/develop-detail/Experiment/ExperimentFormContext";
import ScriptBlock from "./ScriptBlock";
import { testEndpoint } from "./endpoint/testEndpoint";
import { inputColumnFor } from "./runOnRows";
import { rowLabelOf } from "src/sections/common/Runs/runNumbers";
import {
  DEFAULT_OUTPUT_PATH,
  OFFERED_AUDIO_MODES,
  defaultBodyTemplate,
  missingVariables,
  requestSummary,
  rowValues,
} from "./endpoint/endpointModel";
import SecretHeaders from "./endpoint/SecretHeaders";
import StandInTag from "./endpoint/StandInTag";

const PREFIX = "endpoint";

function TestResult({ result }) {
  const ref = useRef(null);
  useEffect(() => {
    if (result && !result.busy)
      ref.current?.scrollIntoView({ block: "nearest", behavior: "smooth" });
  }, [result]);
  if (!result) return null;
  if (result.busy)
    return (
      <Stack direction="row" alignItems="center" gap={1}>
        <CircularProgress size={14} />
        <Typography typography="s2" color="text.secondary">
          {`Calling ${result.url}…`}
        </Typography>
      </Stack>
    );
  const ok =
    result.status >= 200 && result.status < 300 && result.output !== undefined;
  return (
    <Box
      ref={ref}
      data-testid="endpoint-test-result"
      sx={{
        border: "1px solid",
        borderColor: ok ? "divider" : "error.main",
        borderRadius: 1,
        p: 1.25,
      }}
    >
      <Stack
        direction="row"
        alignItems="center"
        gap={1}
        sx={{ mb: 0.75 }}
        flexWrap="wrap"
      >
        <Iconify
          icon={ok ? "mdi:check-circle" : "mdi:alert-circle-outline"}
          width={16}
          sx={{ color: ok ? "success.main" : "error.main" }}
        />
        <Typography typography="s2" fontWeight="fontWeightMedium">
          {result.error
            ? result.error
            : `${result.rowLabel} · HTTP ${result.status} · ${fmtMs(result.ms)}`}
        </Typography>
        {result.simulated ? (
          <StandInTag who={result.host} testId="endpoint-test-simulated" />
        ) : null}
        {!result.error && result.output === undefined ? (
          <Typography typography="s2" color="error.main">
            {`Nothing at ${result.path} in the response`}
          </Typography>
        ) : null}
      </Stack>
      {result.output !== undefined ? (
        <Typography
          typography="s2"
          sx={{ mb: 0.75, whiteSpace: "pre-wrap" }}
          data-testid="endpoint-test-output"
        >
          {typeof result.output === "string"
            ? result.output
            : JSON.stringify(result.output)}
        </Typography>
      ) : null}
      {result.body ? (
        <ScriptBlock
          text={result.body}
          language="json"
          dense
          copyTooltip="Copy response"
        />
      ) : null}
    </Box>
  );
}
TestResult.propTypes = { result: PropTypes.object };

/** The form's http fields as the run's C3 `endpoint` (pure). */
export function endpointValueOf(http = {}) {
  return {
    protocol: "http",
    url: String(http.url ?? "").trim(),
    method: String(http.method ?? "POST").toUpperCase(),
    headers: (http.headers ?? []).filter((h) => String(h?.name ?? "").trim()),
    body_template: http.bodyTemplate ?? "",
    output_path: String(http.outputPath ?? "").trim() || DEFAULT_OUTPUT_PATH,
    streaming: Boolean(http.streaming),
    audio: http.audio ?? "text",
  };
}

export default function EndpointConfig({
  entry,
  dataset,
  compact,
  value,
  onChange,
}) {
  const ctx = useExperimentForm();
  const inputColumn =
    dataset?.inputColumn ??
    inputColumnFor("input", ctx?.mappingColumns ?? ctx?.columns ?? [])
      ?.column ??
    "input";
  // A draft that already has an endpoint (slot.endpoint) reopens with it.
  const prior = value?.endpoint ?? null;
  const form = useForm({
    defaultValues: {
      [PREFIX]: {
        http: {
          method: prior?.method ?? "POST",
          url: prior?.url ?? "",
          bodyTemplate:
            prior?.body_template ?? defaultBodyTemplate(inputColumn),
          outputPath: prior?.output_path ?? DEFAULT_OUTPUT_PATH,
          headers: prior?.headers?.length
            ? prior.headers
            : [{ name: "Authorization", secret: "" }],
          audio: OFFERED_AUDIO_MODES.some((m) => m.id === prior?.audio)
            ? prior.audio
            : "text",
          streaming: false,
        },
        variables: [],
      },
    },
  });
  const http =
    useWatch({ control: form.control, name: `${PREFIX}.http` }) ?? {};
  // The run's endpoint follows the form (C3): Remote endpoint runs call it.
  const onChangeRef = useRef(onChange);
  onChangeRef.current = onChange;
  const endpointKey = JSON.stringify(endpointValueOf(http));
  useEffect(() => {
    if (compact) return;
    onChangeRef.current?.({ endpoint: JSON.parse(endpointKey) });
  }, [endpointKey, compact]);
  // The input column arrives with the dataset: follow it until the user edits.
  const lastInput = useRef(inputColumn);
  useEffect(() => {
    if (lastInput.current === inputColumn) return;
    const body = form.getValues(`${PREFIX}.http.bodyTemplate`);
    if (body === defaultBodyTemplate(lastInput.current))
      form.setValue(
        `${PREFIX}.http.bodyTemplate`,
        defaultBodyTemplate(inputColumn),
      );
    lastInput.current = inputColumn;
  }, [inputColumn, form]);
  const headers = http.headers ?? [];
  const setHeaders = (v) =>
    form.setValue(`${PREFIX}.http.headers`, v, { shouldDirty: true });
  const [result, setResult] = useState(null);
  const sample = ctx?.sampleRow ?? ctx?.rows?.[0] ?? null;
  const values = useMemo(
    () => rowValues(sample, ctx?.mappingColumns ?? ctx?.columns ?? []),
    [sample, ctx],
  );
  const missing = missingVariables(http.bodyTemplate, values);

  const test = async () => {
    const url = String(http.url ?? "").trim();
    if (!url) return;
    const rowLabel = Number.isFinite(sample?.order)
      ? rowLabelOf(
          { n: sample.order + 1 },
          dataset?.environment ? "task_suite" : "rows",
        )
      : ctx?.sampleRowLabel || "Row 1";
    setResult({ busy: true, url });
    // The app's axios rejects with the response body spread (`e.result`):
    // a refused host (L7c g2) shows its reason only.
    const r = await testEndpoint({
      url,
      method: http.method,
      headers,
      bodyTemplate: http.bodyTemplate,
      outputPath: http.outputPath,
      values,
      datasetId: ctx?.datasetId ?? null,
      rowId: sample?.row_id ?? null,
    }).catch((e) => ({
      error:
        e?.result ?? e?.response?.data?.result ?? e?.message ?? "Test failed",
    }));
    setResult(r?.error && !r.request ? { error: r.error } : { ...r, rowLabel });
  };

  if (compact)
    return (
      <Typography typography="s2" color="text.secondary">
        {requestSummary({
          method: http.method,
          headers,
          template: http.bodyTemplate,
          outputPath: http.outputPath,
        })}
      </Typography>
    );

  return (
    <Stack spacing={1} data-testid="endpoint-config" data-entry={entry?.id}>
      <HttpRunForm
        control={form.control}
        setValue={form.setValue}
        getValues={form.getValues}
        fieldPrefix={PREFIX}
        withTools={false}
      />
      <Stack direction="row" alignItems="center" gap={1} flexWrap="wrap">
        <CustomTooltip
          show
          title={
            http.url
              ? "Your browser calls the URL once with row 1 filled in"
              : "Enter the endpoint URL first"
          }
          size="small"
          placement="top"
        >
          <span>
            <Button
              size="small"
              variant="outlined"
              disabled={!String(http.url ?? "").trim() || result?.busy}
              onClick={test}
              // Keep focus in the URL field: its blur shows R3's localhost
              // hint, which would move this button under the pointer.
              onMouseDown={(e) => e.preventDefault()}
              startIcon={<Iconify icon="mdi:play-outline" width={16} />}
              data-testid="endpoint-test"
              sx={{ typography: "s2" }}
            >
              Test on one row
            </Button>
          </span>
        </CustomTooltip>
        <Typography typography="s3" color="text.secondary">
          {missing.length
            ? `Row 1 has no ${missing.join(", ")}: those stay as written`
            : "From your browser, so a localhost URL works for this test"}
        </Typography>
      </Stack>
      <TestResult result={result} />
      <Section
        id="endpoint-headers"
        surface="newExperiment"
        scopeId="http"
        title="Headers"
        defaultOpen={false}
        summary={`${requestSummary({ method: http.method, headers: headers.filter((h) => h.secret), template: http.bodyTemplate, outputPath: http.outputPath })} · Edit`}
      >
        <Stack
          spacing={0.75}
          sx={{ pl: 1, pb: 1 }}
          data-testid="endpoint-headers"
        >
          <SecretHeaders headers={headers} onChange={setHeaders} />
          <Typography typography="s3" color="text.secondary">
            Values stay in workspace secrets; this page never holds them, so a
            browser test sends only the other headers.
          </Typography>
        </Stack>
      </Section>
    </Stack>
  );
}

EndpointConfig.propTypes = {
  entry: PropTypes.object,
  mode: PropTypes.string,
  value: PropTypes.object,
  onChange: PropTypes.func,
  dataset: PropTypes.object,
  ids: PropTypes.shape({
    datasetId: PropTypes.string,
    runLabel: PropTypes.string,
    token: PropTypes.string,
  }),
  onEstimate: PropTypes.func,
  onTryResult: PropTypes.func,
  /** Additive (Ra): the Rerun dialog renders the recipe compactly. */
  compact: PropTypes.bool,
};
