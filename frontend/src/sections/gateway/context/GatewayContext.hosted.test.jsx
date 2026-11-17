import React from "react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { render, screen, waitFor, within } from "src/utils/test-utils";
import userEvent from "@testing-library/user-event";
import { HelmetProvider } from "react-helmet-async";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";

import { endpoints } from "src/utils/axios";

import { GatewayProvider } from "./GatewayContext";
import { useGatewayContext } from "./useGatewayContext";
import GatewayOverviewPage from "../overview/GatewayOverviewPage";

// Every AI Gateway page that shows a request example reads from the context
// whether this is a hosted deployment (Future AGI Cloud), to show the
// gateway's configured public URL there rather than a placeholder
// (utils/snippetBaseUrl.js). Only deployment mode "cloud" is hosted; "oss" and
// "ee" are self-hosted only after the lookup confirms a recognized mode.
// Pending, failed and missing/unrecognized responses must not emit requests.

const { deployment, reads, publicUrl } = vi.hoisted(() => ({
  deployment: { answer: null },
  reads: [],
  // R-LIT synthetic: a visibly fake public gateway URL (RFC 2606 .test host),
  // the value AGENTCC_GATEWAY_PUBLIC_URL would give a hosted deployment.
  publicUrl: "https://gateway.example.test/v1",
}));

vi.mock("src/utils/axios", async (importOriginal) => {
  const actual = await importOriginal();
  return {
    ...actual,
    default: {
      ...actual.default,
      get: vi.fn(async (url) => {
        reads.push(url);
        if (url === actual.endpoints.gateway.list) {
          return {
            data: {
              result: [
                {
                  id: "default",
                  name: "Default",
                  status: "healthy",
                  base_url: publicUrl,
                },
              ],
            },
          };
        }
        if (url === actual.endpoints.settings.v2.deploymentInfo) {
          if (deployment.answer instanceof Error) throw deployment.answer;
          if (deployment.answer === "pending") return new Promise(() => {});
          return { data: { result: { mode: deployment.answer } } };
        }
        if (url === actual.endpoints.gateway.managedModels) {
          return { data: { status: true, result: { available: false } } };
        }
        throw new Error(`unmocked GET ${url}`);
      }),
    },
  };
});

function Reads() {
  const { gateway, hosted, deploymentModeConfirmed } = useGatewayContext();
  return (
    <>
      <div>{`baseUrl=${gateway?.baseUrl ?? ""}`}</div>
      <div>{`hosted=${String(hosted)}`}</div>
      <div>{`confirmed=${String(deploymentModeConfirmed)}`}</div>
    </>
  );
}

// Keep the provider, deployment query, Overview and clipboard controls real.
// Only unrelated page reads/widgets are isolated.
vi.mock("ag-grid-react", () => ({ AgGridReact: () => null }));
vi.mock("src/contexts/OrganizationContext", () => ({
  useOrganization: () => ({ orgLevel: 15 }),
}));
vi.mock("../providers/hooks/useGatewayConfig", () => ({
  useGatewayConfig: () => ({ data: {} }),
  // R-LIT synthetic: one applied provider measured healthy (20 requests, over
  // the 5 a health reading needs) with one model, so the Overview has a
  // configured route to build its request example on.
  useProviderHealth: () => ({
    data: {
      providers: [
        {
          name: "openai",
          pushed: true,
          healthy: true,
          request_count: 20,
          models: ["gpt-4o-mini"],
        },
      ],
    },
  }),
}));
vi.mock("../analytics/hooks/useAnalyticsOverview", () => ({
  useAnalyticsOverview: () => ({ data: null }),
}));
vi.mock("../keys/hooks/useApiKeys", () => ({
  useApiKeys: () => ({ data: [] }),
}));
vi.mock("../providers/hooks/useOrgConfig", () => ({
  useOrgConfig: () => ({ data: {} }),
}));
vi.mock("../onboarding/useRequestCount", () => ({
  useRequestCount: () => ({ data: 0 }),
}));
vi.mock("../overview/GatewaySnippetKey", () => ({
  default: ({ children }) => children(""),
}));

const renderProvider = (page = <Reads />) => {
  const client = new QueryClient({
    defaultOptions: { queries: { retry: false, retryDelay: 0 } },
  });
  return {
    client,
    ...render(
      <QueryClientProvider client={client}>
        <HelmetProvider>
          <GatewayProvider>{page}</GatewayProvider>
        </HelmetProvider>
      </QueryClientProvider>,
    ),
  };
};

describe("GatewayContext hosted", () => {
  beforeEach(() => {
    deployment.answer = null;
    reads.length = 0;
  });

  it("is true on a cloud deployment, beside the gateway's reported URL", async () => {
    deployment.answer = "cloud";
    renderProvider();
    expect(await screen.findByText("hosted=true")).toBeInTheDocument();
    expect(screen.getByText("confirmed=true")).toBeInTheDocument();
    expect(await screen.findByText(`baseUrl=${publicUrl}`)).toBeInTheDocument();
  });

  it.each(["oss", "ee"])(
    "is false on a %s (self-hosted) deployment",
    async (mode) => {
      deployment.answer = mode;
      renderProvider();
      expect(
        await screen.findByText(`baseUrl=${publicUrl}`),
      ).toBeInTheDocument();
      // The mode was asked for, and it did not make the deployment hosted.
      await waitFor(() =>
        expect(reads).toContain(endpoints.settings.v2.deploymentInfo),
      );
      expect(screen.getByText("hosted=false")).toBeInTheDocument();
      expect(await screen.findByText("confirmed=true")).toBeInTheDocument();
    },
  );

  it("does not confirm a pending or failed deployment lookup", async () => {
    deployment.answer = "pending";
    const pending = renderProvider();
    expect(await screen.findByText(`baseUrl=${publicUrl}`)).toBeInTheDocument();
    expect(screen.getByText("confirmed=false")).toBeInTheDocument();
    pending.unmount();
    pending.client.clear();

    deployment.answer = new Error("Deployment info unavailable");
    const { client } = renderProvider();
    await waitFor(() =>
      expect(client.getQueryState(["deployment-info"]).status).toBe("error"),
    );
    expect(screen.getByText(`baseUrl=${publicUrl}`)).toBeInTheDocument();
    expect(screen.getByText("confirmed=false")).toBeInTheDocument();
  });

  // A failed deployment-info read never presents an address as real: the
  // Overview shows the marked placeholder and says so, and never offers it as
  // the endpoint. Retry recovers the real page.
  it.each([new Error("Deployment info unavailable"), null, "unknown"])(
    "builds Overview requests on the marked placeholder for an unconfirmed mode and recovers on retry: %s",
    async (answer) => {
      const user = userEvent.setup();
      const writeText = vi.spyOn(navigator.clipboard, "writeText");
      deployment.answer = answer;
      const { client } = renderProvider(<GatewayOverviewPage />);
      const panel = await screen.findByTestId("overview-endpoint");
      await waitFor(() =>
        expect(client.getQueryState(["deployment-info"]).status).toBe(
          answer instanceof Error ? "error" : "success",
        ),
      );
      expect(client.getQueryData(["agentcc-gateways"]).result[0].base_url).toBe(
        publicUrl,
      );
      expect(panel.textContent).toContain(
        "isn't available from the console right now",
      );
      const code = panel.querySelector("pre");
      if (code) expect(code.textContent).toContain("YOUR_GATEWAY_URL");
      expect(panel.textContent).not.toContain(publicUrl);
      expect(
        within(panel).queryByRole("button", { name: "Copy endpoint" }),
      ).toBeNull();
      expect(writeText).not.toHaveBeenCalled();

      const previousReads = reads.filter(
        (url) => url === endpoints.settings.v2.deploymentInfo,
      ).length;
      deployment.answer = "cloud";
      await user.click(
        within(panel).getByRole("button", { name: "Retry deployment mode" }),
      );
      const copy = await within(panel).findByRole("button", {
        name: "Copy cURL",
      });
      expect(
        reads.filter((url) => url === endpoints.settings.v2.deploymentInfo)
          .length,
      ).toBeGreaterThan(previousReads);
      expect(panel.querySelector("pre").textContent).toContain(publicUrl);
      expect(panel.textContent).not.toMatch(/YOUR_GATEWAY_URL|localhost/i);
      await user.click(copy);
      expect(writeText).toHaveBeenLastCalledWith(
        panel.querySelector("pre").textContent,
      );
      writeText.mockRestore();
    },
  );
});
