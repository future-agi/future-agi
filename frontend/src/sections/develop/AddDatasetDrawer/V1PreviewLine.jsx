import React from "react";
import PropTypes from "prop-types";
import { Typography } from "@mui/material";
import { useQuery } from "@tanstack/react-query";
import axios from "src/utils/axios";
import { IMPORTS_PATH } from "src/sections/common/AddData/mappingModel";
import { v1LineText } from "./v1Line";

/**
 * W3-7: the last line of the blank-grid and task-suite doors. The server
 * previews the v1 (IM1 `source.kind: "blank"`, which writes nothing) from
 * the same spec create-dataset-manually creates with, so the line and the
 * v1 come from one place. `spec`: {kind (rows | task_suite), columns?,
 * number_of_columns?, number_of_rows?}; `name`: the dataset name.
 */
const V1PreviewLine = ({
  name,
  spec,
  enabled = true,
  testId = "v1-preview",
}) => {
  const body = {
    op: "preview",
    source: (({ kind, ...rest }) => ({
      ...rest,
      kind: "blank",
      dataset_kind: kind,
    }))(spec ?? {}),
    target: { new: { name: String(name ?? "").trim() } },
  };
  const q = useQuery({
    queryKey: ["create-v1-preview", body],
    queryFn: () =>
      axios.post(IMPORTS_PATH, body).then((r) => r?.data?.result?.v1 ?? null),
    enabled: Boolean(enabled && String(name ?? "").trim()),
    staleTime: 30000,
    retry: false,
  });
  if (!enabled || !q.data) return null;
  return (
    <Typography typography="s3" color="text.secondary" data-testid={testId}>
      {v1LineText(q.data)}
    </Typography>
  );
};

V1PreviewLine.propTypes = {
  name: PropTypes.string,
  spec: PropTypes.object,
  enabled: PropTypes.bool,
  testId: PropTypes.string,
};

export default V1PreviewLine;
