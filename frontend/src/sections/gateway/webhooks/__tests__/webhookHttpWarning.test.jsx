import React from "react";
import PropTypes from "prop-types";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { act, cleanup, fireEvent } from "@testing-library/react";
import { render, screen } from "src/utils/test-utils";

import CreateEditWebhookDialog from "../CreateEditWebhookDialog";
import WebhookManagementSection from "../WebhookManagementSection";
import WebhookEventLog from "../WebhookEventLog";

const WARNING =
  "This URL uses HTTP, so deliveries are not encrypted. Webhook payloads can include event data from your organization. Use an HTTPS URL to protect payloads in transit.";
const BADGE = "Unencrypted (HTTP)";
const state = vi.hoisted(() => ({
  grid: vi.fn(() => null),
  test: vi.fn(),
  notice: vi.fn(),
  webhooks: [],
  events: [],
}));

vi.mock("react-router-dom", async (importOriginal) => ({
  ...(await importOriginal()),
  useParams: () => ({}),
  useNavigate: () => vi.fn(),
  useSearchParams: () => [new URLSearchParams()],
}));
vi.mock("notistack", async (importOriginal) => ({
  ...(await importOriginal()),
  enqueueSnackbar: state.notice,
}));
vi.mock("../../hooks/useGatewayAdmin", () => ({
  useGatewayAdmin: () => ({ canEdit: true }),
}));
vi.mock("../hooks/useWebhooks", () => ({
  useWebhooks: () => ({ data: state.webhooks }),
  useWebhookEvents: () => ({ data: state.events }),
  useCreateWebhook: () => ({ mutate: vi.fn() }),
  useUpdateWebhook: () => ({ mutate: vi.fn() }),
  useDeleteWebhook: () => ({ mutate: vi.fn() }),
  useRetryWebhookEvent: () => ({ mutate: vi.fn() }),
  useTestWebhook: () => ({ mutate: state.test }),
}));
vi.mock("../../components/frame", () => {
  const Container = ({ children }) => <div>{children}</div>;
  Container.propTypes = { children: PropTypes.node };
  const Status = ({ label }) => <span>{label}</span>;
  Status.propTypes = { label: PropTypes.string };
  return {
    GatewayGrid: state.grid,
    GatewayPageFrame: Container,
    GatewayFilterBar: Container,
    GatewayDetailDrawer: Container,
    GatewayEmptyState: () => null,
    GatewayStatusChip: Status,
  };
});
vi.mock("../../settings/ui", () => {
  const KeyValueList = ({ rows }) => (
    <div>
      {rows
        .filter((row) => !row.hidden)
        .map((row) => (
          <div key={row.label}>
            <span>{row.label}</span>
            <span>{row.value}</span>
          </div>
        ))}
    </div>
  );
  KeyValueList.propTypes = { rows: PropTypes.array.isRequired };
  return {
    KeyValueList,
    EnforcementNote: () => null,
    CopyButton: () => null,
    Glyph: () => null,
  };
});

const endpoint = (url) => ({
  id: "wh-http",
  name: "receiver",
  url,
  events: ["error.occurred"],
  headers: {},
  is_active: true,
});

function dialog(url) {
  const onSubmit = vi.fn();
  render(
    <CreateEditWebhookDialog
      open
      webhook={endpoint(url)}
      onClose={vi.fn()}
      onSubmit={onSubmit}
    />,
  );
  return onSubmit;
}

const gridProps = () => state.grid.mock.lastCall[0];

beforeEach(() => {
  vi.clearAllMocks();
  state.webhooks = [endpoint("https://hooks.example.com/events")];
  state.events = [];
});
afterEach(cleanup);

describe("HTTP webhook warning", () => {
  it.each([
    "http://hooks.example.com/events",
    "HTTP://hooks.example.com/events",
  ])("warns for %s without disabling save", (url) => {
    const onSubmit = dialog(url);
    expect(screen.getByText(WARNING)).toBeInTheDocument();
    const save = screen.getByRole("button", { name: "Save" });
    expect(save).toBeEnabled();
    fireEvent.click(save);
    expect(onSubmit).toHaveBeenCalledWith(expect.objectContaining({ url }));
  });

  it.each([
    "https://hooks.example.com/events",
    "",
    "http://",
    "http://[",
    "not a URL",
  ])("does not warn for HTTPS or an unfinished URL: %s", (url) => {
    dialog(url);
    expect(screen.queryByText(WARNING)).not.toBeInTheDocument();
  });

  it("updates the warning while editing the URL", () => {
    dialog("https://hooks.example.com/events");
    const input = screen.getByLabelText(/^URL/);
    fireEvent.change(input, {
      target: { value: "http://hooks.example.com/events" },
    });
    expect(screen.getByText(WARNING)).toBeInTheDocument();
    fireEvent.change(input, { target: { value: "http://[" } });
    expect(screen.queryByText(WARNING)).not.toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Save" })).toBeDisabled();
    fireEvent.change(input, {
      target: { value: "https://hooks.example.com/events" },
    });
    expect(screen.queryByText(WARNING)).not.toBeInTheDocument();
  });

  it.each([
    ["http://hooks.example.com/events", true],
    ["HTTP://hooks.example.com/events", true],
    ["https://hooks.example.com/events", false],
    ["https://hooks.example.com/http://", false],
    [undefined, false],
  ])("marks only saved HTTP endpoints: %s", (url, expected) => {
    render(<WebhookManagementSection />);
    const column = gridProps().columnDefs.find((item) => item.field === "url");
    const Renderer = column.cellRenderer;
    render(<Renderer value={url} />);
    expect(Boolean(screen.queryByText(BADGE))).toBe(expected);
  });
});

describe("failed Send test and delivery history", () => {
  it.each([302, 307])("shows HTTP %s in the Send test notice", (code) => {
    state.test.mockImplementation((_id, callbacks) =>
      callbacks.onSuccess({ success: false, status_code: code, error: "" }),
    );
    render(<WebhookManagementSection />);
    act(() => gridProps().context.onTest(state.webhooks[0]));
    expect(state.notice).toHaveBeenCalledWith(
      `Test not delivered: receiver answered HTTP ${code}`,
      { variant: "warning" },
    );
  });

  it("retains Send test error text", () => {
    state.test.mockImplementation((_id, callbacks) =>
      callbacks.onSuccess({
        success: false,
        status_code: 307,
        error: "Delivery failed",
      }),
    );
    render(<WebhookManagementSection />);
    act(() => gridProps().context.onTest(state.webhooks[0]));
    expect(state.notice).toHaveBeenCalledWith(
      expect.stringContaining(": Delivery failed"),
      { variant: "warning" },
    );
  });

  it.each([
    [302, "", "HTTP 302"],
    [307, "", "HTTP 307"],
    [302, "Delivery failed", "Delivery failed"],
    [307, "Delivery failed", "Delivery failed"],
  ])(
    "renders failed history and its drawer: %s / %s",
    (code, error, expected) => {
      const delivery = {
        id: "delivery-1",
        webhook_name: "receiver",
        event_type: "test",
        status: "FAILED",
        attempts: 1,
        last_response_code: code,
        last_error: error,
        created_at: "2026-01-01T00:00:00Z",
      };
      state.events = [delivery];
      render(<WebhookEventLog webhooks={state.webhooks} />);
      const props = gridProps();
      const column = props.columnDefs.find(
        (item) => item.field === "last_error",
      );
      const value = column.valueGetter
        ? column.valueGetter({ data: delivery })
        : delivery.last_error;
      const Renderer = column.cellRenderer;
      render(<Renderer value={value} data={delivery} />);
      expect(screen.getByText(expected)).toBeInTheDocument();
      act(() => props.onRowClicked({ data: delivery }));
      expect(screen.getAllByText(expected)).toHaveLength(2);
      if (error)
        expect(screen.queryByText(`HTTP ${code}`)).not.toBeInTheDocument();
    },
  );

  it.each([null, 200])("does not invent an error for response %s", (code) => {
    render(<WebhookEventLog />);
    const column = gridProps().columnDefs.find(
      (item) => item.field === "last_error",
    );
    const data = {
      status: "DELIVERED",
      last_response_code: code,
      last_error: "",
    };
    const value = column.valueGetter
      ? column.valueGetter({ data })
      : data.last_error;
    const Renderer = column.cellRenderer;
    render(<Renderer value={value} data={data} />);
    expect(screen.getByText("—")).toBeInTheDocument();
  });
});
