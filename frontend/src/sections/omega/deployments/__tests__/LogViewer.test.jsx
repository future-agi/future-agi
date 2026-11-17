import React from "react";
import { render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";

import LogViewer from "../components/LogViewer";
import {
  isBuildNoise,
  visibleLogEntries,
} from "../detail/logs/logPresentation";
import { LOG_STATE } from "../lib/useLogStream";

const lines = [
  {
    key: "1",
    at: "2026-10-19T00:00:00Z",
    line: "[deploy] Building FROM ee-deploy-serve-runtime:c7835fd.",
  },
  {
    key: "2",
    at: "2026-10-19T00:00:01Z",
    line: "DEPRECATED: The legacy builder is deprecated and will be removed in a future release.",
  },
  {
    key: "3",
    at: "2026-10-19T00:00:02Z",
    line: "            Install the buildx component to build images with BuildKit:",
  },
  {
    key: "4",
    at: "2026-10-19T00:00:03Z",
    line: "            https://docs.docker.com/go/buildx/",
  },
  {
    key: "5",
    at: "2026-10-19T00:00:04Z",
    line: "Sending build context to Docker daemon  4.608kB",
  },
  {
    key: "6",
    at: "2026-10-19T00:00:05Z",
    line: "Step 1/6 : FROM ee-deploy-serve-runtime:c7835fd",
  },
  {
    key: "7",
    at: "2026-10-19T00:00:06Z",
    line: " ---> 536ba2b665b5",
  },
  {
    key: "8",
    at: "2026-10-19T00:00:07Z",
    line: 'Step 2/6 : LABEL org.omega.workspace-deployment="dep-2006e82056e251b2"',
  },
  {
    key: "9",
    at: "2026-10-19T00:00:08Z",
    line: "Step 5/6 : HEALTHCHECK --interval=5s --timeout=3s",
  },
  {
    key: "10",
    at: "2026-10-19T00:00:09Z",
    line: 'Step 6/6 : CMD ["serve","--store","/data/studio.sqlite","--grace-ms","20000"]',
  },
  {
    key: "11",
    at: "2026-10-19T00:00:10Z",
    line: "Successfully tagged omega-local-ws-dep-2006e82056e251b2:r1",
  },
  {
    key: "12",
    at: "2026-10-19T00:00:11Z",
    line: "[deploy] Revision 3 is live.",
  },
];

const auditedBuildLines = [
  "[deploy] Building FROM ee-deploy-serve-runtime:c7835fd.",
  "DEPRECATED: The legacy builder is deprecated and will be removed in a future release.",
  "            Install the buildx component to build images with BuildKit:",
  "            https://docs.docker.com/go/buildx/",
  "Sending build context to Docker daemon  4.608kB",
  "Step 1/6 : FROM ee-deploy-serve-runtime:c7835fd",
  " ---> 536ba2b665b5",
  'Step 2/6 : LABEL org.omega.workspace-deployment="dep-2006e82056e251b2" org.omega.workspace-deployment.revision="1"',
  " ---> Running in a138ffc36ae4",
  " ---> Removed intermediate container a138ffc36ae4",
  " ---> 0e952319df3e",
  "Step 3/6 : ADD project.tar.gz /srv/omega/app/",
  " ---> 816511df6214",
  "Step 4/6 : ENV OMEGA_DEPLOY_NAME=audit-greeter OMEGA_DEPLOY_REVISION=1",
  " ---> Running in 2b36292c71e2",
  " ---> Removed intermediate container 2b36292c71e2",
  " ---> 59e7da169d51",
  'Step 5/6 : HEALTHCHECK --interval=5s --timeout=3s --start-period=30s --retries=3 CMD ["node","-e","fetch(\'http://127.0.0.1:8080/health\').then((r)=>process.exit(r.ok?0:1),()=>process.exit(1))"]',
  " ---> Running in 51264833328e",
  " ---> Removed intermediate container 51264833328e",
  " ---> f302f2d981f9",
  'Step 6/6 : CMD ["serve","--project","/srv/omega/app","--host","0.0.0.0","--port","8080","--store","/data/studio.sqlite","--grace-ms","20000","--studio"]',
  " ---> Running in c628bef8e5e4",
  " ---> Removed intermediate container c628bef8e5e4",
  " ---> 3719a46dcda0",
  "Successfully built 3719a46dcda0",
  "Successfully tagged omega-local-ws-dep-2006e82056e251b2:r1",
  "[deploy] Started revision 1; waiting for http://127.0.0.1:58127/health to report it.",
  "[deploy] Revision 1 is live at http://127.0.0.1:58127.",
].map((line, index) => ({
  key: `audited-${index}`,
  at: "2026-10-19T00:00:00Z",
  line,
}));

// Revision 2's build log as the cloud rig's BuildKit printed it (2026-10-20).
const buildKitLines = [
  "[deploy] Building FROM ee-deploy-serve-runtime:c7835fd.",
  '#0 building with "default" instance using docker driver',
  "#1 [internal] load build definition from Dockerfile",
  "#1 transferring dockerfile: 687B done",
  "#1 DONE 0.0s",
  "#2 [internal] load metadata for docker.io/library/ee-deploy-serve-runtime:c7835fd",
  "#2 DONE 0.0s",
  "#3 [internal] load .dockerignore",
  "#3 transferring context: 2B done",
  "#3 DONE 0.0s",
  "#4 [1/2] FROM docker.io/library/ee-deploy-serve-runtime:c7835fd",
  "#4 DONE 0.0s",
  "#5 [internal] load build context",
  "#5 transferring context: 2.58kB done",
  "#5 DONE 0.0s",
  "#6 [2/2] ADD project.tar.gz /srv/omega/app/",
  "#6 CACHED",
  "#7 exporting to image",
  "#7 exporting layers done",
  "#7 writing image sha256:299f4741ecb6e8fa3524cbfc570afb63e757bb2db0ab210e129ba38b2e5dbe99 done",
  "#7 naming to docker.io/library/omega-local-ws-dep-6484104c7bce0bce:r2 done",
  "#7 DONE 0.0s",
  "[checks] Checking revision 2 before it takes traffic: every entry must load.",
  "[checks] Started revision 2 apart from Live, with its own run store; it takes no traffic.",
  "[checks] All 3 entries load.",
  "[deploy] Stopping revision 1 so revision 2 can take port 43613; it drains its runs first.",
  "[deploy] Started revision 2; waiting for http://127.0.0.1:43613/health to report it.",
  "[deploy] Revision 2 is live at http://127.0.0.1:43613.",
].map((line, index) => ({
  key: `buildkit-${index}`,
  at: "2026-10-20T21:34:20Z",
  line,
}));

const stream = {
  lines,
  state: LOG_STATE.ENDED,
  endReason: "revision log closed",
  error: null,
  restart: vi.fn(),
};

describe("build log presentation", () => {
  it("keeps log semantics without per-line live announcements (FA-DEP-UX-10)", () => {
    render(<LogViewer stream={stream} source="build" testId="build-log" />);
    expect(screen.getByRole("log")).toHaveAttribute("aria-live", "off");
  });

  it("identifies Docker setup and deprecation noise", () => {
    expect(isBuildNoise(lines[1].line)).toBe(true);
    expect(isBuildNoise(lines[2].line)).toBe(true);
    expect(isBuildNoise(lines[3].line)).toBe(true);
    expect(isBuildNoise(lines[4].line)).toBe(true);
    expect(isBuildNoise(lines[5].line)).toBe(true);
    expect(isBuildNoise(lines[6].line)).toBe(true);
    expect(isBuildNoise(lines[7].line)).toBe(true);
    expect(isBuildNoise(lines[8].line)).toBe(true);
    expect(isBuildNoise(lines[9].line)).toBe(true);
    expect(isBuildNoise(lines[10].line)).toBe(true);
    expect(isBuildNoise(lines[0].line)).toBe(false);
  });

  it("filters setup noise only for build logs and reports what it hid", () => {
    expect(visibleLogEntries(lines, "build")).toMatchObject({
      hiddenCount: 10,
      entries: [lines[0], lines[11]],
    });
    expect(visibleLogEntries(lines, "runtime")).toEqual({
      entries: lines,
      hiddenCount: 0,
    });
  });

  it("filters the audited Docker build output down to deploy milestones", () => {
    const result = visibleLogEntries(auditedBuildLines, "build");
    expect(result.hiddenCount).toBe(26);
    expect(result.entries.map((entry) => entry.line)).toEqual([
      "[deploy] Building FROM ee-deploy-serve-runtime:c7835fd.",
      "[deploy] Started revision 1; waiting for http://127.0.0.1:58127/health to report it.",
      "[deploy] Revision 1 is live at http://127.0.0.1:58127.",
    ]);
  });

  it("filters BuildKit's plain progress down to the deploy and check lines", () => {
    const result = visibleLogEntries(buildKitLines, "build");
    expect(result.hiddenCount).toBe(21);
    expect(result.entries.map((entry) => entry.line)).toEqual([
      "[deploy] Building FROM ee-deploy-serve-runtime:c7835fd.",
      "[checks] Checking revision 2 before it takes traffic: every entry must load.",
      "[checks] Started revision 2 apart from Live, with its own run store; it takes no traffic.",
      "[checks] All 3 entries load.",
      "[deploy] Stopping revision 1 so revision 2 can take port 43613; it drains its runs first.",
      "[deploy] Started revision 2; waiting for http://127.0.0.1:43613/health to report it.",
      "[deploy] Revision 2 is live at http://127.0.0.1:43613.",
    ]);
  });

  it("never hides what a build step printed or why a build failed", () => {
    for (const line of [
      "#6 0.512 added 12 packages, and audited 13 packages in 2s",
      "#8 1.204 npm ERR! code ERESOLVE",
      '#8 ERROR: process "/bin/sh -c npm ci" did not complete successfully: exit code: 1',
      "------",
      " > [2/3] RUN npm ci:",
      'ERROR: failed to solve: process "/bin/sh -c npm ci" did not complete successfully: exit code: 1',
      "#6 CANCELED",
      "[deploy] Build failed.",
    ]) {
      expect(isBuildNoise(line)).toBe(false);
    }
    for (const line of [
      "#4 [builder 2/4] RUN npm ci",
      "#3 resolve docker.io/library/node:22@sha256:0123456789abcdef0123 done",
      "#3 sha256:0123456789abcdef0123456789abcdef 1.2MB / 40MB 0.3s",
      "#3 extracting sha256:0123456789abcdef0123456789abcdef 0.8s done",
      "#9 unpacking to docker.io/library/app:r1 done",
      "Removing intermediate container 2b36292c71e2",
      " ---> Using cache",
    ]) {
      expect(isBuildNoise(line)).toBe(true);
    }
  });

  it("keeps useful build lines visible while hiding raw Docker plumbing", () => {
    render(<LogViewer stream={stream} source="build" testId="build-log" />);
    expect(
      screen.getByText(
        "[deploy] Building FROM ee-deploy-serve-runtime:c7835fd.",
      ),
    ).toBeInTheDocument();
    expect(
      screen.getByText("[deploy] Revision 3 is live."),
    ).toBeInTheDocument();
    expect(screen.queryByText(/DEPRECATED: The legacy builder/)).toBeNull();
    expect(screen.queryByText(/buildx component/)).toBeNull();
    expect(screen.queryByText(/Step 1\/6/)).toBeNull();
    expect(screen.getByTestId("build-log-hidden-noise")).toHaveTextContent(
      "10 setup lines hidden",
    );
  });
});
