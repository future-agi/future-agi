// Retained intake operations use the server's reviewed plan, never fresh bytes.
export const retainedPlanAcceptanceInput = (value) => ({
  plan_id: value.planId, expected_fingerprint: value.expectedFingerprint, item_key: value.itemKey,
  mode: value.mode, expected_head_version: value.expectedHeadVersion, expected_head_digest: value.expectedHeadDigest,
  rights_confirmed: value.rightsConfirmed,
  ...(Object.hasOwn(value, 'tags') ? { tags: value.tags } : {}),
});
export const checkpointRestoreInput = (value) => ({
  checkpoint_id: value.checkpointId, revision: value.revision, expected_digest: value.expectedDigest,
});
