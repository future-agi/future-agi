// Each rule names an existing generated declaration AND its field path. A
// normalized initializer must match exactly; after undoing the edits the rest
// of the declaration must still be identical to the merge base.
const pathsByName = new Map();
function at(paths, ...names) {
  for (const name of names) pathsByName.set(name, paths.split(" "));
}
at(
  "results.conditions.filter.group",
  "ModelHubAnnotationQueuesAutomationRulesListResponse",
);
at(
  "conditions.filter.group",
  "ModelHubAnnotationQueuesAutomationRulesCreateBody",
  "ModelHubAnnotationQueuesAutomationRulesReadResponse",
  "ModelHubAnnotationQueuesAutomationRulesUpdateBody",
  "ModelHubAnnotationQueuesAutomationRulesUpdateResponse",
  "ModelHubAnnotationQueuesAutomationRulesPartialUpdateBody",
  "ModelHubAnnotationQueuesAutomationRulesPartialUpdateResponse",
);
at("selection.filter.group", "ModelHubAnnotationQueuesItemsAddItemsBody");
at(
  "filters.group",
  "SimulateApiHarnessEnvironmentsEditEvaluationBody",
  "SimulateApiHarnessEnvironmentsEditEvaluationResponse",
  "SimulateRunTestsEvalConfigsUpdateCreateBody",
  "TracerInstantEvalEstimateCreateBody",
  "TracerInstantEvalStartCreateBody",
  "TracerObservationSpanGetGraphMethodsBody",
  "TracerProjectGetUserGraphDataBody",
  "TracerProjectGetUserMetricsBody",
  "TracerProjectGetUsersAggregateGraphDataBody",
  "TracerTraceSessionGetSessionGraphDataBody",
);
at(
  "simulate_eval_configs_detail.filters.group evals_detail.filters.group",
  "SimulateApiRunTestsListResponseItem",
  "SimulateRunTestsReadResponse",
  "SimulateRunTestsPartialUpdateResponse",
  "SimulateRunTestsComponentsPartialUpdateResponse",
);
at(
  "result.results.simulate_eval_configs_detail.filters.group result.results.evals_detail.filters.group",
  "SimulatePromptTemplatesSimulationsListResponse",
);
at(
  "evaluations_config.filters.group",
  "SimulatePromptTemplatesSimulationsCreateBody",
  "SimulateRunTestsCreateCreateBody",
  "SimulateRunTestsEvalConfigsCreateBody",
);
at(
  "result.simulate_eval_configs_detail.filters.group result.evals_detail.filters.group",
  "SimulatePromptTemplatesSimulationsReadResponse",
  "SimulatePromptTemplatesSimulationsPartialUpdateResponse",
);
at(
  "results.simulate_eval_configs_detail.filters.group results.evals_detail.filters.group",
  "SimulateRunTestsListResponse",
);
at("metrics.filters.group filters.group", "TracerDashboardQueryBody");
at(
  "query_config.metrics.filters.group query_config.filters.group",
  "TracerDashboardWidgetsPreviewQueryBody",
);
at(
  "results.filters.filters.group results.filters.span_attributes_filters.group",
  "TracerEvalTaskListResponse",
  "TracerEvalTaskGetEvalTaskLogsResponse",
);
at(
  "result.filters_applied.filters.group result.filters_applied.span_attributes_filters.group",
  "TracerEvalTaskGetEvalDetailsResponse",
);
at(
  "filters.filters.group filters.span_attributes_filters.group",
  "TracerEvalTaskSamplingPreviewBody",
  "TracerEvalTaskCreateBody",
  "TracerEvalTaskUpdateEvalTaskBody",
  "TracerEvalTaskReadResponse",
  "TracerEvalTaskUpdateBody",
  "TracerEvalTaskUpdateResponse",
  "TracerEvalTaskPartialUpdateBody",
  "TracerEvalTaskPartialUpdateResponse",
);
at(
  "result.split_by result.series result.totals_note result.metric_statistic",
  "TracerObservationSpanGetGraphMethodsResponse",
  "TracerProjectGetUsersAggregateGraphDataResponse",
  "TracerTraceSessionGetSessionGraphDataResponse",
  "TracerTraceGetGraphMethodsResponse",
);
at(
  "result.metadata.skill_links_exist",
  "TracerTraceSessionListSessionsResponse",
  "TracerTraceListTracesResponse",
  "TracerTraceListTracesOfSessionResponse",
);
at("filters.group split_by", "TracerTraceGetGraphMethodsBody");
at(
  "include_skill_links",
  "TracerTraceListTracesOfSessionQueryParams",
  "TracerTraceListTracesOfSessionCreateBody",
);

const optionalBool = "zod.boolean().optional()";
const oldStatistic =
  'zod.enum(["count", "sum", "mean", "percentage"]).optional()';
const afterFields = {
  group: "zod.number().min(0).max(2147483647).optional()",
  include_skill_links: optionalBool,
  skill_links_exist: optionalBool,
  split_by: 'zod.enum(["skill_version"]).optional()',
  totals_note: "zod.string().min(1).optional()",
  metric_statistic:
    'zod.enum(["count", "sum", "mean", "percentage", "p95"]).optional()',
  series: `zod.array(zod.object({
    version_id: zod.string().uuid(), label: zod.string().min(1), data: zod.array(zod.object({
      timestamp: zod.string().min(1), value: zod.number(), primary_traffic: zod.number().optional(),
      cost_coverage: zod.object({status: zod.enum(["complete", "partial", "unknown"]),
        known_subtotal: zod.number().min(0), known_fact_count: zod.number().min(0),
        unknown_fact_count: zod.number().min(0)}).optional(), traces: zod.number(), incomplete: zod.number()
    }))})).optional()`,
};

export function exactTraceDelta(name, before, after, ts, tokens, printer) {
  const paths = pathsByName.get(name);
  if (!paths || after === undefined) return false;

  // Work on the same null-normalized AST as the original audit, not on raw
  // Orval formatting. Only a transition from absent (or the exact prior enum)
  // to the reviewed validator at this path can be removed for comparison.
  function rest(source, side) {
    const file = ts.createSourceFile(
      "delta.ts",
      `const ${source};`,
      ts.ScriptTarget.Latest,
      true,
    );
    if (file.parseDiagnostics.length) return null;
    const expected = new Map();
    for (const path of paths) {
      const field = path.split(".").at(-1);
      const value =
        side === "after"
          ? afterFields[field]
          : field === "metric_statistic"
            ? oldStatistic
            : null;
      expected.set(path, value === null ? null : tokens(value));
    }
    // A property path must identify exactly one object. In particular, never
    // let a different object in a union consume another variant's exception.
    const parents = new Map(
      paths.map((p) => [p.split(".").slice(0, -1).join("."), 0]),
    );
    let valid = true;
    const result = ts.transform(file, [
      (context) => {
        function visit(node, parentPath) {
          let path = parentPath;
          if (
            ts.isObjectLiteralExpression(node) &&
            parents.has(path.join("."))
          ) {
            const key = path.join(".");
            parents.set(key, parents.get(key) + 1);
          }
          if (ts.isPropertyAssignment(node)) {
            path = [...parentPath, node.name.getText(file)];
            const key = path.join(".");
            if (paths.includes(key)) {
              if (expected.get(key) !== tokens(node.initializer.getText(file)))
                valid = false;
              expected.delete(key);
              return undefined;
            }
          }
          return ts.visitEachChild(
            node,
            (child) => visit(child, path),
            context,
          );
        }
        return (root) => visit(root, []);
      },
    ]);
    const text = tokens(printer.printFile(result.transformed[0]));
    result.dispose();
    return valid &&
      [...expected.values()].every((v) => v === null) &&
      [...parents.values()].every((count) => count === 1)
      ? text
      : null;
  }
  const oldRest = rest(before, "before");
  return oldRest !== null && oldRest === rest(after, "after");
}
