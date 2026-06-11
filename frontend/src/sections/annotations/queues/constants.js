export const QUEUE_ROLES = {
  ANNOTATOR: "annotator",
  REVIEWER: "reviewer",
  MANAGER: "manager",
};

export const ROLE_PRIORITY = [
  QUEUE_ROLES.MANAGER,
  QUEUE_ROLES.REVIEWER,
  QUEUE_ROLES.ANNOTATOR,
];

export const queueRoleList = (member) => {
  if (!member) return [];
  if (Array.isArray(member?.roles) && member.roles.length > 0) {
    return member.roles;
  }
  return member?.role ? [member.role] : [QUEUE_ROLES.ANNOTATOR];
};

export const hasQueueRole = (member, role) =>
  queueRoleList(member).includes(role);

export const isQueueAnnotatorRole = (annotator) =>
  hasQueueRole(annotator, QUEUE_ROLES.ANNOTATOR);

export const queueViewerMembership = (queue) => {
  const viewerRoles =
    Array.isArray(queue?.viewer_roles) && queue.viewer_roles.length > 0
      ? queue.viewer_roles
      : queue?.viewer_role
        ? [queue.viewer_role]
        : [];

  if (viewerRoles.length === 0) return null;

  return {
    role: queue?.viewer_role || viewerRoles[0],
    roles: viewerRoles,
  };
};

export const canViewerAddItemsToQueue = (queue) =>
  hasQueueRole(queueViewerMembership(queue), QUEUE_ROLES.MANAGER);
