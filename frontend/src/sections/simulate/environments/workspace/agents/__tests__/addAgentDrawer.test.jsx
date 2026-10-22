import { describe, it, expect, vi, afterEach } from "vitest";
import { render, screen, fireEvent, act } from "@testing-library/react";
import AddAgentDrawer from "../AddAgentDrawer";
import DynamicField from "../connect/DynamicField";
import { FIELD } from "../connect/connect.constants";
import { connectionRowsFor } from "../agentVersion.helpers";

const type = { id: "chat", label: "Chat" };

// A source agent that already has its first version, so the drawer previews the
// next label ("v2") and the "filling enables submit" test starts from empty.
const agent = {
  id: "agent-1",
  typeId: "chat",
  values: {},
  versions: [{ id: "v1", label: "v1", values: {} }],
  activeVersionId: "v1",
};

function renderDrawer(overrides = {}) {
  const props = { open: true, onClose: vi.fn(), agent, type, onAdd: vi.fn(), ...overrides };
  render(<AddAgentDrawer {...props} />);
  return props;
}

describe("AddAgentDrawer", () => {
  it("renders the connection fields for the default reach when open", () => {
    renderDrawer();
    expect(
      screen.getByPlaceholderText("https://api.yourapp.com/agent"),
    ).toBeInTheDocument();
  });

  it("keeps the submit disabled until the required fields are filled", () => {
    renderDrawer();
    const submit = screen.getByRole("button", { name: /Create/ });
    expect(submit).toBeDisabled();

    fireEvent.change(screen.getByPlaceholderText("https://api.yourapp.com/agent"), {
      target: { value: "https://x.test/agent" },
    });
    expect(submit).toBeEnabled();
  });

  it("fires onAdd with the collected version record on submit", () => {
    const { onAdd } = renderDrawer();
    fireEvent.change(screen.getByPlaceholderText("https://api.yourapp.com/agent"), {
      target: { value: "https://x.test/agent" },
    });
    fireEvent.click(screen.getByRole("button", { name: /Create/ }));

    expect(onAdd).toHaveBeenCalledTimes(1);
    expect(onAdd).toHaveBeenCalledWith(
      expect.objectContaining({
        reach: "endpoint",
        values: expect.objectContaining({ endpoint: "https://x.test/agent" }),
        via: expect.stringContaining("https://x.test/agent"),
        note: expect.any(String),
        connectedAt: expect.any(String),
      }),
    );
  });

  it("shows exactly one close control (SideDrawer's), never a doubled X", () => {
    renderDrawer();
    expect(screen.getAllByRole("button", { name: "Close" })).toHaveLength(1);
  });

  it("swaps the fields when the reach changes", () => {
    renderDrawer();
    fireEvent.click(screen.getByRole("button", { name: /Source repository/ }));
    expect(
      screen.getByPlaceholderText("https://github.com/your-org/your-agent"),
    ).toBeInTheDocument();
  });

  it("renders nothing while closed", () => {
    renderDrawer({ open: false });
    expect(
      screen.queryByPlaceholderText("https://api.yourapp.com/agent"),
    ).not.toBeInTheDocument();
  });
});

describe("AddAgentDrawer — MCP reach", () => {
  afterEach(() => vi.useRealTimers());

  it("gates submit on the mocked connection, then records an MCP version the cards can read", () => {
    vi.useFakeTimers();
    const onAdd = vi.fn();
    render(<AddAgentDrawer open onClose={vi.fn()} agent={agent} type={type} onAdd={onAdd} />);

    fireEvent.click(screen.getByRole("button", { name: /MCP/ }));
    const submit = screen.getByRole("button", { name: /Create/ });
    expect(submit).toBeDisabled();

    fireEvent.click(screen.getByRole("button", { name: "Wait for connection" }));
    act(() => vi.advanceTimersByTime(900));
    expect(submit).toBeEnabled();

    fireEvent.click(submit);
    const record = onAdd.mock.calls[0][0];
    expect(record.reach).toBe("mcp");
    expect(record.values.mcpUrl).toContain("mcp.futureagi.com");
    // The A1 card helper resolves an MCP version off the same `values`.
    expect(connectionRowsFor(record, type, record.values)).toEqual(
      expect.arrayContaining([expect.objectContaining({ value: "MCP server" })]),
    );
  });
});

describe("DynamicField", () => {
  it("renders a native select for a select field spec", () => {
    render(
      <DynamicField
        field={{
          key: "provider",
          label: "Provider",
          type: FIELD.SELECT,
          options: [{ value: "vapi", label: "Vapi" }],
        }}
        value=""
        onChange={vi.fn()}
        values={{}}
      />,
    );
    expect(screen.getByRole("combobox")).toBeInTheDocument();
    expect(screen.getByRole("option", { name: "Vapi" })).toBeInTheDocument();
  });

  it("renders a text field for a text field spec", () => {
    render(
      <DynamicField
        field={{ key: "agentId", label: "Agent id", type: FIELD.TEXT }}
        value=""
        onChange={vi.fn()}
        values={{}}
      />,
    );
    expect(screen.getByRole("textbox")).toBeInTheDocument();
    expect(screen.queryByRole("combobox")).not.toBeInTheDocument();
  });

  it("calls onChange with the typed value", () => {
    const onChange = vi.fn();
    render(
      <DynamicField
        field={{ key: "agentId", label: "Agent id", type: FIELD.TEXT }}
        value=""
        onChange={onChange}
        values={{}}
      />,
    );
    fireEvent.change(screen.getByRole("textbox"), { target: { value: "asst_1" } });
    expect(onChange).toHaveBeenCalledWith("asst_1");
  });
});
