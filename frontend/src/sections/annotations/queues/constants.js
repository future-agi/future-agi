export const QUEUE_ROLES = {
  ANNOTATOR: "annotator",
  REVIEWER: "reviewer",
  MANAGER: "manager",
};

export const isQueueAnnotatorRole = (annotator) =>
  !annotator?.role || annotator.role === QUEUE_ROLES.ANNOTATOR;
