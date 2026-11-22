import { parseGitHubInput } from "src/pages/dashboard/harness/requestMapper";

// The source as the scan and connect endpoints take it.
export const githubSource = ({
  mode,
  connection,
  repository,
  branch,
  link,
  linkBranch,
}) => {
  if (mode === "app") {
    if (!repository) return null;
    return {
      kind: "github",
      repository: repository.full_name,
      ...(branch ? { ref: branch } : {}),
      visibility: repository.private ? "private" : "public",
      ...(repository.private && connection?.external_project_name
        ? { installation_id: String(connection.external_project_name) }
        : {}),
      ...(repository.id ? { repository_id: repository.id } : {}),
    };
  }
  const parsed = parseGitHubInput(link);
  if (!parsed) return null;
  const ref = linkBranch.trim() || parsed.ref;
  return {
    kind: "github",
    repository: parsed.repository,
    ...(ref ? { ref } : {}),
    visibility: "public",
  };
};
