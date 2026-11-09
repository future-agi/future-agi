// W7 A2 (W7-3): Evals › Create › Decision tree. The definition editor plus
// Save, which posts the tree to create-dag/ through the page's typed save
// (EvalCreatePage.handleSaveTyped). Save is disabled until the tree is valid;
// its tooltip lists the reasons. Shown only once the W7 routes are
// registered (integration).
import React, { useEffect, useState } from "react";
import PropTypes from "prop-types";
import { Button, Stack, Tooltip } from "@mui/material";
import { enqueueSnackbar } from "notistack";
import Iconify from "src/components/iconify";
import { endpoints } from "src/utils/axios";
import { validateDag } from "src/sections/common/EvalTypes/dagModel";
import DagDefinitionEditor from "./DagDefinitionEditor";
import { blankDag } from "./dagEditorModel";

export default function DagEvalEditor({ name, onSave, saving = false }) {
  const [dag, setDag] = useState(() => blankDag());
  const [threshold, setThreshold] = useState(null);
  // The create page's right panel (its graph and Test on a row go there).
  const [aside, setAside] = useState(null);
  useEffect(() => {
    setAside(document.getElementById("typed-eval-aside"));
  }, []);
  const errors = validateDag(dag);
  const reasons = [
    ...(!String(name ?? "").trim()
      ? ["Give this evaluation a name before saving."]
      : []),
    ...errors,
  ];
  const save = async () => {
    try {
      await onSave(endpoints.develop.w7.createDag, {
        output_type: dag.output_type ?? "score",
        ...(threshold != null ? { pass_threshold: threshold } : {}),
        dag,
      });
    } catch (e) {
      const errs = e?.response?.data?.errors;
      enqueueSnackbar(
        errs?.length ? errs.join(" · ") : e?.message ?? "Could not save",
        {
          variant: "error",
        },
      );
    }
  };
  return (
    <Stack gap={1.5}>
      <DagDefinitionEditor
        dag={dag}
        onChange={setDag}
        threshold={threshold}
        onThresholdChange={setThreshold}
        disabled={saving}
        asideTarget={aside}
      />
      <Tooltip
        title={reasons.join(" · ")}
        disableHoverListener={!reasons.length}
      >
        <span style={{ alignSelf: "flex-start" }}>
          <Button
            variant="contained"
            size="small"
            disabled={saving || reasons.length > 0}
            onClick={save}
            startIcon={<Iconify icon="mdi:content-save-outline" width={16} />}
            sx={{ textTransform: "none" }}
            data-testid="dag-save"
          >
            {saving ? "Saving..." : "Save decision tree"}
          </Button>
        </span>
      </Tooltip>
    </Stack>
  );
}

/** Real once the W7 routes are registered (integration). */
DagEvalEditor.ready = Boolean(endpoints.develop?.w7?.createDag);

DagEvalEditor.propTypes = {
  onSave: PropTypes.func,
  saving: PropTypes.bool,
  name: PropTypes.string,
};
