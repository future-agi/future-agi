import {
  Dialog,
  DialogContent,
  DialogTitle,
  IconButton,
} from "@mui/material";
import React, { useEffect, useState } from "react";
import Iconify from "src/components/iconify";
import CompositeResultView from "src/sections/evals/components/CompositeResultView";
import { useCompositeEvalStore } from "src/sections/develop-detail/states";

const CompositeEvalDialog = () => {
  const compositeEval = useCompositeEvalStore((s) => s.compositeEval);
  const setCompositeEval = useCompositeEvalStore((s) => s.setCompositeEval);
  const open = Boolean(compositeEval);

  // Retain the last composite so the content stays mounted through the
  // Dialog's close transition; clearing it immediately would collapse the
  // modal height to just the title before the fade-out finishes.
  const [rendered, setRendered] = useState(compositeEval);
  useEffect(() => {
    if (compositeEval) setRendered(compositeEval);
  }, [compositeEval]);

  const close = () => setCompositeEval(null);

  const children = rendered?.children || [];

  return (
    <Dialog
      open={open}
      onClose={close}
      maxWidth="md"
      fullWidth
      TransitionProps={{ onExited: () => setRendered(null) }}
    >
      <DialogTitle
        sx={{
          display: "flex",
          alignItems: "center",
          justifyContent: "space-between",
          typography: "subtitle1",
        }}
      >
        Composite evaluation breakdown
        <IconButton size="small" onClick={close} aria-label="Close">
          <Iconify icon="mdi:close" width={18} />
        </IconButton>
      </DialogTitle>
      <DialogContent dividers sx={{ p: 0 }}>
        {rendered && (
          <CompositeResultView
            compositeResult={{
              aggregation_enabled: rendered?.aggregation_enabled,
              aggregation_function: rendered?.aggregation_function,
              aggregate_score: rendered?.aggregate_score,
              aggregate_pass: rendered?.aggregate_pass,
              summary: rendered?.summary,
              children,
              total_children: children.length,
              completed_children: children.filter(
                (c) => c?.status === "completed",
              ).length,
              failed_children: children.filter((c) => c?.status === "failed")
                .length,
            }}
          />
        )}
      </DialogContent>
    </Dialog>
  );
};

export default CompositeEvalDialog;
