import { useQuery } from "@tanstack/react-query";
import { SIMULATE_ENVIRONMENTS_KEY } from "./environments";
import { PREBUILT_ENVIRONMENTS_FIXTURE } from "./_fixtures/prebuiltEnvironments";

export const prebuiltEnvironmentsQueryKey = () => [
  ...SIMULATE_ENVIRONMENTS_KEY,
  "prebuilt",
];

// Mocks TH-7962's templates endpoint behind a react-query surface so the browse
// page consumes the final shape today; swapping the queryFn to axios is a
// one-line change once the backend lands.
const cloneFixture = () => structuredClone(PREBUILT_ENVIRONMENTS_FIXTURE);

// TODO(TH-7962): swap to axios(endpoints.simulateEnvironments.templates)
export function usePrebuiltEnvironments() {
  return useQuery({
    queryKey: prebuiltEnvironmentsQueryKey(),
    queryFn: async () => cloneFixture(),
  });
}
