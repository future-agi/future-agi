// Agents… (W9-SCOPE §5 A7): attach datasets to the Agent Playground graphs
// they test. One dataset: a checkbox per graph (name and active version)
// and **From this dataset's runs** with **Attach**. Several: per graph
// `Attach to all 3` / `Detach from all 3`. One PUT per changed dataset.
import React, { useEffect, useMemo, useState } from "react";
import PropTypes from "prop-types";
import {
  Box,
  Button,
  Checkbox,
  Dialog,
  DialogActions,
  DialogContent,
  DialogTitle,
  FormControlLabel,
  Stack,
  Typography,
} from "@mui/material";
import {
  sequenceToast,
  useAgentGraphOptions,
  useSaveDatasetOrg,
} from "src/api/develop/dataset-org";
import {
  agentChanges,
  agentChangesProblem,
  agentsCopy,
  attachState,
  suggestedGraphs,
} from "./agentsModel";

const versionText = (g) =>
  g.active_version_number ? ` · v${g.active_version_number}` : "";

export default function AgentsDialog({ open, datasets, onClose, onDone }) {
  const n = datasets?.length ?? 0;
  const single = n === 1;
  const graphs = useAgentGraphOptions({ enabled: open });
  const [choices, setChoices] = useState({});
  const [error, setError] = useState(null);
  const save = useSaveDatasetOrg();
  useEffect(() => {
    if (open) {
      setChoices({});
      setError(null);
    }
  }, [open]);
  const effective = (g) => {
    if (choices[g] === "attach") return "all";
    if (choices[g] === "detach") return "none";
    return attachState(datasets, g);
  };
  const suggested = useMemo(
    () => suggestedGraphs(datasets).filter((g) => effective(g.id) !== "all"),
    // eslint-disable-next-line react-hooks/exhaustive-deps
    [datasets, choices],
  );
  const set = (g, c) => setChoices((cur) => ({ ...cur, [g]: c }));
  const submit = async () => {
    const changes = agentChanges(datasets, choices);
    if (!changes.length) return onClose();
    const problem = agentChangesProblem(
      changes,
      (graphs.data ?? []).map((g) => g.id),
    );
    if (problem) return setError(problem);
    const byId = Object.fromEntries(changes.map((c) => [c.id, c.next]));
    const result = await save.mutateAsync({
      datasets: changes,
      patchFor: (d) => ({ agent_ids: byId[d.id] }),
    });
    const toast = sequenceToast(result, "Updated agents on");
    if (!result.saved.length) {
      setError(result.failed[0]?.error ?? toast.message);
      return undefined;
    }
    onDone?.(toast);
    onClose();
    return undefined;
  };
  return (
    <Dialog open={open} onClose={onClose} fullWidth maxWidth="sm">
      <DialogTitle sx={{ pb: 1 }}>
        <Typography typography="m3" fontWeight="fontWeightSemiBold">
          {single ? `Agents of ${datasets[0].name}` : `Agents of ${n} datasets`}
        </Typography>
        <Typography typography="s2" color="text.secondary">
          {agentsCopy(n)}
        </Typography>
      </DialogTitle>
      <DialogContent>
        {graphs.isLoading && (
          <Typography typography="s2" color="text.secondary">
            Loading agents…
          </Typography>
        )}
        {graphs.isError && (
          <Typography typography="s2" color="error.main">
            Could not load agents. Close and try again.
          </Typography>
        )}
        {graphs.data && !graphs.data.length && (
          <Typography typography="s2" color="text.secondary">
            This workspace has no agents.
          </Typography>
        )}
        {suggested.length > 0 && (
          <Box sx={{ mb: 2 }} data-testid="agents-from-runs">
            <Typography
              typography="s1"
              fontWeight="fontWeightMedium"
              sx={{ mb: 0.5 }}
            >
              {single
                ? "From this dataset's runs"
                : "From these datasets' runs"}
            </Typography>
            {suggested.map((g) => (
              <Stack
                key={g.id}
                direction="row"
                alignItems="center"
                justifyContent="space-between"
              >
                <Typography typography="s1">{g.name}</Typography>
                <Button size="small" onClick={() => set(g.id, "attach")}>
                  Attach
                </Button>
              </Stack>
            ))}
          </Box>
        )}
        <Stack gap={0.5} role="group" aria-label="Agents">
          {(graphs.data ?? []).map((g) => {
            const state = effective(g.id);
            if (single)
              return (
                <FormControlLabel
                  key={g.id}
                  control={
                    <Checkbox
                      size="small"
                      checked={state === "all"}
                      onChange={(e) =>
                        set(g.id, e.target.checked ? "attach" : "detach")
                      }
                    />
                  }
                  label={`${g.name}${versionText(g)}`}
                  componentsProps={{ typography: { typography: "s1" } }}
                />
              );
            return (
              <Stack key={g.id} direction="row" alignItems="center" gap={1}>
                <Typography
                  typography="s1"
                  sx={{ flex: 1, minWidth: 0 }}
                  noWrap
                >
                  {`${g.name}${versionText(g)}`}
                  <Typography
                    component="span"
                    typography="s3"
                    color="text.secondary"
                  >
                    {state === "all"
                      ? ` · on all ${n}`
                      : state === "some"
                        ? ` · on ${datasets.filter((d) => (d.agent_ids ?? []).includes(g.id)).length} of ${n}`
                        : ""}
                  </Typography>
                </Typography>
                <Button
                  size="small"
                  disabled={state === "all"}
                  onClick={() => set(g.id, "attach")}
                >
                  {`Attach to all ${n}`}
                </Button>
                <Button
                  size="small"
                  color="inherit"
                  disabled={state === "none"}
                  onClick={() => set(g.id, "detach")}
                >
                  {`Detach from all ${n}`}
                </Button>
              </Stack>
            );
          })}
        </Stack>
        {error && (
          <Typography
            typography="s2"
            color="error.main"
            sx={{ mt: 1.5 }}
            role="alert"
          >
            {error}
          </Typography>
        )}
      </DialogContent>
      <DialogActions sx={{ px: 3, pb: 2.5 }}>
        <Button variant="outlined" color="inherit" onClick={onClose}>
          Cancel
        </Button>
        <Button
          variant="contained"
          onClick={submit}
          disabled={save.isPending || !graphs.data}
        >
          Save
        </Button>
      </DialogActions>
    </Dialog>
  );
}

AgentsDialog.propTypes = {
  open: PropTypes.bool.isRequired,
  datasets: PropTypes.arrayOf(
    PropTypes.shape({
      id: PropTypes.string,
      name: PropTypes.string,
      agent_ids: PropTypes.arrayOf(PropTypes.string),
      suggested_agents: PropTypes.array,
    }),
  ).isRequired,
  onClose: PropTypes.func.isRequired,
  onDone: PropTypes.func,
};
