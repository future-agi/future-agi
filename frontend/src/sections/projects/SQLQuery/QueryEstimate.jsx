import React, { useEffect, useState } from "react";
import PropTypes from "prop-types";
import { Typography } from "@mui/material";
import { sqlApi } from "./api";
import { ESTIMATE_DEBOUNCE_MS, describeEstimate } from "./estimateText";

export default function QueryEstimate({
  projectId,
  sql,
  hours,
  enabled = true,
}) {
  const [state, setState] = useState({ status: "idle" });
  useEffect(() => {
    if (!enabled || !projectId || !sql.trim()) {
      setState({ status: "idle" });
      return undefined;
    }
    const controller = new AbortController();
    const timer = setTimeout(() => {
      setState({ status: "loading" });
      const end = new Date();
      sqlApi
        .estimate(
          {
            project_id: projectId,
            sql,
            start: new Date(end.getTime() - hours * 3600000).toISOString(),
            end: end.toISOString(),
          },
          controller.signal,
        )
        .then((value) => {
          if (!controller.signal.aborted) setState({ status: "done", value });
        })
        .catch((error) => {
          if (!controller.signal.aborted) setState({ status: "error", error });
        });
    }, ESTIMATE_DEBOUNCE_MS);
    return () => {
      clearTimeout(timer);
      controller.abort();
    };
  }, [projectId, sql, hours, enabled]);
  if (state.status === "idle") return null;
  const { tone, text } = describeEstimate(state);
  return (
    <Typography
      variant="caption"
      aria-live="polite"
      data-testid="sql-estimate"
      data-tone={tone}
      color={tone === "warning" ? "warning.main" : "text.secondary"}
      sx={{
        ml: "auto",
        textAlign: "right",
        minWidth: 0,
        overflowWrap: "anywhere",
      }}
    >
      {text}
    </Typography>
  );
}

QueryEstimate.propTypes = {
  projectId: PropTypes.string,
  sql: PropTypes.string.isRequired,
  hours: PropTypes.number.isRequired,
  enabled: PropTypes.bool,
};
