import {
  Accordion,
  AccordionDetails,
  AccordionSummary,
  Box,
  Button,
  Chip,
  Paper,
  Stack,
  Typography,
} from "@mui/material";
import PropTypes from "prop-types";

import Iconify from "src/components/iconify";

import { readable } from "./harnessShared";

function RawDetails({ data }) {
  return (
    <Box
      component="details"
      sx={{
        border: 1,
        borderColor: "divider",
        borderRadius: 1,
        mt: 0.5,
        "&[open]": { pb: 1 },
      }}
    >
      <Box
        component="summary"
        sx={{
          color: "text.secondary",
          cursor: "pointer",
          fontSize: 12,
          fontWeight: 600,
          px: 1.25,
          py: 1,
          userSelect: "none",
        }}
      >
        View raw details
      </Box>
      <Box
        component="pre"
        sx={{
          color: "text.secondary",
          fontSize: 12,
          m: 0,
          maxHeight: 420,
          overflow: "auto",
          px: 1.25,
          whiteSpace: "pre-wrap",
        }}
      >
        {JSON.stringify(data, null, 2)}
      </Box>
    </Box>
  );
}

RawDetails.propTypes = {
  data: PropTypes.oneOfType([PropTypes.object, PropTypes.array]).isRequired,
};

// One artifact the runner produced during a stage. `kind` is a closed set from ALK —
// contract, environment, scenarios, simulation — and anything else falls back to raw JSON
// rather than rendering nothing, so a new kind is visible rather than silently dropped.
export default function StageOutput({ output }) {
  const data = output.data || {};
  // The environment payload nests its detail under bundle_manifest and metadata,
  // so read those rather than the flat fields the earlier shape used.
  const manifest = data.bundle_manifest || {};
  const processes = manifest.processes || [];
  const readinessChecks = (manifest.readiness || [])
    .map((check) => check.capability)
    .filter(Boolean);
  const fileCount = (manifest.files || []).length;
  const capabilities = Object.entries(manifest.capabilities || {});
  const seedStores = (manifest.seed || {}).stores || [];
  const adoptedFiles = (manifest.provenance || {}).adopted_files || [];
  return (
    <Accordion
      variant="outlined"
      // Scenarios can be long; the others are short enough to read at a glance.
      defaultExpanded={output.kind !== "scenarios"}
      disableGutters
      // The theme leaves a collapsed accordion transparent and paints it only once
      // expanded, so the two states sit on different surfaces. Pin both to the darker
      // surface (default is #0a0a0a against paper's #111111 in dark mode).
      sx={{
        bgcolor: "background.default",
        "&.Mui-expanded": { bgcolor: "background.default" },
      }}
    >
      <AccordionSummary
        expandIcon={<Iconify icon="eva:arrow-ios-downward-fill" width={18} />}
      >
        <Box sx={{ minWidth: 0 }}>
          <Typography variant="subtitle2">{output.title}</Typography>
          {output.summary && (
            <Typography variant="caption" color="text.secondary">
              {output.summary}
            </Typography>
          )}
        </Box>
      </AccordionSummary>

      <AccordionDetails>
        {output.kind === "simulation" && (
          <Stack spacing={1.25} alignItems="flex-start">
            <Typography variant="body2" color="text.secondary">
              Open the simulation to inspect calls, transcripts, recordings,
              tool activity and evaluations.
            </Typography>
            <Button
              component="a"
              href={data.url}
              target="_blank"
              rel="noopener noreferrer"
              variant="outlined"
              color="inherit"
              size="small"
              endIcon={
                <Iconify icon="solar:arrow-right-up-linear" width={16} />
              }
            >
              Open simulation
            </Button>
          </Stack>
        )}

        {output.kind === "contract" && (
          <Stack spacing={1}>
            <Typography variant="body2">{data.one_liner}</Typography>
            <Typography variant="caption" color="text.secondary">
              Talks over {data.modality || "an unknown transport"} · runs{" "}
              {typeof data.runtime === "string"
                ? data.runtime
                : data.runtime?.entrypoint || "a discovered entrypoint"}
            </Typography>
            {Boolean((data.tools || []).length) && (
              <Stack direction="row" gap={0.75} flexWrap="wrap">
                {(data.tools || []).map((tool) => (
                  <Chip
                    key={tool.name || String(tool)}
                    size="small"
                    variant="outlined"
                    label={tool.name || String(tool)}
                  />
                ))}
              </Stack>
            )}
            {(data.hard_constraints || []).map((constraint) => (
              <Typography key={String(constraint)} variant="body2">
                • {String(constraint)}
              </Typography>
            ))}
            <RawDetails data={data} />
          </Stack>
        )}

        {output.kind === "environment" && (
          <Stack spacing={1}>
            {Boolean(processes.length) && (
              <Stack direction="row" gap={0.75} flexWrap="wrap">
                {processes.map((process) => (
                  <Chip
                    key={process.name}
                    size="small"
                    variant="outlined"
                    label={
                      process.engine
                        ? `${process.name} · ${process.engine}${process.version ? ` ${process.version}` : ""}`
                        : process.fixed_port
                          ? `${process.name} · :${process.fixed_port}`
                          : process.name
                    }
                  />
                ))}
              </Stack>
            )}
            {Boolean(data.source && data.source.repository) && (
              <Typography variant="body2">
                {data.source.repository}
                {data.source.commit_sha
                  ? ` · ${String(data.source.commit_sha).slice(0, 7)}`
                  : ""}
              </Typography>
            )}
            <Typography variant="caption" color="text.secondary">
              {data.runtime && data.runtime.kind
                ? `Runs as ${data.runtime.kind}`
                : "Isolated run"}
              {data.runtime && data.runtime.document
                ? ` · ${data.runtime.document}`
                : ""}{" "}
              ·{" "}
              {data.metadata && data.metadata.managed
                ? "built by the harness"
                : "provided by the repository"}
            </Typography>
            {Boolean(readinessChecks.length) && (
              <Typography variant="caption" color="text.secondary">
                Waits for {readinessChecks.join(", ")}
              </Typography>
            )}
            {Boolean(fileCount) && (
              <Typography variant="caption" color="text.secondary">
                {fileCount} files in the bundle
              </Typography>
            )}
            {Boolean(capabilities.length) && (
              <Stack spacing={0.25}>
                <Typography variant="caption" color="text.secondary">
                  Capabilities the agent can reach
                </Typography>
                {capabilities.map(([name, capability]) => (
                  <Typography key={name} variant="caption" color="text.secondary">
                    {name} → {capability.service} ({capability.protocol}
                    {capability.container_port ? `:${capability.container_port}` : ""})
                    {capability.configuration_name
                      ? ` as ${capability.configuration_name}`
                      : ""}
                  </Typography>
                ))}
              </Stack>
            )}
            {seedStores.map((store, index) => (
              <Typography
                key={store.capability || index}
                variant="caption"
                color="text.secondary"
              >
                Seeds {store.capability} from{" "}
                {(store.migrations || []).join(", ") || "no migration file"}
                {store.baseline?.strategy ? ` · ${store.baseline.strategy}` : ""}
              </Typography>
            ))}
            {processes.map((process) => (
              <Typography
                key={`detail-${process.name}`}
                variant="caption"
                color="text.secondary"
              >
                {process.name}
                {process.user ? ` runs as ${process.user}` : ""}
                {(process.depends_on || []).length
                  ? ` · after ${(process.depends_on || []).join(", ")}`
                  : ""}
                {(process.run_command || []).length
                  ? ` · ${(process.run_command || []).join(" ")}`
                  : ""}
              </Typography>
            ))}
            {Boolean(adoptedFiles.length) && (
              <Typography variant="caption" color="text.secondary">
                Adopted from the repository: {adoptedFiles.join(", ")}
              </Typography>
            )}
            {Object.entries(data.overrides || {}).map(([name, value]) => (
              <Typography key={name} variant="caption" color="text.secondary">
                {name} → {String(value)}
              </Typography>
            ))}
            <RawDetails data={data} />
          </Stack>
        )}

        {output.kind === "scenarios" && (
          <Stack spacing={1}>
            {(Array.isArray(data) ? data : []).map((scenario) => (
              <Paper
                key={scenario.name}
                variant="outlined"
                sx={{ p: 1.25, bgcolor: "background.default" }}
              >
                <Typography variant="subtitle2">
                  {readable(scenario.name)}
                </Typography>
                <Typography variant="body2">{scenario.instruction}</Typography>
                <Typography variant="caption" color="text.secondary">
                  {scenario.use_case || "Generated test case"}
                </Typography>
              </Paper>
            ))}
          </Stack>
        )}

        {!["contract", "environment", "scenarios", "simulation"].includes(
          output.kind,
        ) && <RawDetails data={data} />}
      </AccordionDetails>
    </Accordion>
  );
}

StageOutput.propTypes = {
  output: PropTypes.shape({
    data: PropTypes.oneOfType([PropTypes.object, PropTypes.array]),
    kind: PropTypes.string.isRequired,
    summary: PropTypes.string,
    title: PropTypes.string.isRequired,
  }).isRequired,
};
