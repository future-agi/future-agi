import PropTypes from "prop-types";
import { Stack, Typography } from "@mui/material";

import { useRunReadiness } from "src/api/simulate-environments/versions";
import {
  BOUNDARY_LABELS,
  CHECKLIST_LABELS,
  CHECKLIST_RESULTS,
  PREVIEW_COPY,
  STATUS_LABELS,
} from "./preview.constants";

// Each readiness row, the sub-agent checklist, the hidden-material row and each worker's boundaries, as served.
export function ReadinessView({ report }) {
  if (!report?.rows) return null;
  return (
    <Stack spacing={0.5} data-testid="readiness-rows">
      <Typography sx={{ typography: "s2", color: "text.primary" }}>
        {PREVIEW_COPY.readinessTitle} ·{" "}
        {PREVIEW_COPY.blockers(report.blockers ?? 0)}
      </Typography>
      {report.rows.map((row) => (
        <Stack key={row.id} spacing={0.25}>
          <Typography sx={{ typography: "s3", color: "text.primary" }}>
            {row.label}: {STATUS_LABELS[row.status] ?? row.status}
            {row.reason ? ` · ${row.reason}` : ""}
          </Typography>
          {row.id === "children" &&
            (row.evidence ?? []).map((item) => (
              <Typography
                key={item.check}
                sx={{ typography: "s3", color: "text.subtitle", pl: 2 }}
              >
                {CHECKLIST_LABELS[item.check] ?? item.check}:{" "}
                {CHECKLIST_RESULTS[item.result] ?? item.result}
              </Typography>
            ))}
        </Stack>
      ))}
      {(report.workers ?? []).length > 0 && (
        <Typography sx={{ typography: "s2", color: "text.primary", pt: 0.5 }}>
          {PREVIEW_COPY.workersTitle}
        </Typography>
      )}
      {(report.workers ?? []).map((worker) => (
        <Stack key={worker.worker_id} spacing={0.25}>
          <Typography sx={{ typography: "s3", color: "text.primary" }}>
            {worker.name}: {worker.status_label} · {worker.reason}
          </Typography>
          {worker.tools.map((tool) => (
            <Typography
              key={tool.tool}
              sx={{ typography: "s3", color: "text.subtitle", pl: 2 }}
            >
              {tool.tool}: {BOUNDARY_LABELS[tool.boundary] ?? tool.boundary}
            </Typography>
          ))}
        </Stack>
      ))}
    </Stack>
  );
}

ReadinessView.propTypes = { report: PropTypes.object };

export default function ReadinessRows({ envId, picks, enabled }) {
  const { data } = useRunReadiness(envId, picks, { enabled });
  return <ReadinessView report={data} />;
}

ReadinessRows.propTypes = {
  envId: PropTypes.string,
  picks: PropTypes.object,
  enabled: PropTypes.bool,
};
