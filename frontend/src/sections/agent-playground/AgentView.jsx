import { useQuery, useQueryClient } from "@tanstack/react-query";
import React, { useEffect } from "react";
import { useNavigate } from "react-router";
import axios, { endpoints } from "src/utils/axios";
import { LoadingButton } from "@mui/lab";
import EmptyLayout from "src/components/EmptyLayout/EmptyLayout";
import CustomTooltip from "src/components/tooltip/CustomTooltip";
import AgentListView from "./AgentListView";
import useCanEditAgent from "./hooks/useCanEditAgent";
import {
  resetAgentListGridStore,
  useAgentPlaygroundStoreShallow,
} from "./store";
import { useCreateGraph } from "../../api/agent-playground/agent-playground";

export default function AgentView() {
  const { data, isLoading } = useQuery({
    queryKey: ["agent-playground", "graphs", { page: 1 }],
    queryFn: () =>
      axios.get(endpoints.agentPlayground.listGraphs, {
        params: { page_number: 1, page_size: 1 },
      }),
  });

  const hasData = data?.data?.result?.graphs?.length > 0;

  useEffect(() => {
    return () => {
      resetAgentListGridStore();
    };
  }, []);

  if (isLoading) return null;

  return hasData ? <AgentListView /> : <AgentEmptyState />;
}

function AgentEmptyState() {
  const navigate = useNavigate();
  const queryClient = useQueryClient();
  const { canCreate: canCreateAgent } = useCanEditAgent();
  const { setCurrentAgent } = useAgentPlaygroundStoreShallow((s) => ({
    setCurrentAgent: s.setCurrentAgent,
  }));

  const { mutate: createGraph, isPending } = useCreateGraph({
    navigate,
    setCurrentAgent,
    onSuccess: () => {
      queryClient.invalidateQueries({
        queryKey: ["agent-playground", "graphs"],
      });
    },
  });

  return (
    <EmptyLayout
      icon="/assets/icons/navbar/ic_agents.svg"
      title="Create your first agent"
      description="Break down complex tasks into sequential steps that build upon each other."
      action={
        <CustomTooltip
          show={!canCreateAgent}
          type=""
          size="small"
          title="You don't have permission to create agents."
          arrow
        >
          <span>
            <LoadingButton
              loading={isPending}
              onClick={() => createGraph()}
              disabled={!canCreateAgent}
              size="small"
              variant="contained"
              color="primary"
            >
              Start creating
            </LoadingButton>
          </span>
        </CustomTooltip>
      }
    />
  );
}
