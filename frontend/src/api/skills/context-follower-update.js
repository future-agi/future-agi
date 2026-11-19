import { useRef } from 'react';
import { useQueryClient } from '@tanstack/react-query';
import axios from 'src/utils/axios';
import { contextEndpoints, contextParts, mapContextDraft, mapDependencyPreview, mapDraftOperation, requireAcknowledgedDraft } from './context-authoring';
import { contextDraftStartInput, contextDependencyPreviewInput, contextDependencyApplyInput } from './context-mappers';
import { skillV11Result, useSkillV11Scope, idempotencyHeaders } from './testing';
import { skillsKeys } from './keys';

// One reviewed decision over many follower contexts. Every context keeps its own
// draft, preview fingerprint, apply receipt and failure; nothing is shared except the
// reviewer's single acknowledgement of the exact per-context results shown.
const errorCode = (error) => error?.code || error?.response?.data?.code || 'unknown';
const failure = (target, error) => ({ target, status: 'error', code: errorCode(error), httpStatus: error?.statusCode ?? error?.response?.status ?? null });

export function useFollowerUpdates(skillId) {
  const access = useSkillV11Scope();
  const client = useQueryClient();
  const keys = useRef(new Map());
  const operationKey = (id) => { if (!keys.current.has(id)) keys.current.set(id, crypto.randomUUID()); return keys.current.get(id); };
  const config = () => ({ headers: access.headers });
  const live = () => access.current.current === access.identity;
  const refresh = (contextId) => client.invalidateQueries({ queryKey: skillsKeys.v11(access.scope, ...contextParts(contextId)) });

  // Resolve the draft whose content origin is the viewed release; never replace a different-origin draft.
  const draftFor = async (target) => {
    try {
      const existing = mapContextDraft(skillV11Result(await axios.get(contextEndpoints.draft(target.contextId), config())));
      if (existing.baseVersion !== target.version) return { conflict: 'different_origin' };
      return { draft: existing, reusedDraft: true };
    } catch (error) {
      if (errorCode(error) !== 'draft_not_found') throw error;
    }
    const started = mapContextDraft(skillV11Result(await axios.post(contextEndpoints.start(target.contextId), contextDraftStartInput({ revision: 0, fromVersion: target.version }), { headers: { ...access.headers, ...idempotencyHeaders(operationKey(`start:${target.contextId}:${target.version}`)) } })));
    refresh(target.contextId);
    return { draft: started, reusedDraft: false };
  };

  const prepareOne = async (target) => {
    try {
      const resolved = await draftFor(target);
      if (resolved.conflict) return { target, status: 'error', code: resolved.conflict };
      const { draft } = resolved;
      const links = draft.contextSpec.links;
      const preview = mapDependencyPreview(skillV11Result(await axios.post(contextEndpoints.previewDependencies(target.contextId), contextDependencyPreviewInput({ revision: draft.revision, links, refreshSkillIds: [skillId] }), config())));
      return { target, status: 'ready', links, plan: preview, reusedDraft: resolved.reusedDraft };
    } catch (error) { return failure(target, error); }
  };

  const applyOne = async (item) => {
    const { target, plan, links } = item;
    try {
      const body = contextDependencyApplyInput({ revision: plan.revision, links, refreshSkillIds: [skillId], expectedFingerprint: plan.fingerprint });
      const response = skillV11Result(await axios.post(contextEndpoints.applyDependencies(target.contextId), body, { headers: { ...access.headers, ...idempotencyHeaders(operationKey(`apply:${target.contextId}:${plan.fingerprint}`)) } }));
      let draft;
      if (response.files) draft = mapContextDraft(response);
      else {
        const receipt = mapDraftOperation(response);
        if (receipt.contextId !== target.contextId) throw new Error('The dependency receipt belongs to another context.');
        draft = requireAcknowledgedDraft(receipt, mapContextDraft(skillV11Result(await axios.get(contextEndpoints.draft(target.contextId), config()))));
      }
      refresh(target.contextId);
      return { target, status: 'applied', revision: draft.revision, digest: draft.digest, lock: draft.resolvedLock, reusedDraft: item.reusedDraft };
    } catch (error) { refresh(target.contextId); return failure(target, error); }
  };

  return {
    prepare: async (targets) => { const out = []; for (const target of targets) { if (!live()) break; out.push(await prepareOne(target)); } return out; },
    apply: async (items) => { const out = []; for (const item of items) { if (!live()) break; out.push(await applyOne(item)); } return out; },
  };
}
