import { useMutation, useQueryClient } from "@tanstack/react-query";
import axios, { endpoints } from "src/utils/axios";
import { workspacesListKey } from "./list";

// Every screen that creates a workspace goes through here, so the switcher's
// list (cached with staleTime: Infinity) always picks up the new one.
export function useCreateWorkspace({ onSuccess, onError } = {}) {
  const queryClient = useQueryClient();

  return useMutation({
    mutationFn: (payload) => axios.post(endpoints.workspaces.create, payload),
    onSuccess: (...args) => {
      queryClient.invalidateQueries({ queryKey: workspacesListKey });
      onSuccess?.(...args);
    },
    onError,
  });
}
