import React, { useState } from "react";
import PropTypes from "prop-types";
import {
  Box,
  Button,
  MenuItem,
  Select,
  Skeleton,
  Stack,
  Typography,
} from "@mui/material";
import { alpha } from "@mui/material/styles";
import Iconify from "src/components/iconify";
import FieldCard from "src/sections/common/ValueViewer/FieldCard";
import TextDiff, {
  TextDiffLegend,
} from "src/sections/common/ValueViewer/TextDiff";
import ValueViewer from "src/sections/common/ValueViewer/ValueViewer";
import EvalChip from "./EvalChip";
import EvalDetail from "./EvalDetail";
import { outputFooter } from "./rowModel";
import OutlineToggle from "src/sections/common/ux/oneview/OutlineToggle";
import TraceDetailDrawerV2 from "src/components/traceDetail/TraceDetailDrawerV2";
import {
  CODE_COLUMNS_DISABLED_REASON,
  isCodeColumn,
} from "../../AddColumn/ExecuteCode/codeDisabled";

// What generated a column (L5 G4): the Add column type, by origin.
const KIND_LABEL = {
  run_prompt: "Run Prompt",
  vector_db: "Retrieval",
  extracted_entities: "Extract Entities",
  extracted_json: "Extract a JSON Key",
  classification: "Classification",
  api_call: "API Call",
  conditional: "Conditional Node",
  python_code: "Python",
};

const evalTooltip = (ev) => {
  if (ev.verdict.status === "error") return "The eval errored on this row";
  if (ev.verdict.status === "running") return "Scoring this row";
  return ev.reason ? "Show the reason" : null;
};

/**
 * Error cell: the reason in a red card + `Re-run this cell`, or, where a
 * re-run cannot happen (`rerunOff`), the line that says why.
 */
const ErrorBody = ({ reason, onRerun, rerunning, canEdit, rerunOff }) => (
  <Box
    sx={{
      p: 1.25,
      borderRadius: 1,
      border: "1px solid",
      borderColor: "error.light",
      bgcolor: (t) => alpha(t.palette.red[600], 0.06),
    }}
  >
    <Stack direction="row" gap={1} alignItems="flex-start">
      <Iconify
        icon="mdi:alert-circle-outline"
        width={18}
        sx={{ color: "error.main", mt: 0.125, flexShrink: 0 }}
      />
      <Box sx={{ flex: 1, minWidth: 0 }}>
        <Typography
          typography="s2"
          fontWeight="fontWeightSemiBold"
          color="error.main"
        >
          This cell failed to generate
        </Typography>
        <Typography
          typography="s2"
          color="text.secondary"
          sx={{ mt: 0.25, wordBreak: "break-word" }}
        >
          {reason || "No error message was returned."}
        </Typography>
      </Box>
      {canEdit && !rerunOff && (
        <Button
          size="small"
          variant="outlined"
          color="error"
          disabled={rerunning}
          onClick={onRerun}
          startIcon={<Iconify icon="mdi:refresh" width={16} />}
          sx={{ flexShrink: 0 }}
        >
          {rerunning ? "Re-running…" : "Re-run this cell"}
        </Button>
      )}
    </Stack>
    {rerunOff && (
      <Typography
        typography="s2"
        color="text.secondary"
        sx={{ mt: 0.75, wordBreak: "break-word" }}
      >
        {rerunOff}
      </Typography>
    )}
  </Box>
);

ErrorBody.propTypes = {
  reason: PropTypes.string,
  onRerun: PropTypes.func,
  rerunning: PropTypes.bool,
  canEdit: PropTypes.bool,
  rerunOff: PropTypes.string,
};

/**
 * One generated (Run Prompt) column of the row (PRODUCT-SPEC §3.4):
 * header `name · Run Prompt · gpt-4o-mini · status`, the output (or a word
 * diff against an Expected field), `793 ms · 341 tok · $0.000075`, and the
 * evals that judged this output as chips that expand to their reason.
 */
const OutputCard = ({
  output,
  row,
  mode,
  diffOn,
  expectedOptions,
  compareWithId,
  onCompareWith,
  openEvalId,
  onToggleEval,
  canEdit,
  onRerun,
  rerunning,
  expanded,
  onToggleExpanded,
}) => {
  const { column, cell } = output;
  const status = cell?.status;
  const [traceOpen, setTraceOpen] = useState(false);
  const traceId =
    cell?.value_infos?.trace_id ?? cell?.metadata?.trace_id ?? null;
  const kindLabel =
    cell?.value_infos?.kind_label ??
    KIND_LABEL[column.origin_type ?? column.originType] ??
    "Run Prompt";
  const model =
    cell?.value_infos?.model ?? column.metadata?.model ?? column.model ?? null;
  // Python execution is off. Conditional cells also cannot use the row
  // action: it targets Run Prompt IDs and returns 404 for conditional IDs.
  // Row projections may omit branch metadata, so do not infer code support.
  const rerunOff = isCodeColumn(column)
    ? CODE_COLUMNS_DISABLED_REASON
    : (column.origin_type ?? column.originType) === "conditional"
      ? "Re-running individual conditional cells is not supported. Existing values remain readable."
      : null;
  const footer = outputFooter(cell);
  const expected =
    expectedOptions.find((e) => e.column.id === compareWithId) ??
    expectedOptions[0] ??
    null;
  const value = cell?.cell_value ?? "";

  let body;
  if (status === "running") {
    body = (
      <Stack gap={0.75}>
        <Skeleton variant="text" width="90%" />
        <Skeleton variant="text" width="70%" />
        <Typography typography="s2" color="text.disabled">
          Running…
        </Typography>
      </Stack>
    );
  } else if (status === "error") {
    body = (
      <ErrorBody
        reason={cell?.value_infos?.reason}
        onRerun={() => onRerun(column)}
        rerunning={rerunning}
        canEdit={canEdit}
        rerunOff={rerunOff}
      />
    );
  } else if (diffOn && expected && mode === "formatted") {
    body = (
      <Stack gap={1}>
        <TextDiff
          from={expected.value ?? expected.cell?.cell_value ?? ""}
          to={value}
          kind={["json", "array"].includes(column.data_type) ? "json" : "words"}
        />
      </Stack>
    );
  } else {
    body = (
      <ValueViewer value={value} dataType={column.data_type} mode={mode} />
    );
  }

  const diffBar = diffOn && expected && status !== "error" && (
    <Stack
      direction="row"
      alignItems="center"
      gap={1}
      sx={{ mt: 1, flexWrap: "wrap" }}
    >
      <Typography typography="s2" color="text.secondary">
        Compare with:
      </Typography>
      {expectedOptions.length > 1 ? (
        <Select
          size="small"
          value={expected.column.id}
          onChange={(e) => onCompareWith(e.target.value)}
          sx={{ typography: "s2", "& .MuiSelect-select": { py: 0.25 } }}
        >
          {expectedOptions.map((e) => (
            <MenuItem key={e.column.id} value={e.column.id}>
              {e.column.name}
            </MenuItem>
          ))}
        </Select>
      ) : (
        <Typography typography="s2" fontWeight="fontWeightMedium">
          {expected.column.name}
        </Typography>
      )}
      <Box sx={{ flex: 1 }} />
      <TextDiffLegend />
    </Stack>
  );

  const openEval = output.evals.find((e) => e.column.id === openEvalId);

  return (
    <FieldCard
      name={column.name}
      dataType={column.data_type}
      originType="run_prompt"
      value={value}
      mode={mode}
      locked={
        rerunOff
          ? `Generated by ${kindLabel} · read-only`
          : `Generated by ${kindLabel} · re-run to change`
      }
      expanded={expanded}
      actions={
        <>
          <Typography
            typography="s2"
            color="text.disabled"
            noWrap
            sx={{ mr: 0.5 }}
          >
            {kindLabel}
            {model ? ` · ${model}` : ""} ·{" "}
            <Box
              component="span"
              sx={{
                color:
                  status === "error"
                    ? "error.main"
                    : status === "running"
                      ? "text.secondary"
                      : "inherit",
              }}
            >
              {status === "error"
                ? "Failed"
                : status === "running"
                  ? "Running"
                  : "Completed"}
            </Box>
          </Typography>
          {onToggleExpanded && (
            <OutlineToggle
              open={expanded !== false}
              onToggle={onToggleExpanded}
              label={column.name}
            />
          )}
        </>
      }
      footer={
        <>
          {diffBar}
          {(footer.length > 0 || output.evals.length > 0 || traceId) && (
            <Stack
              direction="row"
              alignItems="center"
              gap={0.75}
              sx={{ mt: 1, flexWrap: "wrap", rowGap: 0.75 }}
            >
              {output.evals.map((ev) => (
                <EvalChip
                  key={ev.column.id}
                  name={ev.column.name}
                  verdict={ev.verdict}
                  selected={openEvalId === ev.column.id}
                  onClick={() => onToggleEval(ev.column.id)}
                  tooltip={evalTooltip(ev)}
                />
              ))}
              <Box sx={{ flex: 1 }} />
              {footer.length > 0 && (
                <Typography
                  typography="s2"
                  color="text.disabled"
                  sx={{ fontVariantNumeric: "tabular-nums" }}
                >
                  {footer.join(" · ")}
                </Typography>
              )}
              {traceId && (
                <Button
                  size="small"
                  variant="text"
                  onClick={() => setTraceOpen(true)}
                  startIcon={
                    <Iconify icon="mdi:timeline-text-outline" width={16} />
                  }
                  aria-label={`View trace of ${column.name}`}
                  sx={{ typography: "s2", py: 0 }}
                >
                  View trace
                </Button>
              )}
            </Stack>
          )}
          {traceOpen && traceId && (
            <TraceDetailDrawerV2
              traceId={traceId}
              open
              onClose={() => setTraceOpen(false)}
            />
          )}
          {openEval && (
            <EvalDetail
              ev={openEval}
              row={row}
              output={output}
              canEdit={canEdit}
            />
          )}
        </>
      }
    >
      {body}
    </FieldCard>
  );
};

OutputCard.propTypes = {
  output: PropTypes.shape({
    column: PropTypes.object.isRequired,
    cell: PropTypes.object,
    evals: PropTypes.array.isRequired,
  }).isRequired,
  row: PropTypes.object,
  mode: PropTypes.oneOf(["formatted", "raw"]),
  diffOn: PropTypes.bool,
  expectedOptions: PropTypes.array,
  compareWithId: PropTypes.string,
  onCompareWith: PropTypes.func,
  openEvalId: PropTypes.string,
  onToggleEval: PropTypes.func,
  canEdit: PropTypes.bool,
  onRerun: PropTypes.func,
  rerunning: PropTypes.bool,
  expanded: PropTypes.bool,
  onToggleExpanded: PropTypes.func,
};

export default OutputCard;
