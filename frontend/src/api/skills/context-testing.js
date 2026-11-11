import axios from 'src/utils/axios';
import { apiPath } from 'src/api/contracts/api-surface';
import { skillV11Result, useSkillV11Query, useSkillV11Mutation } from './testing';
import { caseInput, mapSuite } from './v11-mappers';

const suiteEndpoint = (context_id) => apiPath('/model-hub/skills/contexts/{context_id}/tests/', { context_id });
export const contextSuiteParts = (id) => ['agent_context', id, 'suite'];
export function useContextTestSuite(contextId) {
  return useSkillV11Query({ parts: contextSuiteParts(contextId), url: () => suiteEndpoint(contextId), mapper: mapSuite, enabled: Boolean(contextId) });
}
export function useSaveContextTestSuite(contextId) {
  return useSkillV11Mutation({
    send: async ({ revision, cases }, config) => mapSuite(skillV11Result(await axios.put(suiteEndpoint(contextId), { revision, cases: cases.map(caseInput) }, config))),
    invalidate: () => [contextSuiteParts(contextId)],
    // Refresh the authoritative revision after a conflict or lost response. The
    // editor retains its original revision and edits; never retry/rebase silently.
    refreshOnError: () => [contextSuiteParts(contextId)],
  });
}
