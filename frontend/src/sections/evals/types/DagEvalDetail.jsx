// W7 A2 (W7-3): a decision tree's Details tab — the definition (GET …/dag/)
// in the same editor as Create, its readable outline, Save version (PATCH
// …/dag/, then the eval's versions/create with config_snapshot {dag}; the
// toast reads `Saved v<N>`) and the paths a run took. `inPanel`: the
// wizard's side panel (Edit definition) — the editor and Save version, no
// paths. Shown only once the W7 routes are registered (integration).
import React, { useEffect, useState } from "react";
import PropTypes from "prop-types";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { Box, Button, Stack, Tooltip, Typography } from "@mui/material";
import { enqueueSnackbar } from "notistack";
import axios, { endpoints } from "src/utils/axios";
import { validateDag } from "src/sections/common/EvalTypes/dagModel";
import DagDefinitionEditor from "./DagDefinitionEditor";
import DagPathsTable from "./DagPathsTable";
import DagRunPanel from "../dag/DagRunPanel";

export default function DagEvalDetail({ templateId, inPanel = false }) {
  const queryClient = useQueryClient();
  const { data } = useQuery({
    queryKey: ["w7-dag", templateId],
    queryFn: async () => {
      const { data: res } = await axios.get(
        endpoints.develop.w7.dag(templateId),
      );
      return res?.result ?? res;
    },
    enabled: Boolean(templateId),
  });
  const [dag, setDag] = useState(null);
  const [threshold, setThreshold] = useState(null);
  const [dirty, setDirty] = useState(false);
  const [saving, setSaving] = useState(false);
  useEffect(() => {
    if (!data?.dag) return;
    setDag(data.dag);
    setThreshold(data.pass_threshold ?? null);
    setDirty(false);
  }, [data]);
  if (!dag) return null;
  const errors = validateDag(dag);
  const reasons = [...(!dirty ? ["No changes to save"] : []), ...errors];
  const saveVersion = async () => {
    setSaving(true);
    try {
      const { data: patched } = await axios.patch(
        endpoints.develop.w7.dag(templateId),
        {
          dag,
          output_type: dag.output_type,
          ...(threshold != null ? { pass_threshold: threshold } : {}),
        },
      );
      // The version keeps the tree and its readable outline (its rubric).
      const { data: v } = await axios.post(
        endpoints.develop.eval.createEvalVersion(templateId),
        {
          criteria: patched?.result?.instructions ?? "",
          // The judge a question without its own asks (the template's).
          ...(data.model ? { model: data.model } : {}),
          config_snapshot: {
            dag,
            config: { dag },
            ...(threshold != null ? { pass_threshold: threshold } : {}),
          },
        },
      );
      const n = v?.result?.version_number;
      enqueueSnackbar(n ? `Saved v${n}` : "Saved", { variant: "success" });
      setDirty(false);
      queryClient.invalidateQueries({ queryKey: ["w7-dag", templateId] });
      // The wizard's binding summary and the detail read the new outline.
      queryClient.invalidateQueries({ queryKey: ["evals"] });
    } catch (e) {
      const errs = e?.response?.data?.errors;
      enqueueSnackbar(
        errs?.length ? errs.join(" · ") : e?.message ?? "Could not save",
        {
          variant: "error",
        },
      );
    } finally {
      setSaving(false);
    }
  };
  return (
    <Stack gap={2} sx={{ p: inPanel ? 0 : 2 }} data-testid="dag-detail">
      <Stack direction="row" alignItems="center" gap={1}>
        <Typography typography="s1" fontWeight={700}>
          Decision tree
        </Typography>
        <Typography typography="s3" color="text.secondary">
          {data.version}
        </Typography>
        <Box sx={{ flex: 1 }} />
        <Tooltip
          title={reasons.join(" · ")}
          disableHoverListener={!reasons.length}
        >
          <span>
            <Button
              size="small"
              variant="contained"
              disabled={saving || reasons.length > 0}
              onClick={saveVersion}
              sx={{ textTransform: "none" }}
              data-testid="dag-save-version"
            >
              {saving ? "Saving..." : "Save version"}
            </Button>
          </span>
        </Tooltip>
      </Stack>
      <DagDefinitionEditor
        dag={dag}
        onChange={(d) => {
          setDag(d);
          setDirty(true);
        }}
        threshold={threshold}
        onThresholdChange={(t) => {
          setThreshold(t);
          setDirty(true);
        }}
        disabled={saving}
        templateId={templateId}
      />
      <Box>
        <Typography typography="s2" fontWeight={600}>
          How the scorer reads it
        </Typography>
        <Typography
          component="pre"
          typography="s3"
          color="text.secondary"
          sx={{ whiteSpace: "pre-wrap", m: 0 }}
          data-testid="dag-instructions"
        >
          {data.instructions}
        </Typography>
      </Box>
      {inPanel ? null : (
        <>
          <DagRunPanel templateId={templateId} dag={data.dag} dirty={dirty} />
          <DagPathsTable templateId={templateId} />
        </>
      )}
    </Stack>
  );
}

/** Real once the W7 routes are registered (integration). */
DagEvalDetail.ready = Boolean(endpoints.develop?.w7?.dag);

DagEvalDetail.propTypes = {
  templateId: PropTypes.string,
  evalData: PropTypes.object,
  inPanel: PropTypes.bool,
};
