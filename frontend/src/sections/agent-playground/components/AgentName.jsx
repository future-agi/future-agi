import React from "react";
import { useForm } from "react-hook-form";
import { useQueryClient } from "@tanstack/react-query";
import EditableText from "src/components/editable-text/EditableText";
import PropTypes from "prop-types";
import { useUpdateGraph } from "../../../api/agent-playground/agent-playground";
import { useAgentPlaygroundStoreShallow } from "../store";
import useCanEditAgent from "../hooks/useCanEditAgent";

export default function AgentName({ currentAgent }) {
  const queryClient = useQueryClient();
  const { canEditAgent } = useCanEditAgent();
  const { control, reset, handleSubmit } = useForm({
    defaultValues: {
      name: currentAgent?.name,
    },
  });
  const { mutate: updateGraph } = useUpdateGraph();
  const setCurrentAgent = useAgentPlaygroundStoreShallow((s) => {
    return s.setCurrentAgent;
  });

  const onSubmit = (data) => {
    const newName = data.name?.trim();

    // Nothing changed — no-op
    if (!newName || newName === currentAgent.name) {
      reset({ name: currentAgent.name });
      return;
    }

    const previousName = currentAgent.name;

    // Optimistic update: reflect the new name immediately
    setCurrentAgent({ ...currentAgent, name: newName });
    reset({ name: newName });

    const queryKey = ["agent-playground", "graph", currentAgent.id];

    // Update the raw axios response cache so `select` (mapGraphToAgent) picks up the new name
    const updateCache = (name) => {
      queryClient.setQueryData(queryKey, (old) => {
        if (!old?.data?.result) return old;
        return {
          ...old,
          data: {
            ...old.data,
            result: { ...old.data.result, name },
          },
        };
      });
    };

    updateCache(newName);

    updateGraph(
      { id: currentAgent.id, name: newName },
      {
        onError: () => {
          // Rollback store, form, and cache to the previous name
          setCurrentAgent({ ...currentAgent, name: previousName });
          reset({ name: previousName });
          updateCache(previousName);
        },
      },
    );
  };

  return (
    <EditableText
      control={control}
      fieldName="name"
      placeholder="Enter agent name"
      typographyProps={{
        typography: "m3",
        fontWeight: "fontWeightMedium",
        color: "text.primary",
        sx: {
          minWidth: "0",
          maxWidth: "150px",
          textOverflow: "ellipsis",
          overflow: "hidden",
          whiteSpace: "nowrap",
        },
      }}
      textFieldProps={{
        sx: {
          minWidth: "150px",
        },
        size: "small",
      }}
      onSubmit={handleSubmit(onSubmit)}
      reset={reset}
      readOnly={!canEditAgent}
    />
  );
}

AgentName.propTypes = {
  currentAgent: PropTypes.object.isRequired,
};
