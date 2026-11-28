import axios from 'src/utils/axios';
import { apiPath } from 'src/api/contracts/api-surface';
import { useSkillV11Query, skillV11Result } from './testing';
import { contextEndpoints } from './context-authoring';
import { endpoints } from './endpoints';

// Display-only enrichment through existing authorized reads. Never derive an
// approver from the assigned reviewer or executor, or grant authority from a
// directory entry. The query helper fences these reads to the current scope.
export function useReviewMembers() {
  return useSkillV11Query({ parts: ['review-display-members'], load: async (config) => {
    const people = [];
    for (let page = 1; page <= 20; page += 1) {
      const value = skillV11Result(await axios.get(apiPath('/accounts/organization/members/'), { ...config, params: { page, limit: 100 } }));
      people.push(...(value.results || []).filter((person) => person.type !== 'invite' && person.status !== 'Pending'));
      if (!value.total_pages || page >= value.total_pages) break;
    }
    return people;
  } });
}

export function useReviewSubject(subjectId, kind) {
  return useSkillV11Query({ parts: ['review-display-subject', subjectId, kind], enabled: Boolean(subjectId && kind), load: async (config) => {
    const resource = skillV11Result(await axios.get(kind === 'agent_context' ? contextEndpoints.detail(subjectId) : endpoints.detail(subjectId), config));
    return resource.context || resource;
  } });
}

export function enrichReviewDisplay(request, receipt, members = [], subject) {
  const names = new Map(members.filter((person) => person.id).map((person) => [person.id, person.name || person.email]));
  const enrichedRequest = request && {
    ...request,
    name: request.name || subject?.name,
    authorName: request.authorName || names.get(request.authorId),
    recipients: request.recipients?.map((person) => ({ ...person, name: person.name || names.get(person.id) })),
  };
  const sameReceipt = receipt && request && receipt.requestId === request.id && receipt.revision === request.revision;
  return {
    request: enrichedRequest,
    receipt: receipt && {
      ...receipt,
      executedByName: receipt.executedByName || names.get(receipt.executedBy),
      // Only the matching immutable request may supply missing receipt metadata.
      ...(sameReceipt ? {
        name: receipt.name || enrichedRequest.name,
        subjectId: receipt.subjectId || request.subjectId,
        subjectKind: receipt.subjectKind || request.subjectKind,
        previousVersion: receipt.previousVersion !== undefined ? receipt.previousVersion : request.packet?.transition.expectedVersion,
      } : {}),
    },
  };
}
