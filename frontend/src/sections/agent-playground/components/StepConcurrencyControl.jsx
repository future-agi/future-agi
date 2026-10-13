import React, { useEffect, useState } from "react";
import { Stack, TextField, Typography } from "@mui/material";
import CustomTooltip from "src/components/tooltip/CustomTooltip";
import { enqueueSnackbar } from "src/components/snackbar";
import { useUpdateGraph } from "../../../api/agent-playground/agent-playground";
import {
  useAgentPlaygroundStoreShallow,
  useWorkflowRunStoreShallow,
} from "../store";
import useCanEditAgent from "../hooks/useCanEditAgent";
import {
  DEFAULT_MAX_CONCURRENT_NODES,
  MAX_CONCURRENT_NODES_ERROR,
} from "../utils/constants";

function parseConcurrency(raw) {
  if (raw === "" || raw === null || raw === undefined) {
    return null;
  }
  const parsed = Number(raw);
  if (!Number.isInteger(parsed) || parsed < 1) {
    return null;
  }
  return parsed;
}

export default function StepConcurrencyControl() {
  const { canEditAgent } = useCanEditAgent();
  const { currentAgent, setCurrentAgent } = useAgentPlaygroundStoreShallow(
    (s) => ({
      currentAgent: s.currentAgent,
      setCurrentAgent: s.setCurrentAgent,
    }),
  );
  const { isRunning } = useWorkflowRunStoreShallow((s) => ({
    isRunning: s.isRunning,
  }));
  const { mutate: updateGraph } = useUpdateGraph();

  const storedValue =
    currentAgent?.max_concurrent_nodes ?? DEFAULT_MAX_CONCURRENT_NODES;
  const [value, setValue] = useState(String(storedValue));

  useEffect(() => {
    setValue(String(storedValue));
  }, [storedValue]);

  if (!currentAgent?.id) {
    return null;
  }

  const disabled = !canEditAgent || isRunning;

  const persist = (nextValue) => {
    const parsed = parseConcurrency(nextValue);
    if (parsed == null) {
      enqueueSnackbar(MAX_CONCURRENT_NODES_ERROR, { variant: "error" });
      setValue(String(storedValue));
      return;
    }
    if (parsed === storedValue) {
      setValue(String(parsed));
      return;
    }

    const previous = storedValue;
    setCurrentAgent({ ...currentAgent, max_concurrent_nodes: parsed });
    setValue(String(parsed));
    updateGraph(
      { id: currentAgent.id, max_concurrent_nodes: parsed },
      {
        onError: () => {
          setCurrentAgent({ ...currentAgent, max_concurrent_nodes: previous });
          setValue(String(previous));
          enqueueSnackbar(MAX_CONCURRENT_NODES_ERROR, { variant: "error" });
        },
      },
    );
  };

  return (
    <CustomTooltip
      type=""
      size="small"
      title="How many nodes of this agent may run at the same time. Nested agents apply this limit at each level, not once across the whole run."
      arrow
    >
      <Stack
        direction="row"
        alignItems="center"
        gap={1}
        sx={{
          bgcolor: "background.paper",
          border: "1px solid",
          borderColor: "whiteScale.500",
          borderRadius: 1,
          px: 1.5,
          py: 0.5,
          height: 40.8,
        }}
      >
        <Typography
          typography="s2"
          fontWeight="fontWeightRegular"
          color="text.secondary"
          sx={{ whiteSpace: "nowrap" }}
        >
          Concurrent steps
        </Typography>
        <TextField
          type="number"
          size="small"
          disabled={disabled}
          value={value}
          onChange={(event) => setValue(event.target.value)}
          onBlur={() => persist(value)}
          onKeyDown={(event) => {
            if (event.key === "Enter") {
              event.preventDefault();
              event.target.blur();
            }
          }}
          inputProps={{
            min: 1,
            step: 1,
            "aria-label": "Concurrent steps",
          }}
          sx={{
            width: 64,
            "& .MuiInputBase-input": {
              py: 0.5,
              px: 0.75,
              textAlign: "center",
            },
          }}
        />
      </Stack>
    </CustomTooltip>
  );
}
