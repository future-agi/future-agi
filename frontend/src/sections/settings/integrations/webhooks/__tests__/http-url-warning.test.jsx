import PropTypes from "prop-types";
import { useState } from "react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import {
  cleanup,
  fireEvent,
  render,
  screen,
  waitFor,
} from "src/utils/test-utils";
import AddWebhookDialog from "../AddWebhookDialog";
import WebhookFields from "../WebhookFields";
import WebhookEndpointPage from "../WebhookEndpointPage";
import WebhooksSection from "../WebhooksSection";
import SigningSecretDialog from "../SigningSecretDialog";
import { emptyForm, formOf } from "../form";

const MESSAGE =
  "This URL uses HTTP, so deliveries are not encrypted. Webhook payloads can include event data from your organization. Use an HTTPS URL to protect payloads in transit.";
const warning = { code: "webhook_url_unencrypted", message: MESSAGE };
const mocks = vi.hoisted(() => ({
  create: vi.fn(),
  reset: vi.fn(),
  preview: vi.fn(),
  update: vi.fn(),
  endpoint: null,
  rows: [],
  policy: {},
}));
vi.mock("src/components/iconify", () => ({ default: () => null }));
vi.mock("src/utils/utils", () => ({ copyToClipboard: vi.fn() }));
vi.mock("../WebhookPolicyCard", () => ({ default: () => null }));
vi.mock("../DeliveriesCard", () => ({ default: () => null }));
vi.mock("../ActivityCard", () => ({ default: () => null }));
vi.mock("react-helmet-async", () => ({ Helmet: () => null }));
vi.mock("src/components/loading-screen", () => ({ LoadingScreen: () => null }));
vi.mock("src/api/webhooks", () => ({
  WebhooksScope: ({ children }) => children,
  useCreateWebhook: () => ({ mutate: mocks.create, reset: mocks.reset }),
  usePreview: () => ({ mutate: mocks.preview }),
  useSendTest: () => ({ mutate: vi.fn(), reset: mocks.reset }),
  useWebhookEndpoints: () => ({ data: mocks.rows }),
  useWebhookEndpoint: () => ({ data: mocks.endpoint }),
  useUpdateWebhook: () => ({ mutate: mocks.update }),
  useDeleteWebhook: () => ({}),
  useResumeWebhook: () => ({}),
  useRevokePreviousSecret: () => ({}),
  useRotateSecret: () => ({}),
  useSetWebhookEnabled: () => ({}),
  useWebhookPolicy: () => ({ data: mocks.policy }),
  useDestinations: () => ({
    data: {
      destinations: [],
      webhooks: { available: true, can_view: true, can_manage: true },
    },
  }),
}));

function Fields({ endpoint, urlPolicy, fieldError }) {
  const [form, setForm] = useState(() =>
    endpoint ? formOf(endpoint) : emptyForm(),
  );
  return (
    <WebhookFields
      form={form}
      setForm={setForm}
      access={{}}
      urlPolicy={urlPolicy}
      fieldError={fieldError}
    />
  );
}

Fields.propTypes = {
  endpoint: PropTypes.object,
  urlPolicy: PropTypes.object,
  fieldError: PropTypes.object,
};

beforeEach(() => {
  cleanup();
  vi.clearAllMocks();
  mocks.rows = [];
  mocks.policy = {};
});

describe("HTTP webhook warning", () => {
  it.each(["http://", "HTTP://", "HtTp://"])(
    "does not warn for an unaccepted %s draft",
    (scheme) => {
      render(<Fields />);
      const input = screen.getByRole("textbox", { name: /Endpoint URL/ });
      fireEvent.change(input, { target: { value: scheme } });
      expect(screen.queryByText(MESSAGE)).not.toBeInTheDocument();
      fireEvent.change(input, {
        target: { value: "https://hooks.example.com" },
      });
      expect(screen.queryByText(MESSAGE)).not.toBeInTheDocument();
    },
  );

  it("warns for a saved masked HTTP URL without requiring re-entry", () => {
    render(
      <Fields
        endpoint={{
          url: "http://hooks.example.com:8443?token=…",
          url_scheme: "http",
          subscriptions: [],
        }}
      />,
    );
    expect(screen.getByRole("textbox", { name: /Endpoint URL/ })).toHaveValue(
      "",
    );
    expect(screen.getByText(MESSAGE)).toBeInTheDocument();
    fireEvent.change(screen.getByRole("textbox", { name: /Endpoint URL/ }), {
      target: { value: "https://hooks.example.com" },
    });
    expect(screen.queryByText(MESSAGE)).not.toBeInTheDocument();
  });

  it("does not block saving HTTP and passes the API warning to the created result", async () => {
    const onCreated = vi.fn();
    const result = {
      endpoint: { id: "whep_1", url_scheme: "http", warnings: [warning] },
      secret: "test-only",
    };
    mocks.create.mockImplementation((_body, callbacks) =>
      callbacks.onSuccess({ data: { result } }),
    );
    render(
      <AddWebhookDialog
        open
        onClose={vi.fn()}
        onCreated={onCreated}
        access={{ workspace_id: "ws-1" }}
      />,
    );
    fireEvent.change(screen.getByRole("textbox", { name: /^Name/ }), {
      target: { value: "HTTP hook" },
    });
    fireEvent.change(screen.getByRole("textbox", { name: /Endpoint URL/ }), {
      target: { value: "http://hooks.example.com" },
    });
    fireEvent.click(screen.getAllByRole("checkbox")[0]);
    expect(screen.getByRole("button", { name: "Save" })).toBeEnabled();
    fireEvent.click(screen.getByRole("button", { name: "Save" }));
    await waitFor(() => expect(onCreated).toHaveBeenCalledWith(result));
    expect(mocks.create.mock.calls[0][0].url).toBe("http://hooks.example.com");
    expect(screen.queryByText(MESSAGE)).not.toBeInTheDocument();
  });

  it("shows the API warning after save even when the URL is not returned", () => {
    render(
      <SigningSecretDialog
        open
        secret="test-only"
        endpoint={{
          id: "whep_1",
          url_scheme: "http",
          warnings: [warning],
          subscriptions: [],
        }}
        onClose={vi.fn()}
      />,
    );
    expect(screen.getByText(MESSAGE)).toBeInTheDocument();
  });

  it("shows the update response warning without waiting for the endpoint refetch", async () => {
    mocks.endpoint = {
      id: "whep_1",
      name: "Existing",
      url: null,
      url_host: "hooks.example.com",
      status: "enabled",
      can_manage: true,
      subscriptions: [],
      secrets: [],
      timeout_ms: 15000,
      rate_limit_per_s: 10,
      max_in_flight: 4,
      max_age_s: 259200,
      payload_mode: "thin",
      include_people_names: false,
    };
    mocks.update.mockImplementation((body, callbacks) =>
      callbacks.onSuccess({
        data: {
          result: {
            ...mocks.endpoint,
            ...body,
            url_scheme: "http",
            warnings: [warning],
          },
        },
      }),
    );
    render(<WebhookEndpointPage />);
    expect(screen.queryByText(MESSAGE)).not.toBeInTheDocument();
    fireEvent.change(screen.getByRole("textbox", { name: /^Name/ }), {
      target: { value: "Renamed" },
    });
    fireEvent.click(screen.getByRole("button", { name: "Save changes" }));
    await waitFor(() => expect(screen.getByText("Saved.")).toBeInTheDocument());
    expect(mocks.update.mock.calls[0][0]).toEqual({ name: "Renamed" });
    expect(screen.getByText(MESSAGE)).toBeInTheDocument();
    fireEvent.change(screen.getByRole("textbox", { name: /Endpoint URL/ }), {
      target: { value: "https://hooks.example.com" },
    });
    expect(screen.queryByText(MESSAGE)).not.toBeInTheDocument();
  });

  it("marks HTTP list rows from API warnings even when URL access is restricted", async () => {
    mocks.rows = [
      {
        id: "whep_1",
        name: "HTTP",
        url_host: "hooks.example.com",
        url: null,
        status: "enabled",
        url_scheme: "http",
        warnings: [warning],
      },
      {
        id: "whep_2",
        name: "HTTPS",
        url_host: "secure.example.com",
        url: "https://secure.example.com",
        status: "enabled",
      },
    ];
    render(<WebhooksSection />);
    const badges = screen.getAllByText("Unencrypted (HTTP)");
    expect(badges).toHaveLength(1);
    fireEvent.mouseOver(badges[0]);
    expect(await screen.findByRole("tooltip")).toHaveTextContent(MESSAGE);
  });
});

describe("approved profile-dependent copy", () => {
  it.each([
    [
      { http_allowed: true },
      "An HTTPS or HTTP endpoint",
      "HTTPS or HTTP. Host and port restrictions still apply.",
    ],
    [
      { http_allowed: false, private_addresses_blocked: false },
      "An HTTPS endpoint",
      "HTTPS only. Host and port restrictions still apply.",
    ],
    [
      { http_allowed: false, private_addresses_blocked: true },
      "An HTTPS endpoint",
      "HTTPS only. Private, local and cloud metadata addresses are blocked.",
    ],
    [
      undefined,
      "An HTTPS endpoint",
      "HTTPS only. Host and port restrictions still apply.",
    ],
  ])(
    "uses only the server profile %j for endpoint and hint",
    (urlPolicy, label, hint) => {
      render(<Fields urlPolicy={urlPolicy} />);
      expect(screen.getByRole("radio", { name: label })).toBeInTheDocument();
      expect(screen.getByText(hint)).toBeInTheDocument();
      expect(screen.queryByText(MESSAGE)).not.toBeInTheDocument();
    },
  );

  it.each([true, false])(
    "wires the list and add dialog to http_allowed=%s",
    (http_allowed) => {
      mocks.policy = {
        url_policy: { http_allowed, private_addresses_blocked: !http_allowed },
      };
      render(<WebhooksSection />);
      const empty = http_allowed
        ? "No webhooks yet. Send prompt and deployment events to your own HTTPS or HTTP endpoint, signed so you can check they came from Future AGI."
        : "No webhooks yet. Send prompt and deployment events to your own HTTPS endpoint, signed so you can check they came from Future AGI.";
      expect(screen.getByText(empty)).toBeInTheDocument();
      fireEvent.click(screen.getByRole("button", { name: "Add webhook" }));
      expect(
        screen.getByRole("radio", {
          name: http_allowed
            ? "An HTTPS or HTTP endpoint"
            : "An HTTPS endpoint",
        }),
      ).toBeInTheDocument();
    },
  );

  it("renders the saved-scheme warning directly below the URL and beside query warning", () => {
    render(
      <Fields
        endpoint={{
          url: "HTTP://hooks.example.com:8443",
          url_scheme: "http",
          subscriptions: [],
        }}
      />,
    );
    const input = screen.getByRole("textbox", { name: /Endpoint URL/ });
    const field = input.closest(".MuiFormControl-root");
    expect(field.nextElementSibling).toHaveAttribute("role", "alert");
    expect(field.nextElementSibling.textContent).toBe(MESSAGE);
  });

  it("retains a rejected URL error instead of a success warning", () => {
    render(
      <Fields
        endpoint={{
          url: "http://hooks.example.com:8443",
          url_scheme: "http",
          warnings: [warning],
          subscriptions: [],
        }}
        fieldError={{ attr: "url", message: "Webhook URLs must use HTTPS." }}
      />,
    );
    expect(
      screen.getByText("Webhook URLs must use HTTPS."),
    ).toBeInTheDocument();
    expect(screen.queryByText(MESSAGE)).not.toBeInTheDocument();
  });

  it("does not infer saved transport from raw URLs or permission alone", () => {
    mocks.policy = { url_policy: { http_allowed: true } };
    mocks.rows = [
      {
        id: "1",
        name: "No indicator",
        url: "http://hooks.example.com:8443",
        status: "enabled",
      },
      {
        id: "2",
        name: "Secure indicator",
        url_scheme: "https",
        url: null,
        status: "enabled",
      },
      {
        id: "3",
        name: "Masked indicator",
        url_scheme: "http",
        url: null,
        status: "enabled",
      },
    ];
    render(<WebhooksSection />);
    expect(screen.getAllByText("Unencrypted (HTTP)")).toHaveLength(1);
  });
});
