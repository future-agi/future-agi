// Ownerless legacy preferences are intentionally never read or migrated.
export const savedViewStorageKeys = (userId, workspaceId, observeId, userDetailId) => {
  if (!userId || !workspaceId || !(observeId || userDetailId)) return { display: null, filters: null };
  const prefix = userDetailId ? "user" : "observe";
  const scope = `${userId}-${workspaceId}-${userDetailId ?? observeId}`;
  return { display: `${prefix}-display-${scope}`, filters: `${prefix}-filters-${scope}` };
};
