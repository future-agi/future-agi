import React, { useEffect, useState } from "react";
import PropTypes from "prop-types";
import { Typography } from "@mui/material";
import Iconify from "src/components/iconify";
import { sqlApi } from "./api";
import { ESTIMATE_DEBOUNCE_MS, describeEstimate } from "./estimateText";
import { warningText } from "./queryUi";

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
      aria-live="polite"
      data-testid="sql-estimate"
      data-tone={tone}
      sx={{
        ml: "auto",
        display: "inline-flex",
        alignItems: "center",
        justifyContent: "flex-end",
        textAlign: "right",
        gap: 0.5,
        minWidth: 0,
        overflowWrap: "anywhere",
        typography: "s3",
        color: tone === "warning" ? warningText : "text.secondary",
      }}
    >
      <Iconify
        icon={
          tone === "warning"
            ? "solar:danger-triangle-linear"
            : "solar:database-linear"
        }
        width={12}
        sx={{ flexShrink: 0 }}
      />
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
