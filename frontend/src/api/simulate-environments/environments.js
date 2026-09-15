import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { MY_ENVIRONMENTS_FIXTURE } from "./_fixtures/myEnvironments";

export const SIMULATE_ENVIRONMENTS_KEY = ["simulate-environments"];
export const myEnvironmentsQueryKey = () => [
  ...SIMULATE_ENVIRONMENTS_KEY,
  "list",
];

// The hooks below mock TH-7962's endpoints behind a react-query surface so the
// UI consumes the final shape today; swapping each queryFn/mutationFn to axios
// is a one-file change once the backend lands.
const cloneFixture = () => structuredClone(MY_ENVIRONMENTS_FIXTURE);

// TODO(TH-7962): swap queryFn to axios.get(endpoints.simulateEnvironments.list)
export function useMyEnvironments() {
  return useQuery({
    queryKey: myEnvironmentsQueryKey(),
    queryFn: async () => cloneFixture(),
  });
}

// TODO(TH-7962): axios.delete(endpoints.simulateEnvironments.detail(envId))
export function useDeleteEnvironment() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: async (envId) => ({ id: envId }),
    onSuccess: ({ id }) => {
      queryClient.setQueryData(myEnvironmentsQueryKey(), (rows) =>
        (rows || []).filter((r) => r.id !== id),
      );
    },
  });
}

// TODO(TH-7962): axios.post(endpoints.simulateEnvironments.build, source)
// Return only the server-minted id — never echo the raw source back, since it
// can carry apiKey/envText secrets that would then sit in the mutation cache.
export function useBuildEnvironment() {
  return useMutation({
    mutationFn: async () => ({
      envId: `env-${Date.now().toString(36)}`,
    }),
  });
}

// TODO(TH-7962): POST to /secret-files (uploadHarnessSecretFile) — never inline
// file contents. The UI holds only the returned reference, not the bytes.
export function useUploadSecretFile() {
  return useMutation({
    mutationFn: async ({ file }) => ({
      secret_ref: `sref-${Math.random().toString(36).slice(2)}`,
      name: file.name,
      size: file.size,
    }),
  });
}

// TODO(TH-7962): axios.post(endpoints.simulateEnvironments.run(envId))
export function useRunSimulation() {
  return useMutation({
    mutationFn: async (envId) => ({
      envId,
      runId: `run-${Date.now().toString(36)}`,
    }),
  });
}
