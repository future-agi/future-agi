import React from "react";
import { describe, expect, it, vi } from "vitest";
import { render, screen } from "src/utils/test-utils";

import ProviderDetailDrawer from "../ProviderDetailDrawer";

// A provider row as the Providers list builds it from the gateway config
// read: `models` are the saved IDs offered to requests, and the read's
// dropped_model_count says how many saved IDs it held back.
const row = (models, dropped) => ({
  id: "openai",
  name: "openai",
  displayName: "OpenAI",
  baseUrl: "https://llm.example.com",
  apiFormat: "openai",
  models,
  status: "not_pushed",
  statusReason: "",
  requests: null,
  avgLatencyMs: null,
  errorRate: null,
  config: dropped == null ? {} : { dropped_model_count: dropped },
});

const renderDrawer = (models, dropped) =>
  render(
    <ProviderDetailDrawer
      row={row(models, dropped)}
      onClose={vi.fn()}
      canWrite
      onEdit={vi.fn()}
      onRemove={vi.fn()}
      orgProviders={[]}
    />,
  );

describe("Provider drawer: saved models held back", () => {
  it("says how many saved models are held back and how to offer them again", () => {
    renderDrawer([], 3);
    expect(
      screen.getByText(
        "3 saved models are held back and aren't offered to requests. Replace this provider's credential to offer them again.",
      ),
    ).toBeInTheDocument();
    expect(screen.queryByText(/No models selected/)).toBeNull();
  });

  it("uses the singular for one held-back model", () => {
    renderDrawer([], 1);
    expect(
      screen.getByText(
        "1 saved model is held back and isn't offered to requests. Replace this provider's credential to offer it again.",
      ),
    ).toBeInTheDocument();
    expect(screen.queryByText(/No models selected/)).toBeNull();
  });

  it("shows the offered models and, under them, the held-back sentence", () => {
    renderDrawer(["gpt-4o"], 2);
    expect(screen.getByText("Models (1)")).toBeInTheDocument();
    expect(screen.getByText("gpt-4o")).toBeInTheDocument();
    expect(
      screen.getByText(
        "2 saved models are held back and aren't offered to requests. Replace this provider's credential to offer them again.",
      ),
    ).toBeInTheDocument();
  });

  it("keeps the empty state when nothing is held back", () => {
    renderDrawer([], 0);
    expect(
      screen.getByText("No models selected. Edit the provider to choose some."),
    ).toBeInTheDocument();
    expect(screen.queryByText(/held back/)).toBeNull();
  });
});
