import { paths } from "src/routes/paths";

export const observeProjectPath = (projectId) =>
  `${paths.dashboard.observe}/${projectId}/llm-tracing`;
