import { render, screen } from "@testing-library/react";
import { MemoryRouter, Route, Routes, useLocation } from "react-router-dom";
import { describe, expect, it } from "vitest";

import HarnessToEnvironment from "../harness-to-environment";

function Location() {
  return <p data-testid="location">{useLocation().pathname}</p>;
}

describe("HarnessToEnvironment", () => {
  it("sends an old harness job link to the environment with the same id", () => {
    render(
      <MemoryRouter initialEntries={["/dashboard/simulate/harness/job-1"]}>
        <Routes>
          <Route
            path="/dashboard/simulate/harness/:jobId"
            element={<HarnessToEnvironment />}
          />
          <Route path="*" element={<Location />} />
        </Routes>
      </MemoryRouter>,
    );

    expect(screen.getByTestId("location")).toHaveTextContent(
      "/dashboard/simulate/environments/job-1",
    );
  });
});
