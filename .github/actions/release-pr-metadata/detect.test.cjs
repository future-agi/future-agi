const assert = require("node:assert/strict");
const { test } = require("node:test");
const detect = require("./detect.cjs");

function fixture() {
  const files = [
    { filename: "CHANGELOG.md", status: "modified" },
    { filename: ".release-please-manifest.json", status: "modified" },
    {
      filename: "deploy/helm/futureagi/Chart.yaml",
      status: "modified",
      additions: 2,
      deletions: 2,
      patch:
        '@@ -10,4 +10,4 @@\n-version: 1.52.0 # x-release-please-version\n+version: 1.53.0 # x-release-please-version\n-appVersion: "v1.52.0" # x-release-please-version\n+appVersion: "v1.53.0" # x-release-please-version',
    },
  ];
  const context = {
    eventName: "pull_request",
    repo: { owner: "future-agi", repo: "future-agi" },
    payload: {
      pull_request: {
        number: 3585,
        changed_files: 3,
        user: { login: "futureagi-release-bot[bot]" },
        base: { ref: "main", repo: { full_name: "future-agi/future-agi" } },
        head: {
          ref: "release-please--branches--main",
          repo: { full_name: "future-agi/future-agi" },
        },
      },
    },
  };
  const github = {
    rest: { pulls: { listFiles: "listFiles" } },
    paginate: async () => files,
  };
  return { files, context, github };
}

test("release changelog, manifest and chart version bumps skip application tests", async () => {
  assert.equal(await detect(fixture()), true);
});

for (const [name, mutate] of Object.entries({
  push: (f) => {
    f.context.eventName = "push";
    delete f.context.payload.pull_request;
  },
  "merge queue": (f) => {
    f.context.eventName = "merge_group";
  },
  "manual dispatch": (f) => {
    f.context.eventName = "workflow_dispatch";
  },
  "ordinary author": (f) => {
    f.context.payload.pull_request.user.login = "developer";
  },
  "ordinary branch": (f) => {
    f.context.payload.pull_request.head.ref = "hotfix/example";
  },
  "dev target": (f) => {
    f.context.payload.pull_request.base.ref = "dev";
  },
  fork: (f) => {
    f.context.payload.pull_request.head.repo.full_name = "other/future-agi";
  },
  "application change": (f) => {
    f.files[0].filename = "futureagi/app/models.py";
  },
  "chart behavior change": (f) => {
    f.files[2].patch += "\n+dependencies: []";
    f.files[2].additions++;
  },
  "missing chart patch": (f) => {
    delete f.files[2].patch;
  },
  "truncated chart patch": (f) => {
    f.files[2].additions++;
  },
  "renamed file": (f) => {
    f.files[0].status = "renamed";
  },
  "incomplete file list": (f) => {
    f.context.payload.pull_request.changed_files++;
  },
  "empty diff": (f) => {
    f.files.length = 0;
    f.context.payload.pull_request.changed_files = 0;
  },
})) {
  test(`${name} does not skip application tests`, async () => {
    const f = fixture();
    mutate(f);
    assert.equal(await detect(f), false);
  });
}

test("API failure fails the detection rather than skipping tests", async () => {
  const f = fixture();
  f.github.paginate = async () => {
    throw new Error("API unavailable");
  };
  await assert.rejects(detect(f), /API unavailable/);
});
