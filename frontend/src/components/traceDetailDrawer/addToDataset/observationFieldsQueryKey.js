export const getObservationFieldsQueryKey = ({ observeId, projectId } = {}) => [
  "observationFields",
  observeId ?? projectId ?? null,
];
