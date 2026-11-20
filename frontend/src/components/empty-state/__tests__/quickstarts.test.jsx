import React from "react";
import { describe, it, expect } from "vitest";
import { render, screen } from "@testing-library/react";

import EmptyState from "../EmptyState";
import { quickstartTracing } from "../quickstarts";

const SELF_HOSTED = {
  apiUrl: "http://localhost:8001",
  tracesUrl: "http://localhost:4319",
};

const renderQuickstart = (sdkUrls) =>
  render(
    <EmptyState
      title="No traces yet"
      quickstart={quickstartTracing("my-project", sdkUrls)}
    />,
  );

describe("the Observe empty-state quickstart", () => {
  it("points a self-hosted SDK at the install's API and collector", () => {
    renderQuickstart(SELF_HOSTED);

    const code = screen.getByText(/pip install/).textContent;
    expect(code).toContain('export FI_BASE_URL="http://localhost:8001"');
    expect(code).toContain('export FI_TRACES_URL="http://localhost:4319"');
    expect(code).toContain('pip install -U "fi-instrumentation-otel>=1.5.0"');
  });

  it("keeps the SDK defaults on Future AGI Cloud and while the URLs load", () => {
    for (const sdkUrls of [{ apiUrl: null, tracesUrl: null }, undefined]) {
      const { unmount } = renderQuickstart(sdkUrls);

      const code = screen.getByText(/pip install/).textContent;
      expect(code).not.toContain("FI_BASE_URL");
      expect(code).not.toContain("FI_TRACES_URL");
      expect(code).toContain('export FI_API_KEY="your-api-key"');
      unmount();
    }
  });
});
