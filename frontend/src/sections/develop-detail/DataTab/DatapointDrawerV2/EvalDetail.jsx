import React from "react";
import PropTypes from "prop-types";
import { Box, Button, Stack, Typography } from "@mui/material";
import Iconify from "src/components/iconify";
import CellMarkdown from "src/sections/common/CellMarkdown";
import {
  useAddEvaluationFeebackStore,
  useImprovePromptStore,
} from "src/sections/develop-detail/states";
import ErrorLocalizationSection from "./ErrorLocalizationSection";

const directionText = (meta) => {
  const parts = [];
  if (meta?.higher_is_better === false) parts.push("Lower is better");
  else parts.push("Higher is better");
  if (Number.isFinite(meta?.pass_threshold))
    parts.push(`pass at ${meta.pass_threshold.toFixed(2)}`);
  if (meta?.version) parts.push(meta.version);
  if (meta?.model) parts.push(`judge ${meta.model}`);
  return parts.join(" · ");
};

/**
 * What opens under an eval chip (PRODUCT-SPEC §3.4): the judge's reason,
 * Possible error (error localization) on a failure, Add feedback and, for an
 * eval on a Run Prompt output, Improve prompt.
 */
const EvalDetail = ({ ev, row, output, canEdit }) => {
  const { setAddEvaluationFeeback } = useAddEvaluationFeebackStore();
  const { setImprovePrompt } = useImprovePromptStore();
  const cell = ev.cell;
  const isCode = ev.meta?.eval_type === "code";
  const failed = ev.verdict.status === "fail";
  const reason =
    ev.verdict.status === "error"
      ? cell?.value_infos?.reason ?? "The eval could not run on this row."
      : ev.reason;

  return (
    <Box
      sx={{
        mt: 0.75,
        p: 1.25,
        borderRadius: 1,
        border: "1px solid",
        borderColor: "divider",
        bgcolor: "background.paper",
      }}
    >
      <Stack direction="row" alignItems="center" gap={0.75} sx={{ mb: 0.5 }}>
        <Typography typography="s2" fontWeight="fontWeightSemiBold">
          {ev.column.name}
        </Typography>
        <Typography typography="s3" color="text.disabled" noWrap>
          {directionText(ev.meta)}
        </Typography>
      </Stack>
      {reason ? (
        <Box sx={{ "& p": { m: 0 }, color: "text.primary" }}>
          <CellMarkdown text={reason} fontSize={13} spacing={0} />
        </Box>
      ) : (
        <Typography typography="s2" color="text.disabled">
          The judge gave no reason for this score.
        </Typography>
      )}
      {failed && !isCode && cell?.cell_id && (
        <ErrorLocalizationSection evalOpen={cell} />
      )}
      {canEdit && cell && ev.verdict.status !== "running" && (
        <Stack direction="row" gap={1} sx={{ mt: 1.25 }}>
          <Button
            size="small"
            variant="outlined"
            color="inherit"
            startIcon={
              <Iconify icon="mdi:message-reply-text-outline" width={16} />
            }
            onClick={() =>
              setAddEvaluationFeeback({
                ...ev.column,
                sourceId: ev.column.source_id,
                rowData: row,
                value: cell.cell_value,
                valueInfos: cell.value_infos,
              })
            }
            sx={{ borderColor: "divider" }}
          >
            Add feedback
          </Button>
          {output && (
            <Button
              size="small"
              variant="outlined"
              color="inherit"
              startIcon={<Iconify icon="mdi:creation" width={16} />}
              onClick={() =>
                setImprovePrompt({
                  id: output.column.id,
                  name: output.column.name,
                  originType: "run_prompt",
                  metadata: {},
                  rowData: row,
                  valueInfos: { reason: ev.reason },
                })
              }
              sx={{ borderColor: "divider" }}
            >
              Improve prompt
            </Button>
          )}
        </Stack>
      )}
    </Box>
  );
};

EvalDetail.propTypes = {
  ev: PropTypes.object.isRequired,
  row: PropTypes.object,
  output: PropTypes.object,
  canEdit: PropTypes.bool,
};

export default EvalDetail;
