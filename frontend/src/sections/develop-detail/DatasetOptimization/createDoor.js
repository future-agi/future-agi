// Whether the Optimization tab offers "Optimize Prompts".
//
// POST /model-hub/dataset-
// optimization/ was unmocked in prototype mode, so the create door (header
// button, empty-state button, drawer) was absent there. The preview serves the path
// in the mock world (handlers/w6-runs.js: every trial is a real prompt run in
// its own experiment), so the door is offered again in both modes; the backend
// serves it outside prototype mode (api-surface "/model-hub/dataset-
// optimization/": ["get", "post"]).
export function optimizationCreateOffered() {
  return true;
}
