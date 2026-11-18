import { describe, it, expect, vi } from "vitest";
import { useEffect } from "react";
import PropTypes from "prop-types";
import { render, screen, act } from "@testing-library/react";
import {
  MemoryRouter,
  Outlet,
  Route,
  Routes,
  useNavigate,
} from "react-router-dom";

const mounts = [];
function RunDetailStub({ executionId, rerunDisabledReason }) {
  useEffect(() => {
    mounts.push(executionId);
  }, []); // eslint-disable-line react-hooks/exhaustive-deps
  return (
    <div>{`run:${executionId} starting:${rerunDisabledReason ?? "-"}`}</div>
  );
}
RunDetailStub.propTypes = {
  executionId: PropTypes.string,
  rerunDisabledReason: PropTypes.string,
};
vi.mock("../detail/RunDetail", () => ({ default: RunDetailStub }));

const { default: WorkspaceExecutionDetail } = await import(
  "../WorkspaceExecutionDetail"
);

let navigate;
function NavigateProbe() {
  navigate = useNavigate();
  return null;
}

const renderAt = (path, context = {}) =>
  render(
    <MemoryRouter initialEntries={[path]}>
      <NavigateProbe />
      <Routes>
        <Route
          path="/runs"
          element={<Outlet context={{ env: { id: "env1" }, ...context }} />}
        >
          <Route
            path=":testId/:executionId"
            element={<WorkspaceExecutionDetail />}
          />
        </Route>
      </Routes>
    </MemoryRouter>,
  );

describe("WorkspaceExecutionDetail", () => {
  // Opening another run (a re-run opens the new one) must not carry the old
  // run's filters, page or tab over: the run page starts fresh.
  it("starts the run page fresh when the run in the URL changes", async () => {
    mounts.length = 0;
    renderAt("/runs/rt1/ex1");
    expect(screen.getByText(/run:ex1/)).toBeInTheDocument();

    await act(async () => navigate("/runs/rt1/ex2"));

    expect(screen.getByText(/run:ex2/)).toBeInTheDocument();
    expect(mounts).toEqual(["ex1", "ex2"]);
  });
});
