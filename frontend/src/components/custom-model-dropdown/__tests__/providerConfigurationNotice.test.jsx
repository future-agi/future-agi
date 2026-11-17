/* eslint-disable react/prop-types */
// Settings > AI Providers: when the server marks a provider's stored key or
// settings as not valid (configuration_error on the provider status row), the
// provider's card shows the server's sentence beside the provider, with a
// warning glyph so the state does not rest on colour, and not the saved check.
// A valid key keeps the check and shows no sentence.
import React from "react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { describe, expect, it, vi } from "vitest";
import { render, screen, within } from "src/utils/test-utils";

vi.mock("src/utils/axios", () => ({
  default: { get: vi.fn(), post: vi.fn(), delete: vi.fn() },
  endpoints: {
    develop: {
      apiKey: {
        create: "/model-hub/api-keys/",
        update: "/model-hub/api-keys/",
        status: "/model-hub/develops/provider-status/",
        delete: (id) => `/model-hub/api-keys/${id}/`,
      },
    },
  },
}));
vi.mock("src/components/snackbar", () => ({ enqueueSnackbar: vi.fn() }));
// Every glyph renders its name, so a test can tell the saved check from the
// warning glyph.
vi.mock("@iconify/react", () => ({
  Icon: ({ icon }) => <span data-icon={icon} />,
}));
vi.mock("src/components/iconify", () => ({
  default: ({ icon }) => <span data-icon={icon} />,
}));
// The key field and the read-only view under the header are not under test.
vi.mock("src/components/custom-model-dropdown/APIKeyForm", () => ({
  default: () => null,
}));
vi.mock("src/components/custom-model-dropdown/APIKeyReadOnlyView", () => ({
  default: () => null,
}));
vi.mock("src/components/image", () => ({ default: () => null }));
vi.mock("src/components/svg-color", () => ({ default: () => null }));
vi.mock("src/components/svg-color/svg-color", () => ({ default: () => null }));

import KeyCard from "src/sections/develop-detail/Common/ConfigureKeys/KeyCard";
import CloudProviderCard from "src/components/custom-model-dropdown/CloudProviderCard";
import { normalizeProviderStatus } from "src/components/custom-model-dropdown/KeysHelper";

// The server's sentence, as the provider status row carries it.
const NOT_VALID =
  "This model configuration is not valid; enter the key and settings from your provider.";
const SAVED_CHECK = "gg-check-o";

const statusRow = (overrides = {}) =>
  normalizeProviderStatus({
    provider: "openai",
    display_name: "Openai",
    type: "text",
    has_key: true,
    masked_key: "sk-f**********0042",
    logo_url: null,
    id: "key-openai",
    configuration_error: null,
    ...overrides,
  });

const notValidText = statusRow({
  masked_key: "••••••••",
  configuration_error: NOT_VALID,
});
const validText = statusRow({ provider: "anthropic", id: "key-anthropic" });
const notValidJson = statusRow({
  provider: "azure",
  display_name: "Azure",
  type: "json",
  id: "key-azure",
  masked_key: { api_key: "••••••••", api_version: "2024******6-01" },
  configuration_error: NOT_VALID,
});
const validJson = statusRow({
  provider: "bedrock",
  display_name: "Bedrock",
  type: "json",
  id: "key-bedrock",
  masked_key: { aws_region_name: "us-e******st-1" },
});

function renderCards(ui) {
  const queryClient = new QueryClient({
    defaultOptions: { queries: { retry: false } },
  });
  return render(
    <QueryClientProvider client={queryClient}>{ui}</QueryClientProvider>,
  );
}

const card = (testId) => screen.getByTestId(testId);

function expectMarkedNotValid(cardElement) {
  const sentence = within(cardElement).getByText(NOT_VALID);
  // The whole sentence wraps inside the card; it is never cut short.
  expect(sentence).not.toHaveClass("MuiTypography-noWrap");
  const notice = sentence.closest(
    '[data-testid="provider-configuration-notice"]',
  );
  expect(notice).not.toBeNull();
  expect(
    notice.querySelector('[data-icon="solar:danger-triangle-linear"]'),
  ).not.toBeNull();
  expect(cardElement.querySelector(`[data-icon="${SAVED_CHECK}"]`)).toBeNull();
}

function expectSavedAndValid(cardElement) {
  expect(within(cardElement).queryByText(NOT_VALID)).toBeNull();
  expect(
    cardElement.querySelector(`[data-icon="${SAVED_CHECK}"]`),
  ).not.toBeNull();
}

describe("Settings > AI Providers: a stored key the server marks as not valid", () => {
  it("the provider status row carries the server's sentence", () => {
    expect(notValidText.configurationError).toBe(NOT_VALID);
    expect(validText.configurationError).toBeNull();
    expect(
      normalizeProviderStatus({ provider: "openai", has_key: false })
        .configurationError,
    ).toBeNull();
  });

  it("a key card shows the sentence and a warning glyph instead of the saved check", () => {
    renderCards(
      <>
        <div data-testid="not-valid-card">
          <KeyCard data={notValidText} onDeleteClick={vi.fn()} />
        </div>
        <div data-testid="valid-card">
          <KeyCard data={validText} onDeleteClick={vi.fn()} />
        </div>
      </>,
    );

    expectMarkedNotValid(card("not-valid-card"));
    expectSavedAndValid(card("valid-card"));
  });

  it("a key card for settings shows the sentence and a warning glyph instead of the saved check", () => {
    renderCards(
      <>
        <div data-testid="not-valid-card">
          <KeyCard data={notValidJson} onDeleteClick={vi.fn()} />
        </div>
        <div data-testid="valid-card">
          <KeyCard data={validJson} onDeleteClick={vi.fn()} />
        </div>
      </>,
    );

    expectMarkedNotValid(card("not-valid-card"));
    expectSavedAndValid(card("valid-card"));
  });

  it("a cloud provider card shows the sentence and a warning glyph instead of the saved check", () => {
    renderCards(
      <>
        <div data-testid="not-valid-card">
          <CloudProviderCard
            provider={notValidJson}
            showJsonField
            onDeleteClick={vi.fn()}
          />
        </div>
        <div data-testid="valid-card">
          <CloudProviderCard
            provider={validJson}
            showJsonField
            onDeleteClick={vi.fn()}
          />
        </div>
      </>,
    );

    expectMarkedNotValid(card("not-valid-card"));
    expectSavedAndValid(card("valid-card"));
  });
});
