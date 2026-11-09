// W7-4 remote scorers: the wizard picker's read-only summary of a remote
// scorer (mounted by TypedEvalPickerSummary). Its cells read "Scoring at
// <host>" until the scorer answers. A0's dispatcher passes the picked eval
// (evalData); its definition is read from the scorer's own route.
import React, { useEffect, useState } from "react";
import PropTypes from "prop-types";
import { Box, Typography } from "@mui/material";
import axios, { endpoints } from "src/utils/axios";
import { SIGNATURE_LABEL, secondsText } from "./remoteModel";

const AUTH = {
  hmac: SIGNATURE_LABEL,
  bearer: "Bearer token",
  oauth: "OAuth client credentials",
  none: "No auth",
};

export default function RemotePickerSummary({ evalData, templateId: id }) {
  const templateId = id ?? evalData?.template_id ?? evalData?.id ?? null;
  const [d, setD] = useState(null);
  useEffect(() => {
    if (!templateId) return undefined;
    let live = true;
    axios
      .get(endpoints.develop.w7.remote(templateId))
      .then((res) => live && setD(res?.data?.result ?? null))
      .catch(() => live && setD(null));
    return () => {
      live = false;
    };
  }, [templateId]);
  if (!d) return null;
  const r = d.remote;
  return (
    <Box data-testid="remote-picker-summary" sx={{ py: 0.5 }}>
      <Typography variant="caption" component="div" sx={{ fontWeight: 600 }}>
        Remote scorer · {d.host}
      </Typography>
      <Typography variant="caption" component="div" color="text.secondary">
        {AUTH[r.auth?.kind] ?? r.auth?.kind} · timeout{" "}
        {secondsText(r.timeout_ms)} · {r.retries}{" "}
        {r.retries === 1 ? "retry" : "retries"} · {r.calls_at_a_time} at a time
        · answers{" "}
        {r.output?.type === "pass_fail" ? "pass / fail" : r.output?.type}
      </Typography>
      <Typography variant="caption" component="div" color="text.secondary">
        Cells read &quot;Scoring at {d.host}&quot; until it answers. Billed by
        your service · 0 Future AGI credits.
      </Typography>
    </Box>
  );
}

RemotePickerSummary.ready = true;

RemotePickerSummary.propTypes = {
  evalData: PropTypes.object,
  templateId: PropTypes.string,
};
