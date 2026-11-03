module.exports = async ({ github, context }) => {
  const pr = context.payload.pull_request;
  if (
    context.eventName !== "pull_request" ||
    pr?.user?.login !== "futureagi-release-bot[bot]" ||
    pr.base.ref !== "main" ||
    pr.head.ref !== "release-please--branches--main" ||
    pr.head.repo.full_name !== pr.base.repo.full_name
  )
    return false;

  const files = await github.paginate(github.rest.pulls.listFiles, {
    ...context.repo,
    pull_number: pr.number,
    per_page: 100,
  });
  // A missing/truncated file list must never exempt application changes.
  if (!files.length || files.length !== pr.changed_files) return false;
  return files.every((file) => {
    if (file.status !== "modified") return false;
    if (
      ["CHANGELOG.md", ".release-please-manifest.json"].includes(file.filename)
    )
      return true;
    if (file.filename !== "deploy/helm/futureagi/Chart.yaml" || !file.patch)
      return false;
    const changes = file.patch.split("\n").filter((line) => /^[+-]/.test(line));
    return (
      changes.length === file.additions + file.deletions &&
      changes.length > 0 &&
      changes.every((line) =>
        /^[+-](version|appVersion): .*# x-release-please-version$/.test(line),
      )
    );
  });
};
