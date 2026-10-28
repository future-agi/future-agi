import { useNavigate } from "react-router-dom";
import { useQueryClient } from "@tanstack/react-query";
import { useCreateGraph } from "src/api/agent-playground/agent-playground";
import { useAgentPlaygroundStoreShallow } from "../store";

/**
 * Starts a new Playground agent and opens it in the builder, as Create
 * Agents → Playground does. `{ create, isPending }`.
 */
export default function useCreatePlaygroundAgent() {
  const navigate = useNavigate();
  const queryClient = useQueryClient();
  const { setCurrentAgent } = useAgentPlaygroundStoreShallow((s) => ({
    setCurrentAgent: s.setCurrentAgent,
  }));
  const { mutate, isPending } = useCreateGraph({
    navigate,
    setCurrentAgent,
    onSuccess: () => {
      queryClient.invalidateQueries({
        queryKey: ["agent-playground", "graphs"],
      });
    },
  });
  return { create: () => mutate(), isPending };
}
