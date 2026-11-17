// W7 A0 (review MF5): a typed eval's definition in a side panel over the
// wizard — the same Details view as Evals (the editor, Test on a row, Save
// version), so the experiment draft and the session stay where they are.
import React from "react";
import PropTypes from "prop-types";
import { Box, Drawer, IconButton, Stack, Typography } from "@mui/material";
import Iconify from "src/components/iconify";
import { typedEvalLabel } from "src/sections/common/EvalTypes/typedEvalTypes";
import TypedEvalDetail from "./TypedEvalDetail";

export default function TypedEvalPanel({
  open,
  onClose,
  evalType,
  templateId,
  evalData,
}) {
  return (
    <Drawer
      anchor="right"
      open={open}
      onClose={onClose}
      PaperProps={{ sx: { width: "min(880px, 92vw)", p: 2 } }}
    >
      <Stack data-testid="typed-eval-panel" gap={1.5}>
        <Stack direction="row" alignItems="center" gap={1}>
          <Typography typography="s1" fontWeight={700} noWrap>
            {evalData?.name ?? "Evaluation"}
          </Typography>
          <Typography typography="s3" color="text.secondary">
            {typedEvalLabel(evalType)}
          </Typography>
          <Box sx={{ flex: 1 }} />
          <IconButton size="small" onClick={onClose} aria-label="Close">
            <Iconify icon="mingcute:close-line" width={18} />
          </IconButton>
        </Stack>
        {open ? (
          <TypedEvalDetail
            evalType={evalType}
            templateId={templateId}
            evalData={evalData}
            inPanel
          />
        ) : null}
      </Stack>
    </Drawer>
  );
}

TypedEvalPanel.propTypes = {
  open: PropTypes.bool,
  onClose: PropTypes.func,
  evalType: PropTypes.string,
  templateId: PropTypes.string,
  evalData: PropTypes.object,
};
