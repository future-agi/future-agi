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

// One artifact the runner produced during a stage. `kind` is a closed set from ALK —
// contract, environment, scenarios, simulation — and anything else falls back to raw JSON
// rather than rendering nothing, so a new kind is visible rather than silently dropped.
export default function StageOutput({ output }) {
  const data = output.data || {};
  return (
    <Accordion
      variant="outlined"
      // Scenarios can be long; the others are short enough to read at a glance.
      defaultExpanded={output.kind !== "scenarios"}
      disableGutters
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
              endIcon={<Iconify icon="solar:arrow-right-up-linear" width={16} />}
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
          </Stack>
        )}

        {output.kind === "environment" && (
          <Stack spacing={1}>
            {Boolean((data.services || []).length) && (
              <Stack direction="row" gap={0.75} flexWrap="wrap">
                {(data.services || []).map((service) => (
                  <Chip
                    key={service}
                    size="small"
                    variant="outlined"
                    label={service}
                  />
                ))}
              </Stack>
            )}
            <Typography variant="body2">
              {data.project || "Isolated run"} ·{" "}
              {data.managed ? "built by ALK" : "provided by the repository"}
            </Typography>
            {Object.entries(data.overrides || {}).map(([name, value]) => (
              <Typography key={name} variant="caption" color="text.secondary">
                {name} → {String(value)}
              </Typography>
            ))}
          </Stack>
        )}

        {output.kind === "scenarios" && (
          <Stack spacing={1}>
            {(Array.isArray(data) ? data : []).map((scenario) => (
              <Paper key={scenario.name} variant="outlined" sx={{ p: 1.25 }}>
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
        ) && (
          <Box
            component="pre"
            sx={{ m: 0, whiteSpace: "pre-wrap", fontSize: 12 }}
          >
            {JSON.stringify(data, null, 2)}
          </Box>
        )}
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
