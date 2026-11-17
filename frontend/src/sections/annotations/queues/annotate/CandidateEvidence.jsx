import React, { useState } from "react";
import PropTypes from "prop-types";
import { Box, Collapse, Link, Stack } from "@mui/material";
import Iconify from "src/components/iconify";

/**
 * Why a run passed or failed a review row: each eval's
 * verdict and the reason it recorded, and `Open trace` for the run's
 * trace. Hidden on blind rows (it would name the evals' verdicts).
 */
export default function CandidateEvidence({ candidate, blind, traceUrl }) {
  const [open, setOpen] = useState(true);
  const reasons = (candidate?.eval_reasons ?? []).filter((r) => r.reason);
  if (blind || (!reasons.length && !(traceUrl && candidate?.trace_id)))
    return null;
  return (
    <Stack spacing={0.5} data-testid="candidate-evidence">
      {reasons.length ? (
        <>
          <Link
            component="button"
            type="button"
            variant="caption"
            underline="hover"
            color="text.secondary"
            onClick={() => setOpen((o) => !o)}
            aria-expanded={open}
            sx={{
              alignSelf: "flex-start",
              display: "inline-flex",
              alignItems: "center",
              gap: 0.25,
            }}
          >
            <Iconify
              icon={open ? "mdi:chevron-down" : "mdi:chevron-right"}
              width={14}
            />
            Eval reasons ({reasons.length})
            {reasons[0]?.trials > 1
              ? ` · from try ${reasons[0].trial} of ${reasons[0].trials}`
              : ""}
          </Link>
          <Collapse in={open}>
            <Stack spacing={0.5}>
              {reasons.map((r) => (
                <Box
                  key={r.key}
                  data-testid="eval-reason"
                  sx={{ typography: "caption" }}
                >
                  <Box
                    component="span"
                    sx={{
                      fontWeight: 600,
                      color: r.pass ? "success.dark" : "error.main",
                    }}
                  >
                    {r.name} · {r.pass ? "Pass" : "Fail"}
                    {r.display ? ` (${r.display})` : ""}
                  </Box>
                  <Box component="span" sx={{ color: "text.secondary" }}>
                    {" — "}
                    {r.reason}
                  </Box>
                </Box>
              ))}
            </Stack>
          </Collapse>
        </>
      ) : null}
      {traceUrl && candidate?.trace_id ? (
        <Link
          component="button"
          type="button"
          variant="caption"
          underline="hover"
          onClick={() => window.open(traceUrl, "_blank", "noopener")}
          sx={{
            alignSelf: "flex-start",
            display: "inline-flex",
            alignItems: "center",
            gap: 0.5,
          }}
        >
          <Iconify icon="mdi:timeline-text-outline" width={14} />
          Open trace
          <Iconify icon="mdi:open-in-new" width={12} />
        </Link>
      ) : null}
    </Stack>
  );
}

CandidateEvidence.propTypes = {
  candidate: PropTypes.object.isRequired,
  blind: PropTypes.bool,
  traceUrl: PropTypes.string,
};
