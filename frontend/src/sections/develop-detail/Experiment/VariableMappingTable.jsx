import React, { useEffect, useMemo, useRef, useState } from "react";
import PropTypes from "prop-types";
import { Box, Button, Link, Stack, Typography } from "@mui/material";
import { useWatch } from "react-hook-form";
import { useModelInputs } from "./modelInputs";
import { useParams } from "react-router-dom";
import Iconify from "src/components/iconify";
import CustomTooltip from "src/components/tooltip";
import RunLetterBadge from "./RunLetterBadge";
import { useExperimentForm } from "./ExperimentFormContext";
import { coverageInScope } from "./runInputs";
import { toneSx } from "./toneSx";
import { kindIcon } from "src/sections/common/Values/kindIcons";
import { dominantKind } from "src/sections/common/Values/inferSchema";
import { toLegacyMapping } from "src/sections/common/Values/faPath";
import { mediaKindOfString } from "src/sections/common/Values/sniff";
import { datasetGridHref } from "src/sections/common/Analysis/analysisLinks";
import { rowIdsFilter } from "src/sections/develop-detail/DataTab/DataGrid/dataGridView";
import VariableColumnSelect, {
  ConstantValueField,
} from "./VariableColumnSelect";
import { takeMappingFocus } from "./mappingFocus";
import { renameVariableText, sameNameMapping } from "./serverBlockCopy";
import {
  JSON_PATH_SEPARATOR,
  constantMapping,
  fixedMappingLabel,
  isConstantMapping,
  isEmptyMapping,
  leaveEmpty,
  leaveEmptyLabel,
  mappedValueOnRow,
  mappingCoverage,
  mappingChipLabel,
  mappingHowOf,
  meaningTooltip,
  mappingStatus,
  modalityWarnings,
  unmappedMessage,
} from "./variableMapping";

const PREVIEW_CHARS = 80;
const clip = (s) =>
  s && s.length > PREVIEW_CHARS ? `${s.slice(0, PREVIEW_CHARS)}…` : s;

const head = {
  typography: "s3",
  fontWeight: "fontWeightMedium",
  color: "text.secondary",
  px: 1,
  py: 0.75,
  borderBottom: "1px solid",
  borderColor: "divider",
  bgcolor: "background.neutral",
};
const cell = {
  px: 1,
  py: 0.5,
  borderBottom: "1px solid",
  borderColor: "divider",
  display: "flex",
  alignItems: "center",
  minWidth: 0,
};

/**
 * Variable mapping of one run (PRODUCT-SPEC §3.8 step 2): Variable ·
 * Dataset column (+ JSON paths) · Value on the sample row · status.
 */
// MUST 10: the modality a variable sends, from the column type or value.
const MODALITY = {
  image: "Image",
  images: "Image",
  audio: "Audio",
  document: "Document",
  video: "Video",
  json: "Chat",
  conversation: "Chat",
};
function modalityOf(column, isPath, preview) {
  if (!column) return null;
  if (isPath) return "Text";
  if (MODALITY[column.data_type]) {
    if (column.data_type === "json")
      return /conversation|messages|turns|transcript|chat/i.test(column.name)
        ? "Chat"
        : "JSON";
    return MODALITY[column.data_type];
  }
  const m = mediaKindOfString(preview);
  return m ? MODALITY[m] ?? "Text" : "Text";
}

const VariableMappingTable = ({
  control,
  setValue,
  fieldPrefix,
  title,
  letters = [],
  icon,
  showErrors = false,
  nameOnly = false,
  kind,
}) => {
  const {
    mappingColumns,
    jsonSchemas,
    sampleRow,
    sampleRowLabel,
    nextOrdinal,
    exampleCount,
  } = useExperimentForm();
  const variables =
    useWatch({ control, name: `${fieldPrefix}.variables` }) ?? [];
  const mapping =
    useWatch({ control, name: `${fieldPrefix}.variableMapping` }) ?? {};
  const status = mappingStatus(variables, mapping, mappingColumns);
  const allMapped = status.mapped === status.total;
  // `nameOnly`: the run fills {{name}} only from the column named `name`, so
  // any other choice blocks it; such a variable says what to do instead of
  // reading as mapped.
  const renames = nameOnly
    ? [...new Set(variables)].filter(
        (v) => !sameNameMapping(v, mapping?.[v], mappingColumns),
      )
    : [];
  const ready = allMapped && !renames.length;

  // L3 G7 W6: what each run's model reads (`capabilities.inputs` of the
  // model list); a mapped column of another modality warns on that run.
  const models = useWatch({ control, name: `${fieldPrefix}.model` });
  const runModels = (Array.isArray(models) ? models : []).filter(
    (m) => m?.value,
  );
  const modelInputs = useModelInputs(
    runModels.some((m) => !m?.capabilities?.inputs),
  );
  const warnings = modalityWarnings(
    letters.map((letter, i) => {
      const m = runModels[i];
      return {
        letter,
        model: m?.value ?? null,
        inputs: m?.capabilities?.inputs ?? modelInputs?.[m?.value] ?? [],
      };
    }),
    mapping,
    mappingColumns ?? [],
  );
  const warningOf = (v) => warnings.filter((w) => w.variable === v);

  // Every column, then ALL its typed paths (json-schema `paths[]`: type
  // icon + coverage), or its keys when the schema has no typed paths.
  const options = useMemo(() => {
    const out = [];
    (mappingColumns ?? []).forEach((c) => {
      out.push({
        value: c.id,
        label: c.name,
        role: c.role,
        kind: c.data_type,
      });
      const entry = jsonSchemas?.[c.id];
      if (entry?.paths?.length) {
        entry.paths
          .filter((p) => dominantKind(p.kinds) !== "null")
          .forEach((p) =>
            out.push({
              value: toLegacyMapping(c.id, p.rel),
              label: p.path,
              path: true,
              kind: dominantKind(p.kinds),
              coverage: p.coverage,
            }),
          );
      } else {
        (entry?.keys ?? []).forEach((path) =>
          out.push({
            value: `${c.id}${JSON_PATH_SEPARATOR}$.${path}`,
            label: `${c.name}.${path}`,
            path: true,
          }),
        );
      }
    });
    return out;
  }, [mappingColumns, jsonSchemas]);
  const { dataset: routeDataset } = useParams();
  const datasetId = mappingColumns?.[0]?.dataset_id ?? routeDataset;

  const setOne = (variable, value) =>
    setValue(
      `${fieldPrefix}.variableMapping`,
      { ...mapping, [variable]: value },
      { shouldDirty: true },
    );
  const setAllEmpty = (list) =>
    setValue(`${fieldPrefix}.variableMapping`, leaveEmpty(mapping, list), {
      shouldDirty: true,
    });
  // IT-R4 G1 P1: `Same value…` opens the row's text field focused.
  const [editingConstant, setEditingConstant] = useState(null);
  // `Go to Data` from Try on one row focuses that variable's picker.
  const gridRef = useRef(null);
  useEffect(() => {
    const v = variables.find((x) => takeMappingFocus(x));
    if (!v) return;
    const el = gridRef.current?.querySelector(
      `[data-testid="column-select-${CSS.escape(v)}"] [role="combobox"]`,
    );
    el?.scrollIntoView?.({ block: "center" });
    el?.focus?.();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [variables.join("|")]);

  return (
    <Box
      sx={{
        border: "1px solid",
        borderColor: showErrors && !ready ? "warning.main" : "divider",
        borderRadius: 0.5,
        overflow: "hidden",
        bgcolor: "background.paper",
      }}
    >
      <Box
        sx={{
          display: "flex",
          alignItems: "center",
          gap: 1,
          px: 1.25,
          py: 0.75,
        }}
      >
        {letters.map((l) => (
          <RunLetterBadge
            key={l}
            letter={l}
            ordinal={nextOrdinal ?? undefined}
          />
        ))}
        {icon}
        <Typography typography="s1" fontWeight="fontWeightMedium" noWrap>
          {title}
        </Typography>
        <Box sx={{ flex: 1 }} />
        <Box
          component="span"
          data-testid="mapping-chip"
          sx={{
            typography: "s3",
            px: 0.75,
            py: 0.125,
            borderRadius: 0.5,
            ...toneSx(ready ? "pass" : "warn"),
          }}
        >
          {renames.length ? "Rename needed" : mappingChipLabel(status)}
        </Box>
      </Box>
      {variables.length ? (
        <Box
          ref={gridRef}
          sx={{
            display: "grid",
            gridTemplateColumns: "176px 196px minmax(0, 1fr) 28px",
          }}
        >
          <Box sx={head}>Variable</Box>
          <Box sx={head}>Dataset column</Box>
          <Box sx={head}>Value on {sampleRowLabel || "the sample row"}</Box>
          <Box sx={head} />
          {variables.map((v) => {
            const value = mapping?.[v] ?? "";
            const ok = !status.unmapped.includes(v);
            const rename = renames.includes(v);
            const preview = ok ? mappedValueOnRow(sampleRow, value) : null;
            const column = ok
              ? (mappingColumns ?? []).find(
                  (c) => c.id === String(value).split(JSON_PATH_SEPARATOR)[0],
                )
              : null;
            const isPath = String(value).includes(JSON_PATH_SEPARATOR);
            const media =
              !isPath &&
              ["image", "images", "audio", "video", "document"].includes(
                column?.data_type,
              )
                ? column.data_type === "images"
                  ? "image"
                  : column.data_type
                : !isPath && mediaKindOfString(preview);
            // v2.1 (vars:coverage): a coverage line counts the rows in
            // scope; the schema counts the whole latest version, so it shows
            // only when those are the rows that run.
            const coverage = coverageInScope(
              ok ? mappingCoverage(value, jsonSchemas) : null,
              exampleCount,
            );
            const missingHref =
              coverage?.missingRowIds?.length && datasetId
                ? datasetGridHref(datasetId, [
                    rowIdsFilter(coverage.missingRowIds, {
                      label: `Missing ${column?.name ?? "path"}`,
                    }),
                  ])
                : null;
            return (
              <React.Fragment key={v}>
                <Box
                  sx={{
                    ...cell,
                    flexDirection: "column",
                    alignItems: "flex-start",
                    justifyContent: "center",
                  }}
                >
                  <Typography
                    noWrap
                    title={`{{${v}}}`}
                    sx={{
                      fontFamily: "monospace",
                      fontSize: 12,
                      color: "text.primary",
                      maxWidth: "100%",
                    }}
                  >
                    {`{{${v}}}`}
                  </Typography>
                  {(() => {
                    const how = ok
                      ? mappingHowOf(v, value, mappingColumns ?? [])
                      : null;
                    const modality = modalityOf(column, isPath, preview);
                    const unread = warningOf(v);
                    return (
                      <Stack
                        direction="row"
                        gap={0.5}
                        sx={{ mt: 0.25, minWidth: 0 }}
                      >
                        {modality ? (
                          <CustomTooltip
                            show
                            title={
                              unread.length
                                ? unread
                                    .map((w) => `#${w.letter}: ${w.text}`)
                                    .join(" · ")
                                : `Sends ${modality.toLowerCase()} to the run`
                            }
                            size="small"
                            placement="top"
                          >
                            <Typography
                              component="span"
                              typography="s3"
                              data-testid={`modality-${v}`}
                              sx={{
                                px: 0.5,
                                borderRadius: 0.5,
                                ...(unread.length
                                  ? toneSx("warn")
                                  : {
                                      bgcolor: "background.neutral",
                                      color: "text.secondary",
                                    }),
                              }}
                            >
                              {modality}
                            </Typography>
                          </CustomTooltip>
                        ) : null}
                        {how ? (
                          <CustomTooltip
                            show
                            title={meaningTooltip(v, column?.name)}
                            size="small"
                            placement="top"
                          >
                            <Typography
                              component="span"
                              typography="s3"
                              data-testid={`matched-by-meaning-${v}`}
                              sx={{
                                px: 0.5,
                                borderRadius: 0.5,
                                bgcolor: "purple.o10",
                                color: "primary.main",
                              }}
                            >
                              {how === "meaning"
                                ? "matched by meaning"
                                : "last customer message"}
                            </Typography>
                          </CustomTooltip>
                        ) : null}
                      </Stack>
                    );
                  })()}
                </Box>
                <Box sx={cell}>
                  <VariableColumnSelect
                    variable={v}
                    value={value}
                    ok={ok}
                    options={options}
                    onChange={(next) => setOne(v, next)}
                    onConstantStart={() =>
                      setEditingConstant({ v, at: Date.now() })
                    }
                  />
                </Box>
                <Box sx={{ ...cell, display: "block", py: 0.625 }}>
                  {isConstantMapping(value) ? (
                    <ConstantValueField
                      variable={v}
                      value={value}
                      focusKey={
                        editingConstant?.v === v ? editingConstant.at : 0
                      }
                      onChange={(next) => setOne(v, next)}
                    />
                  ) : isEmptyMapping(value) ? (
                    <Typography
                      typography="s2"
                      color="text.secondary"
                      noWrap
                      data-testid={`empty-label-${v}`}
                    >
                      {fixedMappingLabel(value)}
                    </Typography>
                  ) : ok && media ? (
                    <Stack direction="row" alignItems="center" gap={0.5}>
                      <Box
                        component="span"
                        sx={{
                          display: "inline-flex",
                          alignItems: "center",
                          gap: 0.5,
                          px: 0.75,
                          borderRadius: 0.5,
                          typography: "s3",
                          fontWeight: 500,
                          lineHeight: "20px",
                          bgcolor: "action.hover",
                          color: "text.secondary",
                        }}
                      >
                        <Iconify icon={kindIcon(media)} width={14} />
                        {media}
                      </Box>
                      <Typography typography="s3" color="text.disabled" noWrap>
                        {media === "image"
                          ? "Sent as an image input"
                          : "Sent as a media input"}
                      </Typography>
                    </Stack>
                  ) : (
                    <CustomTooltip
                      show={Boolean(preview && preview.length > PREVIEW_CHARS)}
                      title={preview}
                      size="small"
                      type="black"
                    >
                      <Typography
                        typography="s2"
                        color={preview ? "text.primary" : "text.disabled"}
                        noWrap
                      >
                        {ok
                          ? preview == null || preview === ""
                            ? "empty on this row"
                            : clip(preview)
                          : "Not mapped"}
                      </Typography>
                    </CustomTooltip>
                  )}
                  {ok && coverage ? (
                    coverage.present === coverage.total ? (
                      <Typography typography="s3" color="text.secondary" noWrap>
                        {`Found in all ${coverage.total.toLocaleString("en-US")} rows`}
                      </Typography>
                    ) : coverage.present >= coverage.total * 0.9 ? (
                      <Typography typography="s3" color="text.secondary">
                        {`Found in ${coverage.present.toLocaleString("en-US")} of ${coverage.total.toLocaleString("en-US")} rows`}
                        {missingHref ? (
                          <>
                            {" · "}
                            <Link
                              href={missingHref}
                              target="_blank"
                              rel="noreferrer"
                              typography="s3"
                            >
                              {`View the ${(coverage.total - coverage.present).toLocaleString("en-US")} ${coverage.total - coverage.present === 1 ? "row" : "rows"}`}
                            </Link>
                          </>
                        ) : null}
                      </Typography>
                    ) : (
                      <Typography typography="s3" sx={{ color: "amber.700" }}>
                        {`Not found in ${(coverage.total - coverage.present).toLocaleString("en-US")} of ${coverage.total.toLocaleString("en-US")} rows · they get an empty value`}
                        {missingHref ? (
                          <>
                            {" · "}
                            <Link
                              href={missingHref}
                              target="_blank"
                              rel="noreferrer"
                              typography="s3"
                            >
                              {`View the ${(coverage.total - coverage.present).toLocaleString("en-US")} rows`}
                            </Link>
                          </>
                        ) : null}
                      </Typography>
                    )
                  ) : null}
                </Box>
                <Box sx={{ ...cell, justifyContent: "center" }}>
                  <Iconify
                    icon={ok && !rename ? "mdi:check-circle" : "mdi:alert"}
                    width={16}
                    sx={{
                      color: ok && !rename ? "success.main" : "warning.main",
                    }}
                  />
                </Box>
                {rename ? (
                  <Box
                    data-testid={`rename-${v}`}
                    sx={{ ...cell, gridColumn: "1 / -1", gap: 0.75, py: 0.75 }}
                  >
                    <Iconify
                      icon="mdi:alert"
                      width={14}
                      sx={{ color: "warning.main", flexShrink: 0 }}
                    />
                    <Typography typography="s2" color="warning.dark">
                      {renameVariableText(v, value, mappingColumns, kind)}
                    </Typography>
                  </Box>
                ) : null}
              </React.Fragment>
            );
          })}
        </Box>
      ) : (
        <Typography
          typography="s2"
          color="text.secondary"
          sx={{ px: 1.25, pb: 1 }}
        >
          No variables to map.
        </Typography>
      )}
      {warnings.length ? (
        <Stack
          gap={0.5}
          data-testid="modality-warnings"
          sx={{
            px: 1.25,
            py: 0.75,
            borderTop: "1px solid",
            borderColor: "divider",
          }}
        >
          {warnings.map((w) => (
            <Stack
              key={`${w.letter}-${w.variable}`}
              direction="row"
              alignItems="center"
              gap={0.75}
              data-testid="modality-warning"
            >
              <Iconify
                icon="mdi:alert"
                width={14}
                sx={{ color: "warning.main", flexShrink: 0 }}
              />
              {w.letter ? (
                <RunLetterBadge
                  letter={w.letter}
                  ordinal={nextOrdinal ?? undefined}
                />
              ) : null}
              <Typography typography="s2" color="warning.dark">
                {`${w.text}. The run sends it anyway and the model answers without it.`}
              </Typography>
            </Stack>
          ))}
        </Stack>
      ) : null}
      {!allMapped ? (
        <Stack
          direction="row"
          alignItems="center"
          flexWrap="wrap"
          gap={0.75}
          data-testid="unmapped-message"
          sx={{ px: 1.25, py: 0.75 }}
        >
          <Typography typography="s2" color="warning.dark">
            {unmappedMessage(status.unmapped)}
          </Typography>
          <Button
            size="small"
            variant="outlined"
            onClick={() => setAllEmpty(status.unmapped)}
            sx={{ typography: "s2", height: 24, px: 1 }}
          >
            {leaveEmptyLabel(status.unmapped.length)}
          </Button>
          {status.unmapped.length === 1 ? (
            <Button
              size="small"
              variant="text"
              onClick={() => {
                const only = status.unmapped[0];
                setOne(
                  only,
                  isConstantMapping(mapping?.[only])
                    ? mapping[only]
                    : constantMapping(""),
                );
                setEditingConstant({ v: only, at: Date.now() });
              }}
              sx={{ typography: "s2", height: 24, px: 1 }}
            >
              Same value…
            </Button>
          ) : null}
        </Stack>
      ) : null}
    </Box>
  );
};

VariableMappingTable.propTypes = {
  control: PropTypes.object.isRequired,
  setValue: PropTypes.func.isRequired,
  fieldPrefix: PropTypes.string.isRequired,
  title: PropTypes.string,
  letters: PropTypes.arrayOf(PropTypes.string),
  icon: PropTypes.node,
  showErrors: PropTypes.bool,
  nameOnly: PropTypes.bool,
  kind: PropTypes.string,
};

export default VariableMappingTable;
