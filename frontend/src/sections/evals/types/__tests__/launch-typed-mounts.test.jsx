import React from "react";
import { afterEach, expect, it, vi } from "vitest";
import { cleanup, render, screen } from "@testing-library/react";
import {
  setLaunchOverrideForTest,
  isLaunchBuild,
} from "src/launch/launchSwitch";
import TypedEvalEditor from "../TypedEvalEditor";
import TypedEvalDetail from "../TypedEvalDetail";
import TypedEvalPanel from "../TypedEvalPanel";
const mounts = vi.hoisted(() => ({
  dagEditor: vi.fn(),
  dagDetail: vi.fn(),
  remoteEditor: vi.fn(),
  remoteDetail: vi.fn(),
}));
vi.mock("../DagEvalEditor", () => {
  const View = () => {
    mounts.dagEditor();
    return <div data-testid="dag-editor" />;
  };
  View.ready = true;
  return { default: View };
});
vi.mock("../DagEvalDetail", () => {
  const View = () => {
    mounts.dagDetail();
    return <div data-testid="dag-detail" />;
  };
  View.ready = true;
  return { default: View };
});
vi.mock("../RemoteScorerEditor", () => {
  const View = () => {
    mounts.remoteEditor();
    return <div data-testid="remote-editor" />;
  };
  View.ready = true;
  return { default: View };
});
vi.mock("../RemoteScorerDetail", () => {
  const View = () => {
    mounts.remoteDetail();
    return <div data-testid="remote-detail" />;
  };
  View.ready = true;
  return { default: View };
});
afterEach(() => {
  cleanup();
  setLaunchOverrideForTest(null);
  vi.clearAllMocks();
});
it("mounts released Decision tree views directly and in a panel", () => {
  setLaunchOverrideForTest({
    showUnreleased: false,
    launch: true,
    madeReal: ["evals.dag"],
  });
  render(
    <>
      <TypedEvalEditor evalType="dag" name="fixture" onSave={() => {}} />
      <TypedEvalDetail evalType="dag" templateId="fixture" />
      <TypedEvalPanel
        open
        evalType="dag"
        templateId="fixture"
        onClose={() => {}}
      />
    </>,
  );
  expect(isLaunchBuild()).toBe(true);
  expect(screen.getByTestId("dag-editor")).toBeTruthy();
  expect(screen.getAllByTestId("dag-detail")).toHaveLength(2);
  expect(mounts.dagEditor).toHaveBeenCalled();
  expect(mounts.dagDetail).toHaveBeenCalled();
});
it("retains an independently claimed typed control in the same launch mode", () => {
  setLaunchOverrideForTest({
    showUnreleased: false,
    launch: true,
    madeReal: ["evals.remote"],
  });
  render(
    <>
      <TypedEvalEditor evalType="remote" name="fixture" onSave={() => {}} />
      <TypedEvalDetail evalType="remote" templateId="fixture" />
    </>,
  );
  expect(isLaunchBuild()).toBe(true);
  expect(screen.getByTestId("remote-editor")).toBeTruthy();
  expect(screen.getByTestId("remote-detail")).toBeTruthy();
  expect(mounts.remoteEditor).toHaveBeenCalled();
  expect(mounts.remoteDetail).toHaveBeenCalled();
  expect(mounts.dagEditor).not.toHaveBeenCalled();
  expect(mounts.dagDetail).not.toHaveBeenCalled();
});
