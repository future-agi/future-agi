// W7 A2 (W7-3): a decision tree's read-only summary in the wizard picker
// (S9 / S10): the tree in one line and the scores it can reach, read from
// its definition (GET …/dag/). The numbered outline the scorer reads shows
// as the eval's instructions; the tree is edited in Evals.
import React from "react";
import PropTypes from "prop-types";
import { useQuery } from "@tanstack/react-query";
import { Stack, Typography } from "@mui/material";
import axios, { endpoints } from "src/utils/axios";
import { dagSummaryOf } from "./dagModel";

export default function DagPickerSummary({ evalData }) {
  const id = evalData?.id ?? evalData?.template_id ?? null;
  const { data } = useQuery({
    queryKey: ["w7-dag", id],
    queryFn: async () => {
      const { data: res } = await axios.get(endpoints.develop.w7.dag(id));
      return res?.result ?? res;
    },
    enabled: Boolean(id && endpoints.develop?.w7?.dag),
  });
  if (!data?.dag) return null;
  const leaves = data.dag.nodes.filter((n) => n.kind === "leaf");
  const line = dagSummaryOf(data.dag);
  // A tree whose author wrote no description is described by this same line
  // (create-dag/), and the picker's preview prints the description under
  // this summary: say it once (review SF6).
  const described = String(evalData?.description ?? "").trim() === line;
  return (
    <Stack gap={0.25} data-testid="dag-picker-summary">
      {described ? null : (
        <Typography typography="s2" fontWeight={600}>
          {line}
        </Typography>
      )}
      <Typography typography="s3" color="text.secondary">
        {`Scores: ${leaves.map((l) => `${l.label} ${Number(l.score).toFixed(2)}`).join(" · ")}`}
      </Typography>
    </Stack>
  );
}

DagPickerSummary.ready = true;

DagPickerSummary.propTypes = {
  evalData: PropTypes.object,
};
