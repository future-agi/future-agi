import { describe, expect, it } from "vitest";
import { render, screen } from "src/utils/test-utils";
import ObserveExportLinks from "./ObserveExportLinks";

const receipt = {
  status: "verified",
  projectId: "project-a",
  projectName: "Squad runs",
  traceId: "0123456789abcdef0123456789abcdef",
  verifiedAt: "2026-10-13T00:00:00Z",
  deepLink:
    "/dashboard/observe/project-a/trace/01234567-89ab-cdef-0123-456789abcdef",
};
const response = (overrides = {}) => ({
  available: true,
  schemaVersion: "omega-workspace-observations/v1",
  conversationId: "run-a",
  observeExports: [receipt],
  ...overrides,
});

describe("Observe trace links", () => {
  it("opens the verified project and canonical trace from the selected run", () => {
    render(<ObserveExportLinks runId="run-a" response={response()} />);
    expect(
      screen.getByRole("link", { name: "Open Squad runs" }),
    ).toHaveAttribute("href", receipt.deepLink);
  });
  it.each(["export_pending", "accepted"])(
    "does not treat %s as verified even with a supplied link",
    (status) => {
      render(
        <ObserveExportLinks
          runId="run-a"
          response={response({ observeExports: [{ ...receipt, status }] })}
        />,
      );
      expect(screen.queryByRole("link")).not.toBeInTheDocument();
      expect(
        screen.getByText("The trace is not yet verified in Observe."),
      ).toBeInTheDocument();
    },
  );
  it.each([
    { deepLink: "https://foreign.example/trace" },
    {
      deepLink:
        "/dashboard/observe/other/trace/01234567-89ab-cdef-0123-456789abcdef",
    },
    { traceId: "1234" },
    { verifiedAt: "invalid" },
  ])("refuses an invalid or inconsistent receipt %#", (change) => {
    render(
      <ObserveExportLinks
        runId="run-a"
        response={response({ observeExports: [{ ...receipt, ...change }] })}
      />,
    );
    expect(screen.queryByRole("link")).not.toBeInTheDocument();
  });
  it("ignores a stale response from another selected run", () => {
    render(<ObserveExportLinks runId="run-b" response={response()} />);
    expect(screen.queryByText("Observe traces")).not.toBeInTheDocument();
    expect(screen.queryByRole("link")).not.toBeInTheDocument();
  });
});
