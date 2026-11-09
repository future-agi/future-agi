// `Test on one row` for the HTTP card (W6 B1/B2): the button and what came
// back. The request is the run's own (requestModel), so the result is the
// run's try 1 on that row; it shows what was sent (secrets by reference, or
// the note that a browser left them out), the try's traceparent and whether
// the server echoed it, and the transform's output, tool calls and usage.
import React, { useState } from "react";
import { useQueryClient } from "@tanstack/react-query";
import PropTypes from "prop-types";
import { Box, Button, Chip, Link, Stack, Typography } from "@mui/material";
import Iconify from "src/components/iconify";
import { isLocalUrl } from "./endpointModel";
import StandInTag from "./StandInTag";
import { testEndpoint } from "./testEndpoint";
import { textPathsOf } from "./previewModel";
import { useSecretNamesState } from "./useSecretNamesState";

const line = { whiteSpace: "pre-wrap", wordBreak: "break-word" };

/**
 * L7c g2 (knob:http:output-path): the response the Test got, and the text
 * paths in it; picking one sets the card's output path.
 */
function ResponseBody({ test, testIdPrefix, onPickPath }) {
  const [open, setOpen] = useState(Boolean(test.error));
  let json = null;
  try {
    json = test.body ? JSON.parse(test.body) : null;
  } catch {
    json = null;
  }
  if (!test.body) return null;
  const paths = json ? textPathsOf(json) : [];
  return (
    <Stack spacing={0.5} sx={{ mt: 0.5 }}>
      <Stack direction="row" gap={0.75} alignItems="center" flexWrap="wrap">
        <Link
          component="button"
          type="button"
          typography="s3"
          underline="hover"
          onClick={() => setOpen((v) => !v)}
          data-testid={`${testIdPrefix}-test-body-toggle`}
        >
          {open ? "Hide the response" : "Show the response"}
        </Link>
        {onPickPath && paths.length ? (
          <>
            <Typography typography="s3" color="text.secondary">
              Output path:
            </Typography>
            {paths.map((p) => (
              <Chip
                key={p}
                size="small"
                variant={p === test.path ? "filled" : "outlined"}
                label={p}
                onClick={() => onPickPath(p)}
                data-testid={`${testIdPrefix}-test-path`}
                sx={{ typography: "s3", height: 20 }}
              />
            ))}
          </>
        ) : null}
      </Stack>
      {open ? (
        <Box
          component="pre"
          data-testid={`${testIdPrefix}-test-body`}
          sx={{
            m: 0,
            p: 0.75,
            maxHeight: 180,
            overflow: "auto",
            bgcolor: "background.paper",
            borderRadius: 0.5,
            typography: "s3",
            fontFamily: "monospace",
            ...line,
          }}
        >
          {test.body}
        </Box>
      ) : null}
    </Stack>
  );
}
ResponseBody.propTypes = {
  test: PropTypes.object.isRequired,
  testIdPrefix: PropTypes.string,
  onPickPath: PropTypes.func,
};

export function TestResultView({ test, testIdPrefix = "http", onPickPath }) {
  if (!test || test.busy) return null;
  // L7c g2: a refused request (a private host, a missing secret) shows its
  // refusal only: nothing was sent, so no request line, tag or traceparent.
  const req = test.request;
  return (
    <Box
      data-testid={`${testIdPrefix}-test-result`}
      sx={{ bgcolor: "background.neutral", borderRadius: 1, p: 1 }}
    >
      {req ? (
        <Stack spacing={0.25}>
          <Stack direction="row" gap={1} alignItems="center" flexWrap="wrap">
            <Typography typography="s3" color="text.secondary" sx={line}>
              {`${test.rowLabel ? `${test.rowLabel} · ` : ""}${req.method} ${req.url} · ${test.status ?? "—"} · ${Math.round(test.ms ?? 0)} ms`}
            </Typography>
            {test.simulated ? (
              <StandInTag
                who={test.host}
                testId={`${testIdPrefix}-test-simulated`}
              />
            ) : null}
          </Stack>
          <Typography
            typography="s3"
            color="text.secondary"
            sx={line}
            data-testid={`${testIdPrefix}-test-headers`}
          >
            {Object.entries(req.headers ?? {})
              .filter(([k]) => k !== "traceparent")
              .map(([k, v]) => `${k}: ${v}`)
              .join(" · ") || "No headers"}
          </Typography>
          {test.note ? (
            <Typography
              typography="s3"
              color="warning.dark"
              sx={line}
              data-testid={`${testIdPrefix}-test-secret-note`}
            >
              {test.note}
            </Typography>
          ) : null}
          {test.traceparent ? (
            <Typography
              typography="s3"
              color="text.secondary"
              sx={{ ...line, fontFamily: "monospace" }}
              data-testid={`${testIdPrefix}-test-traceparent`}
            >
              {`traceparent ${test.traceparent}${
                test.simulated
                  ? ""
                  : test.echoed === test.traceparent
                    ? " · your server echoed it"
                    : test.echoed
                      ? ` · your server echoed ${test.echoed}`
                      : " · your server did not echo it"
              }`}
            </Typography>
          ) : null}
        </Stack>
      ) : null}
      <Typography
        typography="s2"
        color={test.error ? "error.main" : "text.primary"}
        sx={{ mt: 0.5, ...line }}
        data-testid={`${testIdPrefix}-test-output`}
      >
        {test.error ??
          (typeof test.output === "string"
            ? test.output
            : JSON.stringify(test.output))}
      </Typography>
      <ResponseBody
        key={test.body ?? ""}
        test={test}
        testIdPrefix={testIdPrefix}
        onPickPath={onPickPath}
      />
      {test.tool_calls?.length ? (
        <Typography
          typography="s3"
          color="text.secondary"
          sx={line}
          data-testid={`${testIdPrefix}-test-tool-calls`}
        >
          {`Tool calls · ${test.tool_calls
            .map((c) => `${c.name}(${JSON.stringify(c.arguments ?? {})})`)
            .join(" · ")}`}
        </Typography>
      ) : null}
      {test.usage ? (
        <Typography
          typography="s3"
          color="text.secondary"
          sx={line}
          data-testid={`${testIdPrefix}-test-usage`}
        >
          {`Usage · ${Object.entries(test.usage)
            .map(([k, v]) => `${k} ${v}`)
            .join(" · ")}`}
        </Typography>
      ) : null}
    </Box>
  );
}
TestResultView.propTypes = {
  test: PropTypes.object,
  testIdPrefix: PropTypes.string,
  onPickPath: PropTypes.func,
};

export default function EndpointTestPanel({
  http,
  mapping,
  columns,
  row,
  rowLabel,
  datasetId,
  testIdPrefix = "http",
  onPickPath,
}) {
  const names = useSecretNamesState();
  const queryClient = useQueryClient();
  const [test, setTest] = useState(null);
  const url = String(http?.url ?? "").trim();
  const run = async () => {
    setTest({ busy: true });
    try {
      const r = await testEndpoint({
        url,
        method: http?.method,
        headers: http?.headers ?? [],
        bodyTemplate: http?.bodyTemplate,
        outputPath: http?.outputPath,
        transform: http?.transform ?? null,
        row,
        columns: columns ?? [],
        mapping: mapping ?? {},
        datasetId,
        rowId: row?.row_id ?? null,
        secretNames: names ?? undefined,
      });
      setTest({ ...r, rowLabel });
    } catch (e) {
      // The app's axios rejects with the response body spread (`e.result`).
      const error =
        e?.result ?? e?.response?.data?.result ?? e?.message ?? "Test failed";
      setTest({ error });
      // A secret deleted since the list loaded: refresh it so the picker
      // flags the name too.
      if (/No workspace secret named/.test(String(error)))
        queryClient.invalidateQueries({ queryKey: ["secrets", "r4-names"] });
    }
  };
  return (
    <Stack spacing={0.75}>
      <Stack direction="row" gap={1} alignItems="center">
        <Button
          size="small"
          variant="outlined"
          disabled={!url || test?.busy}
          onClick={run}
          startIcon={<Iconify icon="mdi:play-outline" width={14} />}
          data-testid={`${testIdPrefix}-test`}
          sx={{ typography: "s2" }}
        >
          Test on one row
        </Button>
        <Typography typography="s3" color="text.secondary">
          {isLocalUrl(url)
            ? `Your browser calls this URL with ${rowLabel || "the sample row"}; secret headers are left out`
            : `Future AGI sends ${rowLabel || "the sample row"}'s request, secret headers included`}
        </Typography>
      </Stack>
      <TestResultView
        test={test}
        testIdPrefix={testIdPrefix}
        onPickPath={onPickPath}
      />
    </Stack>
  );
}

EndpointTestPanel.propTypes = {
  http: PropTypes.object,
  mapping: PropTypes.object,
  columns: PropTypes.arrayOf(PropTypes.object),
  row: PropTypes.object,
  rowLabel: PropTypes.string,
  datasetId: PropTypes.string,
  testIdPrefix: PropTypes.string,
  /** Sets the card's output path from a path the Test response holds. */
  onPickPath: PropTypes.func,
};
