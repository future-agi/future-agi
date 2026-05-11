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
  if (Array.isArray(member?.roles) && member.roles.length > 0) {
    return member.roles;
  }
  return member?.role ? [member.role] : [QUEUE_ROLES.ANNOTATOR];
};

export const hasQueueRole = (member, role) =>
  queueRoleList(member).includes(role);

export const isQueueAnnotatorRole = (annotator) =>
  hasQueueRole(annotator, QUEUE_ROLES.ANNOTATOR);
