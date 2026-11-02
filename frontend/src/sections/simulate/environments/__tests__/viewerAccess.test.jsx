import { describe, it, expect, vi, beforeAll, beforeEach } from "vitest";
import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter } from "react-router-dom";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";

import useSimulationAccess from "../hooks/useSimulationAccess";
import ViewerLock from "../components/ViewerLock";
import RowActionsMenu from "../components/RowActionsMenu";
import CancelBuildControl from "../buildEnvironment/building/CancelBuildControl";
import BuilderConsole from "../buildEnvironment/console/BuilderConsole";
import WorkspaceHeader from "../workspace/WorkspaceHeader";
import { ENV_STATUS, ROW_ACTION_LABEL } from "../myEnvironments.constants";
import { WORKSPACE_COPY } from "../workspace/workspace.constants";

const VIEWER = WORKSPACE_COPY.viewer;

const providers = (ui) => (
  <QueryClientProvider client={new QueryClient()}>
    <MemoryRouter>{ui}</MemoryRouter>
  </QueryClientProvider>
);

const hoverShows = async (element, text) => {
  await userEvent.hover(element.closest("span") ?? element);
  await waitFor(() =>
    expect(screen.getByRole("tooltip")).toHaveTextContent(text),
  );
};

beforeAll(() => {
  window.HTMLElement.prototype.scrollIntoView = vi.fn();
});

beforeEach(() => {
  vi.mocked(useSimulationAccess).mockReturnValue({
    canEdit: false,
    canRun: false,
  });
});

describe("ViewerLock", () => {
  it("leaves the control alone for someone who can edit", () => {
    render(
      <ViewerLock locked={false} title="nope">
        <button type="button">Save</button>
      </ViewerLock>,
    );
    expect(
      screen.getByRole("button", { name: "Save" }).parentElement.tagName,
    ).not.toBe("SPAN");
  });

  it("explains a locked control on hover", async () => {
    render(
      <ViewerLock locked title={VIEWER.secrets}>
        <button type="button" disabled>
          Save
        </button>
      </ViewerLock>,
    );
    await hoverShows(
      screen.getByRole("button", { name: "Save" }),
      VIEWER.secrets,
    );
  });
});

describe("as a Viewer", () => {
  it("can't run or delete an environment from its row menu", async () => {
    const anchorEl = document.createElement("button");
    document.body.appendChild(anchorEl);
    render(
      <RowActionsMenu
        menuFor={{
          row: { id: "env-1", status: ENV_STATUS.COMPLETED },
          anchorEl,
        }}
        onClose={() => {}}
      />,
    );

    const run = screen.getByRole("menuitem", { name: ROW_ACTION_LABEL.run });
    const remove = screen.getByRole("menuitem", {
      name: ROW_ACTION_LABEL.delete,
    });
    expect(run).toHaveAttribute("aria-disabled", "true");
    expect(remove).toHaveAttribute("aria-disabled", "true");
    await hoverShows(remove, VIEWER.delete);
  });

  it("can't cancel a build", async () => {
    render(providers(<CancelBuildControl envId="env-1" building />));

    const cancel = screen.getByRole("button", { name: /Cancel build/ });
    expect(cancel).toBeDisabled();
    await hoverShows(cancel, VIEWER.cancel);
  });

  it("can't write to the builder chat, and is told why", async () => {
    const onSend = vi.fn();
    render(<BuilderConsole turns={[]} running={false} onSend={onSend} />);

    const input = screen.getByPlaceholderText(VIEWER.chat);
    expect(input).toBeDisabled();
    const send = screen.getByRole("button", { name: "Send" });
    expect(send).toBeDisabled();
    await hoverShows(send, VIEWER.chat);
    expect(onSend).not.toHaveBeenCalled();
  });

  it("can't rename an environment from its header", async () => {
    render(
      providers(
        <WorkspaceHeader
          env={{ id: "env-1", name: "Support bot", status: ENV_STATUS.READY }}
          envState={{ scenarios: [] }}
          canRun={false}
          runBlockedReason={VIEWER.run}
          backed
          onFork={vi.fn()}
        />,
      ),
    );

    const rename = screen.getByRole("button", { name: "Rename environment" });
    expect(rename).toBeDisabled();
    await hoverShows(rename, VIEWER.rename);
  });
});

describe("as a Member", () => {
  it("keeps every row action available", () => {
    vi.mocked(useSimulationAccess).mockReturnValue({
      canEdit: true,
      canRun: true,
    });
    const anchorEl = document.createElement("button");
    document.body.appendChild(anchorEl);
    render(
      <RowActionsMenu
        menuFor={{
          row: { id: "env-1", status: ENV_STATUS.COMPLETED },
          anchorEl,
        }}
        onClose={() => {}}
      />,
    );

    expect(
      screen.getByRole("menuitem", { name: ROW_ACTION_LABEL.run }),
    ).not.toHaveAttribute("aria-disabled", "true");
    expect(
      screen.getByRole("menuitem", { name: ROW_ACTION_LABEL.delete }),
    ).not.toHaveAttribute("aria-disabled", "true");
  });
});
