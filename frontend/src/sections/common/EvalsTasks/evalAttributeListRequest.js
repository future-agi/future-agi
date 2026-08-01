import axios, { endpoints } from "src/utils/axios";

export const getEvalAttributeListQueryKey = (projectId, rowType) => [
  "eval-attributes",
  projectId,
  rowType,
];

export const fetchEvalAttributeList = (projectId, rowType) =>
  axios.get(endpoints.project.getEvalAttributeList(), {
    params: {
      // The endpoint's strict `filters` contract accepts project_id only.
      // Task selection filters belong to task creation/update requests and
      // must not leak into attribute discovery.
      filters: JSON.stringify({ project_id: projectId }),
      row_type: rowType,
    },
  });
