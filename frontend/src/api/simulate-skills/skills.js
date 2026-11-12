import { useQuery } from "@tanstack/react-query";
import {
  simulateApiAgentSkillsFiles,
  simulateApiAgentSkillsList,
} from "src/generated/api-contracts/api";

const dataOf = (response) => response?.data;

export const agentSkillKeys = {
  list: (agentId, versionId) => ["agent-skills", agentId, versionId],
  files: (agentId, versionId, skillVersionId) => [
    "agent-skills",
    "files",
    agentId,
    versionId,
    skillVersionId,
  ],
};

export const isSkillsDisabled = (error) =>
  error?.statusCode === 404 && error?.error === "skills_disabled";

const versionParams = (agentId, versionId) => ({
  agent_definition_id: agentId,
  ...(versionId ? { agent_version_id: versionId } : {}),
});

export function useAgentSkills(agentId, versionId) {
  return useQuery({
    queryKey: agentSkillKeys.list(agentId, versionId),
    queryFn: async () =>
      dataOf(
        await simulateApiAgentSkillsList(versionParams(agentId, versionId)),
      ),
    enabled: !!agentId,
    retry: (count, error) => !isSkillsDisabled(error) && count < 2,
  });
}

export function useAgentSkillFiles(agentId, versionId, skillVersionId) {
  return useQuery({
    queryKey: agentSkillKeys.files(agentId, versionId, skillVersionId),
    queryFn: async () =>
      dataOf(
        await simulateApiAgentSkillsFiles(
          skillVersionId,
          versionParams(agentId, versionId),
        ),
      ),
    enabled: !!agentId && !!skillVersionId,
  });
}
