// W7 A0: the wizard's binding panel for a typed eval (integration S10): the
// definition read-only, the existing Variable Mapping (children) and Edit
// definition, which opens the eval's Details (editor, Test, Save version) in
// a side panel over the wizard (review MF5: a new tab starts a new session,
// so it would not find an eval made in this one). The instructions, model
// and few-shot editors of a judge eval do not apply to a typed eval.
import React, { useState } from "react";
import PropTypes from "prop-types";
import { Box, Button, Stack, Typography } from "@mui/material";
import Iconify from "src/components/iconify";
import TypedEvalPanel from "src/sections/evals/types/TypedEvalPanel";
import { hasTypedDetail } from "src/sections/evals/types/typedViews";
import TypedEvalPickerSummary from "./TypedEvalPickerSummary";
import { typedEvalLabel } from "./typedEvalTypes";

export default function TypedEvalBindingConfig({
  evalType,
  evalData,
  templateId,
  children,
}) {
  const [open, setOpen] = useState(false);
  const id = templateId ?? evalData?.id ?? null;
  return (
    <Stack gap={1.5} data-testid="typed-eval-binding">
      <Stack direction="row" alignItems="center" justifyContent="space-between">
        <Typography typography="s2" fontWeight={600}>
          {typedEvalLabel(evalType) ?? "Evaluation"}
        </Typography>
        {id && hasTypedDetail(evalType) ? (
          <Button
            size="small"
            variant="text"
            onClick={() => setOpen(true)}
            startIcon={<Iconify icon="solar:pen-bold" width={14} />}
            sx={{ textTransform: "none" }}
            data-testid="typed-eval-edit"
          >
            Edit definition
          </Button>
        ) : null}
      </Stack>
      <TypedEvalPickerSummary evalType={evalType} evalData={evalData} />
      {children ? <Box>{children}</Box> : null}
      {id ? (
        <TypedEvalPanel
          open={open}
          onClose={() => setOpen(false)}
          evalType={evalType}
          templateId={id}
          evalData={evalData}
        />
      ) : null}
    </Stack>
  );
}

TypedEvalBindingConfig.propTypes = {
  evalType: PropTypes.string,
  evalData: PropTypes.object,
  templateId: PropTypes.string,
  children: PropTypes.node,
};
