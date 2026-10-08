import { describe, expect, it, vi } from "vitest";
import { fireEvent, render, screen } from "src/utils/test-utils";
import AddIntegrationWizard from "../AddIntegrationWizard";

const created = vi.hoisted(() => ({ connection: null }));

vi.mock("../AddIntegrationWizard/StepCredentials", () => ({
  default: ({ onNext }) => (
    <button type="button" onClick={onNext}>
      credentials-next
    </button>
  ),
}));

vi.mock("../AddIntegrationWizard/StepSyncSettings", () => ({
  default: ({ onSuccess }) => (
    <button
      type="button"
      onClick={() => onSuccess(created.connection.id, created.connection)}
    >
      create-connection
    </button>
  ),
}));

function finishWizard(connection) {
  created.connection = connection;
  render(
    <AddIntegrationWizard open onClose={vi.fn()} initialPlatform="posthog" />,
  );
  fireEvent.click(screen.getByText("credentials-next"));
  fireEvent.click(screen.getByText("create-connection"));
}

describe("AddIntegrationWizard success step", () => {
  it("says the integration is active when it was created active", () => {
    finishWizard({ id: "conn-1", status: "active", status_message: "" });

    expect(screen.getByText(/now active/i)).toBeInTheDocument();
  });

  it("shows the status message instead when the initial sync could not start", () => {
    finishWizard({
      id: "conn-1",
      status: "error",
      status_message:
        "Could not start the initial sync. Use Sync Now to retry.",
    });

    expect(screen.queryByText(/now active/i)).not.toBeInTheDocument();
    expect(
      screen.getByText(
        "Could not start the initial sync. Use Sync Now to retry.",
      ),
    ).toBeInTheDocument();
    expect(screen.getByText("View Integration")).toBeInTheDocument();
  });
});
