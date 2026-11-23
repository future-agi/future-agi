import { useEffect } from "react";
import PropTypes from "prop-types";
import {
  Alert,
  Button,
  CircularProgress,
  Stack,
  Typography,
} from "@mui/material";
import { useQueryClient } from "@tanstack/react-query";
import {
  sourceGenerationPending,
  useEnvironmentSourceGeneration,
} from "src/api/simulate-environments/sourceImports";
import { draftsKey } from "src/api/simulate-environments/versions";
import { harnessEnvironmentKey } from "src/api/simulate-environments/environment";
import { harnessScenariosListKey } from "src/api/simulate-environments/scenariosHooks";

export default function SourceScenarioGeneration({ review, onDismiss }) {
  const queryClient = useQueryClient();
  const query = useEnvironmentSourceGeneration(
    review.id,
    review.receipt.scenario_generation,
  );
  const state = query.data?.state;
  const envId = review.scope.id;
  const pending = sourceGenerationPending(query.data);
  const scenarios = query.data?.generated_scenarios || [];
  const filenames = new Map(
    (review.preview?.sources || []).map(({ filename, pin }) => [
      pin.source_id,
      filename,
    ]),
  );

  useEffect(() => {
    if (state !== "completed") return;
    queryClient.invalidateQueries({ queryKey: draftsKey(envId) });
    queryClient.invalidateQueries({ queryKey: harnessEnvironmentKey(envId) });
    queryClient.invalidateQueries({ queryKey: harnessScenariosListKey(envId) });
  }, [envId, queryClient, state]);

  return (
    <Stack spacing={1}>
      {pending && (
        <Stack direction="row" alignItems="center" spacing={1} role="status">
          <CircularProgress size={16} />
          <Typography>
            {state === "queued"
              ? "Scenario generation is queued."
              : "Generating scenarios from the approved sources…"}
          </Typography>
        </Stack>
      )}
      {query.isError && (
        <Alert
          severity="error"
          action={
            <Button color="inherit" onClick={query.refetch}>
              Retry
            </Button>
          }
        >
          Could not read scenario generation progress.
        </Alert>
      )}
      {state === "failed" && (
        <Alert severity="error">
          {query.data.error?.message || "Scenario generation failed."}
        </Alert>
      )}
      {state === "stale" && (
        <Alert severity="warning">
          The Environment or scenario draft changed during generation. Prepare
          the sources again.
        </Alert>
      )}
      {state === "completed" && (
        <>
          <Typography variant="h6">Generated scenarios</Typography>
          <Typography color="text.secondary">
            Save creates a new Environment version with the reviewed sources and
            these scenarios.
          </Typography>
          {scenarios.map(({ scenario_key: key, name, citations = [] }) => (
            <Stack key={key} spacing={0.25}>
              <Typography fontWeight={600}>{name}</Typography>
              {citations.map(({ source_id: id, page, row }, index) => (
                <Typography
                  key={`${id}-${page}-${row}-${index}`}
                  variant="body2"
                  color="text.secondary"
                >
                  {[
                    filenames.get(id) || id,
                    page != null && `Page ${page}`,
                    row != null && `Row ${row}`,
                  ]
                    .filter(Boolean)
                    .join(" · ")}
                </Typography>
              ))}
            </Stack>
          ))}
        </>
      )}
      {onDismiss && !pending && (
        <Button sx={{ alignSelf: "start" }} onClick={onDismiss}>
          Close
        </Button>
      )}
    </Stack>
  );
}

SourceScenarioGeneration.propTypes = {
  review: PropTypes.object.isRequired,
  onDismiss: PropTypes.func,
};
