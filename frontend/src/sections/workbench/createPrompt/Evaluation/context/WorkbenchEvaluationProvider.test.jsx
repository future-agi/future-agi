import React from "react";
import { describe, expect, it } from "vitest";
import { renderWithRouter, screen } from "src/utils/test-utils";
import { PromptWorkbenchContext } from "../../WorkbenchContext";
import WorkbenchEvaluationProvider from "./WorkbenchEvaluationProvider";
import { useWorkbenchEvaluationContext } from "./WorkbenchEvaluationContext";

const VersionProbe = () => {
  const { versions } = useWorkbenchEvaluationContext();
  return <div data-testid="versions">{JSON.stringify(versions)}</div>;
};

const renderProvider = ({
  route = "/dashboard/workbench/create/prompt-1",
} = {}) =>
  renderWithRouter(
    <PromptWorkbenchContext.Provider
      value={{
        selectedVersions: [
          { version: "v1", isDraft: false },
          { version: "v2", isDraft: false },
        ],
      }}
    >
      <WorkbenchEvaluationProvider>
        <VersionProbe />
      </WorkbenchEvaluationProvider>
    </PromptWorkbenchContext.Provider>,
    { route },
  );

describe("WorkbenchEvaluationProvider", () => {
  it("runs workbench evaluations against every compared prompt version by default", () => {
    renderProvider();

    expect(JSON.parse(screen.getByTestId("versions").textContent)).toEqual([
      "v1",
      "v2",
    ]);
  });

  it("keeps an explicit versions URL override", () => {
    renderProvider({
      route: '/dashboard/workbench/create/prompt-1?versions=["v2"]',
    });

    expect(JSON.parse(screen.getByTestId("versions").textContent)).toEqual([
      "v2",
    ]);
  });
});
