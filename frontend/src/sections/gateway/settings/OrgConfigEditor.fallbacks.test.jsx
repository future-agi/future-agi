import React from "react";
import PropTypes from "prop-types";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen, waitFor } from "src/utils/test-utils";
import userEvent from "@testing-library/user-event";
import OrgConfigEditor from "./OrgConfigEditor";
import fixtures from "../../../../../api_contracts/gateway/fixtures/fallback-editor-patches.json";

const { post } = vi.hoisted(() => ({ post: vi.fn() }));
vi.mock("src/utils/axios", () => ({
  default: { post },
  endpoints: {
    gateway: {
      updateConfig: (id) => `/agentcc/gateways/${id}/update-config/`,
    },
  },
}));
vi.mock("notistack", () => ({ enqueueSnackbar: vi.fn() }));
vi.mock("src/contexts/OrganizationContext", () => ({
  useOrganization: () => ({ orgLevel: 8 }),
}));
vi.mock("../context/useGatewayContext", () => ({
  useGatewayContext: () => ({ gatewayId: "gw-1" }),
}));
vi.mock("../providers/hooks/useGatewayConfig", () => ({
  useGatewayConfig: () => ({ data: { org_sections_enforced: ["cache"] } }),
  refreshGatewayConfig: vi.fn(),
  asRequestError: (err) => err,
}));
vi.mock("../hooks/useAvailableModels", () => ({
  useAvailableModels: () => [],
}));
vi.mock("./ProviderConfigTab", () => ({ default: () => null }));
vi.mock("./GuardrailConfigTab", () => ({ default: () => null }));
vi.mock("./CacheConfigTab", () => ({ default: () => null }));
vi.mock("./BudgetsConfigTab", () => ({ default: () => null }));
vi.mock("./SecurityConfigTab", () => ({ default: () => null }));
vi.mock("./AlertingConfigTab", () => ({ default: () => null }));
vi.mock("./ModelDatabaseConfigTab", () => ({ default: () => null }));
vi.mock("./ModelMapConfigTab", () => ({ default: () => null }));

// Cache is read-only under RL-917; use another editable section to exercise
// the unchanged fallback-alias preservation contract.
const MockRateLimitingTab = ({ onChange }) => (
  <button type="button" onClick={() => onChange({ enabled: true })}>
    Enable rate limiting
  </button>
);
MockRateLimitingTab.propTypes = { onChange: PropTypes.func.isRequired };
vi.mock("./RateLimitingConfigTab", () => ({
  default: (props) => <MockRateLimitingTab {...props} />,
}));

// Keep the real RoutingConfigTab handlers and OrgConfigEditor save path.
// Only the individual chain inputs are simplified; limit/trim controls have
// their own real-component coverage in FallbackBounds.test.jsx.
const MockChainEditor = ({
  primaryModel,
  fallbacks,
  onChange,
  onRemoveChain,
}) => (
  <div>
    <button type="button" onClick={onRemoveChain}>
      Remove {primaryModel}
    </button>
    <button type="button" onClick={() => onChange("renamed", fallbacks)}>
      Rename {primaryModel}
    </button>
    <button
      type="button"
      onClick={() => onChange(primaryModel, fallbacks.slice(0, 15))}
    >
      Trim {primaryModel}
    </button>
  </div>
);
MockChainEditor.propTypes = {
  primaryModel: PropTypes.string.isRequired,
  fallbacks: PropTypes.array.isRequired,
  onChange: PropTypes.func.isRequired,
  onRemoveChain: PropTypes.func.isRequired,
};
vi.mock("./FallbackChainEditor", () => ({
  default: (props) => <MockChainEditor {...props} />,
}));

let client;
beforeEach(() => {
  post.mockReset();
  post.mockResolvedValue({ data: { result: { gateway_synced: true } } });
  client = new QueryClient({
    defaultOptions: { mutations: { retry: false }, queries: { retry: false } },
  });
});

function openEditor(routing) {
  const onClose = vi.fn();
  render(
    <QueryClientProvider client={client}>
      <OrgConfigEditor
        open
        onClose={onClose}
        initialConfig={{ routing }}
        defaultTab={2}
      />
    </QueryClientProvider>,
  );
  return onClose;
}

// Shared with the fallback page's patch-builder tests and Django API tests.
describe.each([
  ["camel-only", { modelFallbacks: fixtures.chains }],
  ["canonical-only", { model_fallbacks: fixtures.chains }],
  [
    "both-spelling",
    {
      model_fallbacks: fixtures.chains,
      modelFallbacks: fixtures.shadowedChains,
    },
  ],
])("OrgConfigEditor fallback patch over %s storage", (_, stored) => {
  it.each(fixtures.edits)("$name", async ({ actions, patch }) => {
    const user = userEvent.setup();
    const routing = { strategy: "cost", ...stored };
    const original = JSON.parse(JSON.stringify(routing));
    const onClose = openEditor(routing);

    for (const name of actions) {
      await user.click(screen.getByRole("button", { name }));
    }
    await user.click(screen.getByRole("button", { name: "Save & Activate" }));

    await waitFor(() => expect(onClose).toHaveBeenCalledTimes(1));
    expect(post).toHaveBeenCalledTimes(1);
    expect(post).toHaveBeenCalledWith(
      "/agentcc/gateways/gw-1/update-config/",
      patch,
      expect.anything(),
    );
    expect(routing).toEqual(original);
  });

  it("does not post an untouched draft", async () => {
    const onClose = openEditor(stored);
    await userEvent.click(
      screen.getByRole("button", { name: "Save & Activate" }),
    );
    await waitFor(() => expect(onClose).toHaveBeenCalledTimes(1));
    expect(post).not.toHaveBeenCalled();
  });

  it("does not migrate fallback aliases when saving another section", async () => {
    const user = userEvent.setup();
    const onClose = openEditor(stored);
    await user.click(screen.getByRole("tab", { name: "Rate Limiting" }));
    await user.click(
      screen.getByRole("button", { name: "Enable rate limiting" }),
    );
    await user.click(screen.getByRole("button", { name: "Save & Activate" }));
    await waitFor(() => expect(onClose).toHaveBeenCalledTimes(1));
    expect(post.mock.calls[0][1]).toEqual({ rate_limiting: { enabled: true } });
  });
});
