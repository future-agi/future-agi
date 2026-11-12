import { beforeEach, describe, expect, it, vi } from "vitest";
import { render, screen } from "src/utils/test-utils";
import WebhookEndpointPage from "../WebhookEndpointPage";

// A webhook's page stays readable at 1024: a long name or host wraps under
// the title, and Turn off, Send test and More stay in view beside it. jsdom
// does not lay out, so the test checks the styles that make the row behave.

const NAME = "production_prompt_release_notifications_for_the_platform_team";
const HOST = "hooks.internal.examplecompanyproductionenvironment.example.com";

const mocks = vi.hoisted(() => ({ endpoint: null }));

vi.mock("src/components/iconify", () => ({ default: () => null }));
vi.mock("src/utils/utils", () => ({ copyToClipboard: vi.fn() }));
vi.mock("../WebhookPolicyCard", () => ({ default: () => null }));
vi.mock("../DeliveriesCard", () => ({ default: () => null }));
vi.mock("../ActivityCard", () => ({ default: () => null }));
vi.mock("react-helmet-async", () => ({ Helmet: () => null }));
vi.mock("src/components/loading-screen", () => ({ LoadingScreen: () => null }));
vi.mock("src/api/webhooks", () => ({
  WebhooksScope: ({ children }) => children,
  useCreateWebhook: () => ({ mutate: vi.fn(), reset: vi.fn() }),
  usePreview: () => ({ mutate: vi.fn() }),
  useSendTest: () => ({ mutate: vi.fn(), reset: vi.fn() }),
  useWebhookEndpoints: () => ({ data: [] }),
  useWebhookEndpoint: () => ({ data: mocks.endpoint }),
  useUpdateWebhook: () => ({ mutate: vi.fn() }),
  useDeleteWebhook: () => ({}),
  useResumeWebhook: () => ({}),
  useRevokePreviousSecret: () => ({}),
  useRotateSecret: () => ({}),
  useSetWebhookEnabled: () => ({}),
  useWebhookPolicy: () => ({ data: {} }),
  useDestinations: () => ({
    data: {
      destinations: [],
      webhooks: { available: true, can_view: true, can_manage: true },
    },
  }),
}));

beforeEach(() => {
  vi.clearAllMocks();
  mocks.endpoint = {
    id: "whep_1",
    name: NAME,
    url: null,
    url_host: HOST,
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
});

describe("Webhook endpoint page stays readable at 1024", () => {
  it("a long name and host wrap under the title and the actions stay in view", () => {
    render(<WebhookEndpointPage />);
    const title = screen.getByRole("heading", { level: 1, name: NAME });
    expect(title).toHaveStyle({ overflowWrap: "anywhere" });
    const host = title.nextElementSibling;
    expect(host).toHaveTextContent(HOST);
    expect(host).toHaveStyle({ overflowWrap: "anywhere" });
    expect(title.parentElement).toHaveStyle({ minWidth: "0" });
    expect(
      screen.getByRole("button", { name: "Turn off" }).parentElement,
    ).toHaveStyle({ flexShrink: "0" });
  });
});
