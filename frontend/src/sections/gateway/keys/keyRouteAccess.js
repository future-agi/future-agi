// The model ACL checks the requested ID, not an inferred bare-model alias.
// Unknown/malformed restrictions must not become an unrestricted request.
export const permits = (list, id) =>
  Array.isArray(list) && (!list.length || list.includes(id));
