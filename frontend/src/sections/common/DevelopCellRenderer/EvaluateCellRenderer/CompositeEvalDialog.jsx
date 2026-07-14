import {
  Dialog,
  DialogContent,
  DialogTitle,
  IconButton,
} from "@mui/material";
import React from "react";
import Iconify from "src/components/iconify";
import CompositeResultView from "src/sections/evals/components/CompositeResultView";
import { useCompositeEvalStore } from "src/sections/develop-detail/states";

const CompositeEvalDialog = () => {
  const compositeEval = useCompositeEvalStore((s) => s.compositeEval);
  const setCompositeEval = useCompositeEvalStore((s) => s.setCompositeEval);
  const open = Boolean(compositeEval);

  const close = () => setCompositeEval(null);

  const children = compositeEval?.children || [];

  return (
    <Dialog open={open} onClose={close} maxWidth="md" fullWidth>
      <DialogTitle
        sx={{
          display: "flex",
          alignItems: "center",
          justifyContent: "space-between",
          fontSize: "15px",
          fontWeight: 600,
        }}
      >
        Composite evaluation breakdown
        <IconButton size="small" onClick={close} aria-label="Close">
          <Iconify icon="mdi:close" width={18} />
        </IconButton>
      </DialogTitle>
      <DialogContent dividers sx={{ p: 0 }}>
        {open && (
          <CompositeResultView
            compositeResult={{
              aggregation_enabled: compositeEval?.aggregation_enabled,
              aggregation_function: compositeEval?.aggregation_function,
              aggregate_score: compositeEval?.aggregate_score,
              aggregate_pass: compositeEval?.aggregate_pass,
              summary: compositeEval?.summary,
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
