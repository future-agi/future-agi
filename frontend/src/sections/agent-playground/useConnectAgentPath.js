import { useConnectAvailability } from "src/api/agents/agentConnect";
import { connectAgentPath } from "./agentsTabs";

export default function useConnectAgentPath() {
  const availability = useConnectAvailability();
  return connectAgentPath(availability.data?.readingEnabled === true);
}
