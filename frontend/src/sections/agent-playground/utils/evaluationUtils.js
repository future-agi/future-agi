export const buildAgentEvaluationColumns = (executionData) =>
  (executionData?.nodes || []).flatMap((node) =>
    (node.ports || [])
      .filter((port) => (port.direction || port.portDirection) === "output")
      .map((port) => ({
        field: `${node.id}.${port.key}`,
        headerName: `${node.name}.${port.displayName || port.display_name || port.key}`,
        dataType: "text",
      })),
  );

export const getEvaluatorId = (item) =>
  item?.evalId ||
  item?.eval_id ||
  item?.templateId ||
  item?.template_id ||
  item?.id;

export const getEvaluatorMapping = (item) =>
  item?.mapping || item?.config?.mapping || {};

export const hasEvaluatorMappings = (evaluators) =>
  evaluators.length > 0 &&
  evaluators.every((item) => Object.keys(getEvaluatorMapping(item)).length > 0);
