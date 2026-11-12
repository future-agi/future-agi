import { describe, it, expect } from "vitest";
import { render, screen } from "src/utils/test-utils";

import { PreflightReport } from "../PreflightStatus";

const UNAVAILABLE =
  "Private GitHub repositories are not available on Future AGI cloud. Upload the code to keep using this environment.";
const SOURCE_FAILED = {
  id: "source",
  label: "Source reachable",
  status: "failed",
};

describe("PreflightReport for a check that couldn't finish", () => {
  it("says a private repository can't be read here in words, not its code", () => {
    render(
      <PreflightReport
        report={{
          checks: [SOURCE_FAILED],
          error: "github_private_unavailable",
        }}
      />,
    );

    expect(screen.getByTestId("preflight-report")).toHaveTextContent(
      UNAVAILABLE,
    );
    expect(screen.getByText("Source reachable: failed")).toBeInTheDocument();
    expect(screen.queryByText(/github_private_unavailable/)).toBeNull();
    expect(screen.queryByText(/The check couldn't finish/)).toBeNull();
  });

  it("names any other code as before", () => {
    render(
      <PreflightReport
        report={{ checks: [SOURCE_FAILED], error: "source_unreachable" }}
      />,
    );

    expect(
      screen.getByText("The check couldn't finish (source_unreachable)."),
    ).toBeInTheDocument();
    expect(screen.queryByText(UNAVAILABLE)).toBeNull();
  });

  it("names a code that every object has a property for as any other code", () => {
    render(
      <PreflightReport
        report={{ checks: [SOURCE_FAILED], error: "constructor" }}
      />,
    );

    expect(
      screen.getByText("The check couldn't finish (constructor)."),
    ).toBeInTheDocument();
    expect(screen.queryByText(UNAVAILABLE)).toBeNull();
  });
});
