import axios from 'src/utils/axios';
import {
  getModelHubSkillsContextsCredentialsCreateUrl,
  getModelHubSkillsContextsCredentialsDeleteUrl,
  getModelHubSkillsContextsCredentialsListUrl,
  modelHubSkillsContextsResolveList,
  modelHubSkillsContextsVersionsAccessCheckCreate,
  modelHubSkillsContextsVersionsFilesList,
  modelHubSkillsContextsVersionsManifestList,
} from 'src/generated/api-contracts/api';
import {
  contextCredentialInput, contextSelectorInput, mapConsumptionChunk, mapConsumptionManifest,
  mapContextAccess, mapContextCredential, mapIssuedContextCredential,
} from './context-mappers';
import { mapV11Page } from './v11-mappers';
import { idempotencyHeaders, skillV11Result, useSkillV11Mutation, useSkillV11Query, v11PageParams } from './testing';

// Reuse the existing scoped transport and key factory. Include the resource
// family in every key so a context never aliases a legacy skill/private run.
const credentialParts = ['agent_context', 'credentials'];

export function useContextCredentials(params = {}, { enabled = true } = {}) {
  return useSkillV11Query({
    parts: [...credentialParts, params], url: () => getModelHubSkillsContextsCredentialsListUrl(),
    params: v11PageParams(params), mapper: (value) => mapV11Page(value, mapContextCredential), enabled,
  });
}

export function useIssueContextCredential() {
  return useSkillV11Mutation({
    send: async ({ idempotencyKey, ...input }, config) => mapIssuedContextCredential(skillV11Result(await axios.post(
      getModelHubSkillsContextsCredentialsCreateUrl(), contextCredentialInput(input),
      { ...config, headers: { ...config.headers, ...idempotencyHeaders(idempotencyKey) } },
    ))),
    // Never put the one-time secret into a query key, query result or storage.
    // It is available only on this mutation result until the caller resets it.
    invalidate: () => [credentialParts],
  });
}

const uuidPattern = /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i;

function consumptionOptions(credential, signal, resolutionId) {
  if (typeof credential !== 'string' || !credential || /\s/.test(credential)) throw new Error('Supply an isolated Context credential.');
  if (resolutionId !== undefined && !uuidPattern.test(resolutionId)) throw new Error('Supply the original resolution ID for continuation.');
  return {
    signal, headers: {
      Authorization: `Context ${credential}`,
      // This credential has its own scope, independent of the browser workspace.
      'X-Organization-Id': null, 'X-Workspace-Id': null,
      ...(resolutionId === undefined ? {} : { 'X-Context-Resolution': resolutionId }),
    },
  };
}
function requireSnapshot(snapshot) {
  if (snapshot.kind !== 'agent_context' || !uuidPattern.test(snapshot.resourceId) ||
      !uuidPattern.test(snapshot.versionId) || !uuidPattern.test(snapshot.resolutionId) ||
      !/^sha256:[a-f0-9]{64}$/.test(snapshot.digest) ||
      !Number.isInteger(snapshot.version) || snapshot.version < 1) {
    throw new Error('Continue an exact resolved agent context, not a label.');
  }
}
function requireIdentity(value, snapshot) {
  if (value.versionId !== snapshot.versionId || value.digest !== snapshot.digest ||
      (Object.hasOwn(value, 'resourceId') && value.resourceId !== snapshot.resourceId)) {
    throw new Error('The immutable context response does not match the resolved snapshot.');
  }
  return value;
}
function consumptionResult(response, signal) {
  if (signal?.aborted) throw new DOMException('Context request aborted.', 'AbortError');
  return skillV11Result(response);
}

// Imperative credential-backed reads deliberately do not enter React Query's
// human cache. These are bounded pages/chunks, not a second SDK/materializer.
export async function resolveContext({ credential, name, selector, signal }) {
  const params = { name, ...(selector === undefined ? {} : contextSelectorInput(selector)) };
  const response = await modelHubSkillsContextsResolveList(params, consumptionOptions(credential, signal));
  const value = mapConsumptionManifest(consumptionResult(response, signal));
  requireSnapshot(value);
  return value;
}
export async function readContextManifestPage({ credential, snapshot, fileOffset = 0, fileLimit = 25, signal }) {
  requireSnapshot(snapshot);
  const response = await modelHubSkillsContextsVersionsManifestList(snapshot.resourceId, String(snapshot.version),
    { file_offset: fileOffset, file_limit: fileLimit }, consumptionOptions(credential, signal, snapshot.resolutionId));
  const value = requireIdentity(mapConsumptionManifest(consumptionResult(response, signal)), snapshot);
  if (value.kind !== 'agent_context' || value.resolutionId !== snapshot.resolutionId) throw new Error('The context resolution changed during continuation.');
  return value;
}
export async function readContextFileChunk({ credential, snapshot, path, offset = 0, maxBytes = 32768, signal }) {
  requireSnapshot(snapshot);
  const response = await modelHubSkillsContextsVersionsFilesList(snapshot.resourceId, String(snapshot.version),
    { path, offset, max_bytes: maxBytes }, consumptionOptions(credential, signal, snapshot.resolutionId));
  return requireIdentity(mapConsumptionChunk(consumptionResult(response, signal)), snapshot);
}
export async function checkContextAccess({ credential, snapshot, signal }) {
  requireSnapshot(snapshot);
  const response = await modelHubSkillsContextsVersionsAccessCheckCreate(snapshot.resourceId, String(snapshot.version), {},
    consumptionOptions(credential, signal, snapshot.resolutionId));
  return requireIdentity(mapContextAccess(consumptionResult(response, signal)), snapshot);
}

export function useRevokeContextCredential() {
  return useSkillV11Mutation({
    send: async ({ credentialId }, config) => {
      await axios.delete(getModelHubSkillsContextsCredentialsDeleteUrl(credentialId), config);
      return { credentialId };
    },
    invalidate: () => [credentialParts],
  });
}
