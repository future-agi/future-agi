import React from "react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { act, fireEvent, screen } from "@testing-library/react";
import PlaygroundTab from "./PlaygroundTab";
import { renderMcp } from "../testing/renderMcp";

const state = vi.hoisted(() => ({
  tools: [{ name: "alpha__read", server_id: "alpha" }],
  mutate: vi.fn(),
  canEdit: true,
}));
vi.mock("../hooks/useMCPConfig", () => ({
  useMCPTools: () => ({ data: state.tools, isLoading: false }),
  useTestMCPTool: () => ({ mutate: state.mutate, isPending: false }),
}));
vi.mock("../../hooks/useGatewayAdmin", () => ({
  useGatewayAdmin: () => ({ canEdit: state.canEdit }),
}));
function invoke(button) {
  const key = Object.keys(button).find((name) =>
    name.startsWith("__reactProps$"),
  );
  act(() => button[key].onClick());
}
beforeEach(() => {
  state.tools = [{ name: "alpha__read", server_id: "alpha" }];
  state.mutate.mockReset();
  state.canEdit = true;
});

describe("RL-1009 cached tools and direct execution boundary", () => {
  it("disables and guards Run with cached tools, retaining local arguments", () => {
    renderMcp(<PlaygroundTab gatewayId="gw" runtimeOff />, {
      route: "/?tool=alpha__read",
    });
    const run = screen.getByRole("button", { name: "Run" });
    expect(run).toBeDisabled();
    invoke(run);
    expect(state.mutate).not.toHaveBeenCalled();
    const args = screen.getByLabelText("Arguments (JSON)");
    fireEvent.change(args, { target: { value: '{"local":true}' } });
    expect(args).toHaveValue('{"local":true}');
    expect(
      screen.getByText(/Live MCP operations are unavailable/),
    ).toBeInTheDocument();
  });
  it("guards an on-to-off transition after a successful run", () => {
    let turnOff;
    function Harness() {
      const [off, setOff] = React.useState(false);
      turnOff = () => setOff(true);
      return <PlaygroundTab gatewayId="gw" runtimeOff={off} />;
    }
    renderMcp(<Harness />, { route: "/?tool=alpha__read" });
    fireEvent.click(screen.getByRole("button", { name: "Run" }));
    expect(state.mutate).toHaveBeenCalledTimes(1);
    act(() =>
      state.mutate.mock.calls[0][1].onSuccess({ content: [], is_error: false }),
    );
    act(() => turnOff());
    const run = screen.getByRole("button", { name: "Run" });
    expect(run).toBeDisabled();
    invoke(run);
    expect(state.mutate).toHaveBeenCalledTimes(1);
    expect(screen.getByText("Previous result")).toBeInTheDocument();
  });
  it("keeps arguments available with no cached tools and does not invent a selectable tool", () => {
    state.tools = [];
    renderMcp(<PlaygroundTab gatewayId="gw" runtimeOff />, {
      route: "/?tool=alpha__missing",
    });
    expect(screen.getByLabelText("Arguments (JSON)")).toBeEnabled();
    const run = screen.getByRole("button", { name: "Run" });
    expect(run).toBeDisabled();
    invoke(run);
    expect(state.mutate).not.toHaveBeenCalled();
  });
  it("guards direct execution for a read-only role with MCP on", () => {
    state.canEdit = false;
    renderMcp(<PlaygroundTab gatewayId="gw" />, {
      route: "/?tool=alpha__read",
    });
    invoke(screen.getByRole("button", { name: "Run" }));
    expect(state.mutate).not.toHaveBeenCalled();
  });
});
