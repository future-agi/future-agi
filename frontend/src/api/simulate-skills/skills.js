import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import {
  simulateApiSkillsAttach,
  simulateApiSkillsAttached,
  simulateApiSkillsCreate,
  simulateApiSkillsCreateVersion,
  simulateApiSkillsDetach,
  simulateApiSkillsInCode,
  simulateApiSkillsInCodeCopyToLibrary,
  simulateApiSkillsInCodeInCodeFiles,
  simulateApiSkillsList,
  simulateApiSkillsPromote,
  simulateApiSkillsRead,
  simulateApiSkillsStartTest,
  simulateApiSkillsTests,
  simulateApiSkillsVersionsVersionFiles,
} from "src/generated/api-contracts/api";

const dataOf = (response) => response?.data;

export const skillKeys = {
  all: ["simulate-skills"],
  list: (params) => ["simulate-skills", "list", params],
  enabled: ["simulate-skills", "enabled"],
  detail: (id) => ["simulate-skills", "detail", id],
  files: (id, version) => ["simulate-skills", "files", id, version],
  tests: (id) => ["simulate-skills", "tests", id],
  attached: (agentId) => ["simulate-skills", "attached", agentId],
  inCode: (agentId) => ["simulate-skills", "in-code", agentId],
  inCodeFiles: (agentId, id) => [
    "simulate-skills",
    "in-code-files",
    agentId,
    id,
  ],
};

export const isSkillsDisabled = (error) =>
  error?.statusCode === 404 && error?.error === "skills_disabled";

export function useSkillsEnabled() {
  const query = useQuery({
    queryKey: skillKeys.enabled,
    queryFn: async () => {
      try {
        await simulateApiSkillsList({ page: 1, limit: 1 });
        return true;
      } catch (error) {
        if (isSkillsDisabled(error)) return false;
        throw error;
      }
    },
    staleTime: 1000 * 60 * 10,
    retry: false,
  });
  return query.data === true;
}

export function useSkillsList(params) {
  return useQuery({
    queryKey: skillKeys.list(params),
    queryFn: async () => dataOf(await simulateApiSkillsList(params)),
    placeholderData: (previous) => previous,
    retry: (count, error) => !isSkillsDisabled(error) && count < 2,
  });
}

export function useSkill(id) {
  return useQuery({
    queryKey: skillKeys.detail(id),
    queryFn: async () => dataOf(await simulateApiSkillsRead(id)),
    enabled: Boolean(id),
  });
}

export function useSkillVersionFiles(id, version) {
  return useQuery({
    queryKey: skillKeys.files(id, version),
    queryFn: async () =>
      dataOf(await simulateApiSkillsVersionsVersionFiles(id, String(version))),
    enabled: Boolean(id) && Boolean(version),
    staleTime: Infinity,
  });
}

const RUNNING_TEST_POLL_MS = 15000;

export function useSkillTests(id) {
  return useQuery({
    queryKey: skillKeys.tests(id),
    queryFn: async () => dataOf(await simulateApiSkillsTests(id)),
    enabled: Boolean(id),
    refetchInterval: (query) =>
      (query.state.data || []).some((test) => test.state === "running")
        ? RUNNING_TEST_POLL_MS
        : false,
  });
}

export function useAttachedSkills(agentId, { enabled = true } = {}) {
  return useQuery({
    queryKey: skillKeys.attached(agentId),
    queryFn: async () =>
      dataOf(await simulateApiSkillsAttached({ agent_definition_id: agentId })),
    enabled: Boolean(agentId) && enabled,
    retry: (count, error) => !isSkillsDisabled(error) && count < 2,
  });
}

export function useCodeSkills(agentId, { enabled = true } = {}) {
  return useQuery({
    queryKey: skillKeys.inCode(agentId),
    queryFn: async () =>
      dataOf(await simulateApiSkillsInCode({ agent_definition_id: agentId })),
    enabled: Boolean(agentId) && enabled,
    retry: (count, error) => !isSkillsDisabled(error) && count < 2,
  });
}

export function useCodeSkillFiles(agentId, skillVersionId) {
  return useQuery({
    queryKey: skillKeys.inCodeFiles(agentId, skillVersionId),
    queryFn: async () =>
      dataOf(
        await simulateApiSkillsInCodeInCodeFiles(skillVersionId, {
          agent_definition_id: agentId,
        }),
      ),
    enabled: Boolean(agentId) && Boolean(skillVersionId),
    staleTime: Infinity,
  });
}

function useSkillMutation(mutationFn, invalidate) {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn,
    onSuccess: (data, variables) =>
      Promise.all(
        invalidate(data, variables).map((queryKey) =>
          queryClient.invalidateQueries({ queryKey }),
        ),
      ),
  });
}

export const useCreateSkill = () =>
  useSkillMutation(
    async (body) => dataOf(await simulateApiSkillsCreate(body)),
    () => [skillKeys.all],
  );

export const useCreateSkillVersion = (id) =>
  useSkillMutation(
    async (body) => dataOf(await simulateApiSkillsCreateVersion(id, body)),
    () => [skillKeys.detail(id), ["simulate-skills", "list"]],
  );

export const usePromoteSkill = (id) =>
  useSkillMutation(
    async (body) => dataOf(await simulateApiSkillsPromote(id, body)),
    () => [skillKeys.detail(id), ["simulate-skills", "list"]],
  );

export const useAttachSkill = (id) =>
  useSkillMutation(
    async (body) => dataOf(await simulateApiSkillsAttach(id, body)),
    (_, body) => [
      skillKeys.attached(body.agent_definition_id),
      ["simulate-skills", "list"],
    ],
  );

export const useDetachSkill = (id) =>
  useSkillMutation(
    async (body) => dataOf(await simulateApiSkillsDetach(id, body)),
    (_, body) => [
      skillKeys.attached(body.agent_definition_id),
      ["simulate-skills", "list"],
    ],
  );

export const useStartSkillTest = (id) =>
  useSkillMutation(
    async (body) => dataOf(await simulateApiSkillsStartTest(id, body)),
    () => [skillKeys.tests(id), ["simulate-skills", "list"]],
  );

export const useCopyCodeSkill = (agentId) =>
  useSkillMutation(
    async (skillVersionId) =>
      dataOf(
        await simulateApiSkillsInCodeCopyToLibrary(skillVersionId, {
          agent_definition_id: agentId,
        }),
      ),
    () => [["simulate-skills", "list"]],
  );
