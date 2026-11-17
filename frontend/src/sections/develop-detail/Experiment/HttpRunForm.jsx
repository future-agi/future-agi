import React, { useEffect, useMemo, useRef, useState } from "react";
import PropTypes from "prop-types";
import { Box, Stack, Typography, useTheme } from "@mui/material";
import {
  defaultBodyTemplate,
  unknownColumns,
} from "src/sections/common/RunSources/endpoint/endpointModel";
import { inputColumnFor } from "src/sections/common/RunSources/runOnRows";
import SecretHeaders from "src/sections/common/RunSources/endpoint/SecretHeaders";
import {
  EndpointTestPanel,
  RequestPreview,
  ResponseTransform,
} from "src/sections/common/RunSources/endpoint/httpCardParts";
import Iconify from "src/components/iconify";
import FormTextFieldV2 from "src/components/FormTextField/FormTextFieldV2";
import { FormSearchSelectFieldControl } from "src/components/FromSearchSelectField";
import RequestBody from "../AddColumn/AddColumnApiCall/RequestBody";
import RunCardShell from "./RunCardShell";
import { useExperimentForm } from "./ExperimentFormContext";
import { useCardValue } from "./cardWatch-g2";
import {
  getBodyNote,
  httpMethodSwitch,
  isGetMethod,
  sentTemplateOf,
} from "./endpointCards-g2";
import { variablesInTemplate } from "./variableMapping";
import { httpUrlHint } from "./stepIssues";

const METHODS = ["POST", "PUT", "GET"].map((m) => ({ label: m, value: m }));
// The stock body a new HTTP card starts with (experimentFormValues).
const STOCK_BODY = defaultBodyTemplate("customer_message");

/**
 * HTTP endpoint run (PRODUCT-SPEC §3.8, critique N10, basic): Future AGI
 * calls the endpoint once per example. The body template takes `{{column}}`
 * variables (the dataset API-call column's editor); the output path picks
 * the reply out of the JSON response. options-truth L1 G5: headers by
 * secret and audio live in the card (and the payload), and `Test on one
 * row` calls the endpoint with the sample row. L5 G5: headers use the one
 * workspace-secret picker (SecretHeaders); no streaming or audio-out.
 * L7c g2 (knob:http:method): a GET sends no body, so the card shows no body
 * editor, keeps no body and maps only the URL's `{{variables}}`.
 */
const HttpRunForm = ({
  control,
  setValue,
  getValues,
  fieldPrefix,
  letters = [],
  onRemove,
  showErrors = false,
  error,
  withTools = true,
}) => {
  const {
    mappingColumns,
    jsonSchemas,
    datasetId,
    sampleRow,
    rows,
    setSampleRowId,
    sampleRowLabel,
    exampleCount,
  } = useExperimentForm();
  const http = useCardValue(control, fieldPrefix, "http") ?? {};
  const headers = http.headers ?? [];
  const setHttp = (patch) =>
    setValue(
      `${fieldPrefix}.http`,
      { ...http, ...patch },
      { shouldDirty: true },
    );
  const url = useCardValue(control, fieldPrefix, "http.url");
  const body = useCardValue(control, fieldPrefix, "http.bodyTemplate");
  const method = useCardValue(control, fieldPrefix, "http.method");
  const isGet = isGetMethod(method);
  // CR#32: validate on blur (and after a Next attempt), not only on Next.
  const [urlTouched, setUrlTouched] = useState(false);
  const isDark = useTheme().palette.mode === "dark";
  const urlHint = urlTouched || showErrors ? httpUrlHint(url) : null;
  const columns = useMemo(
    () =>
      (mappingColumns ?? []).map((c) => ({ headerName: c.name, field: c.id })),
    [mappingColumns],
  );

  // N6: the default body reads the dataset's input column (a task suite's
  // `instruction`, the golden set's `customer_message`), not a fixed name.
  const inputColumn =
    inputColumnFor("input", mappingColumns ?? [])?.column ?? null;
  const seeded = useRef(false);
  useEffect(() => {
    if (seeded.current || !inputColumn || !(mappingColumns ?? []).length)
      return;
    seeded.current = true;
    const now = getValues(`${fieldPrefix}.http.bodyTemplate`);
    const want = defaultBodyTemplate(inputColumn);
    if (now === STOCK_BODY && want !== STOCK_BODY)
      setValue(`${fieldPrefix}.http.bodyTemplate`, want, {
        shouldDirty: false,
      });
  }, [inputColumn, mappingColumns, fieldPrefix, getValues, setValue]);
  // A variable mapped to a column on the Data step is known too.
  const mapping = useCardValue(control, fieldPrefix, "variableMapping") ?? {};
  const unknown = unknownColumns(
    sentTemplateOf({ method, url, body }),
    (mappingColumns ?? []).map((c) => c.name),
  ).filter((v) => !mapping?.[v]);

  // L7c g2 (knob:http:method): to GET, the body and the mapping only it
  // used are put aside (a GET never sends them); back to POST / PUT, an
  // empty body gets them back.
  const aside = useRef(null);
  const wasGet = useRef(isGet);
  useEffect(() => {
    const fromGet = wasGet.current && !isGet;
    wasGet.current = isGet;
    if (!isGet && !fromGet) return;
    const next = httpMethodSwitch({
      toGet: isGet,
      url: getValues(`${fieldPrefix}.http.url`),
      body: getValues(`${fieldPrefix}.http.bodyTemplate`),
      mapping: getValues(`${fieldPrefix}.variableMapping`) ?? {},
      aside: aside.current,
      stockBody: defaultBodyTemplate(inputColumn ?? "customer_message"),
    });
    if (!next) return;
    aside.current = next.aside;
    setValue(`${fieldPrefix}.http.bodyTemplate`, next.body, {
      shouldDirty: true,
    });
    setValue(`${fieldPrefix}.variableMapping`, next.mapping, {
      shouldDirty: true,
    });
    // inputColumn only seeds the stock body; a method change drives this.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [isGet, fieldPrefix, getValues, setValue]);

  // The sent {{vars}} are this run's variables (mapped on the Data step):
  // the URL's and, unless it is a GET, the body's.
  useEffect(() => {
    const vars = variablesInTemplate(sentTemplateOf({ method, url, body }));
    const current = getValues(`${fieldPrefix}.variables`) ?? [];
    if (vars.join("|") !== current.join("|"))
      setValue(`${fieldPrefix}.variables`, vars);
  }, [method, url, body, fieldPrefix, getValues, setValue]);

  return (
    <RunCardShell
      icon={
        <Iconify icon="mdi:api" width={16} sx={{ color: "text.secondary" }} />
      }
      letters={letters}
      title="HTTP endpoint"
      typeLabel="Called once per example"
      onRemove={onRemove}
      error={showErrors ? error : null}
    >
      <Box sx={{ display: "flex", gap: 1 }}>
        <Box sx={{ width: 110, flexShrink: 0 }}>
          {/* CR#31: a required field, so no clear button. */}
          <FormSearchSelectFieldControl
            fullWidth
            size="small"
            label="Method"
            control={control}
            fieldName={`${fieldPrefix}.http.method`}
            options={METHODS}
            showClear={false}
            sx={{ bgcolor: "background.paper" }}
          />
        </Box>
        <FormTextFieldV2
          control={control}
          fieldName={`${fieldPrefix}.http.url`}
          label="Endpoint URL"
          placeholder="https://api.example.com/v1/reply"
          size="small"
          fullWidth
          required
          onBlur={() => setUrlTouched(true)}
          error={urlHint?.level === "error"}
          helperText={urlHint?.text}
          sx={{
            bgcolor: "background.paper",
            ...(urlHint?.level === "warning" && {
              "& .MuiFormHelperText-root": {
                color: isDark ? "orange.400" : "orange.700",
              },
            }),
          }}
        />
      </Box>
      {isGet ? (
        <Typography
          typography="s2"
          color="text.secondary"
          data-testid="http-get-no-body"
        >
          {getBodyNote(inputColumn)}
        </Typography>
      ) : (
        <RequestBody
          control={control}
          contentFieldName={`${fieldPrefix}.http.bodyTemplate`}
          allColumns={columns}
          jsonSchemas={jsonSchemas}
          label="Request body · type {{ to insert a column"
          placeholder={defaultBodyTemplate(inputColumn ?? "input")}
          sx={{ minHeight: "96px", backgroundColor: "transparent" }}
        />
      )}
      {unknown.length ? (
        <Typography
          typography="s2"
          color="warning.dark"
          role="alert"
          data-testid="http-unknown-columns"
        >
          {`${unknown.map((v) => `{{${v}}}`).join(", ")} ${unknown.length === 1 ? "is not a column" : "are not columns"} of this dataset${inputColumn ? ` · its input column is {{${inputColumn}}}` : ""}`}
        </Typography>
      ) : null}
      <Box sx={{ display: "flex", gap: 1, alignItems: "center" }}>
        <Box sx={{ width: 220 }}>
          <FormTextFieldV2
            control={control}
            fieldName={`${fieldPrefix}.http.outputPath`}
            label="Output path"
            placeholder="$.reply"
            size="small"
            fullWidth
            required
            sx={{ bgcolor: "background.paper" }}
          />
        </Box>
        <Typography typography="s3" color="text.secondary">
          JSONPath of the reply in the response. Its value is the run output the
          evaluations score.
        </Typography>
      </Box>
      <ResponseTransform
        http={http}
        onChange={(transform) => setHttp({ transform })}
      />
      {withTools ? (
        <Stack spacing={0.75} data-testid="http-extras">
          <Typography typography="s2" fontWeight="fontWeightMedium">
            Headers
          </Typography>
          <SecretHeaders
            headers={headers}
            onChange={(next) => setHttp({ headers: next })}
            testIdPrefix="http-header"
          />
          <RequestPreview
            http={http}
            mapping={mapping}
            columns={mappingColumns ?? []}
            rows={rows ?? []}
            row={sampleRow}
            onRow={setSampleRowId}
            total={Number(exampleCount) || undefined}
          />
          <EndpointTestPanel
            http={http}
            mapping={mapping}
            columns={mappingColumns ?? []}
            row={sampleRow}
            rowLabel={sampleRowLabel}
            datasetId={datasetId}
            onPickPath={(path) =>
              setValue(`${fieldPrefix}.http.outputPath`, path, {
                shouldDirty: true,
              })
            }
          />
        </Stack>
      ) : null}
    </RunCardShell>
  );
};

HttpRunForm.propTypes = {
  control: PropTypes.object.isRequired,
  setValue: PropTypes.func.isRequired,
  getValues: PropTypes.func.isRequired,
  fieldPrefix: PropTypes.string.isRequired,
  letters: PropTypes.arrayOf(PropTypes.string),
  onRemove: PropTypes.func,
  showErrors: PropTypes.bool,
  error: PropTypes.string,
  /** Headers, request preview and Test on one row (off in EndpointConfig). */
  withTools: PropTypes.bool,
};

export default HttpRunForm;
