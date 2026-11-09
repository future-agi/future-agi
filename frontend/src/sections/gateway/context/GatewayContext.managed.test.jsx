import React from "react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { render, screen } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";

import { GatewayProvider } from "./GatewayContext";
import { useGatewayContext } from "./useGatewayContext";

// Every AI Gateway page can read whether this deployment offers managed
// models (GET /agentcc/managed-models/ available), to leave managed
// sentences out where none are offered. Until the status says they are
// offered, it reads false.

const { managed } = vi.hoisted(() => ({ managed: { answer: null } }));

vi.mock("src/utils/axios", async (importOriginal) => {
  const actual = await importOriginal();
  return {
    ...actual,
    default: {
      ...actual.default,
      get: vi.fn(async (url) => {
        if (url === actual.endpoints.gateway.list) {
          return { data: { result: [{ id: "default", name: "Default" }] } };
        }
        if (url === actual.endpoints.gateway.managedModels) {
          if (managed.answer instanceof Error) throw managed.answer;
          if (managed.answer === "pending") return new Promise(() => {});
          return { data: { status: true, result: managed.answer } };
        }
        throw new Error(`unmocked GET ${url}`);
      }),
    },
  };
});

function Reads() {
  const { managedOffered } = useGatewayContext();
  return <div>{`managedOffered=${String(managedOffered)}`}</div>;
}

const renderProvider = () =>
  render(
    <QueryClientProvider
      client={
        new QueryClient({ defaultOptions: { queries: { retry: false } } })
      }
    >
      <GatewayProvider>
        <Reads />
      </GatewayProvider>
    </QueryClientProvider>,
  );

describe("GatewayContext managedOffered", () => {
  beforeEach(() => {
    managed.answer = null;
  });

  it("is true where the deployment offers managed models", async () => {
    managed.answer = {
      available: true,
      enabled: false,
      balance_usd: 0,
      markup_percent: 5,
    };
    renderProvider();
    expect(await screen.findByText("managedOffered=true")).toBeInTheDocument();
  });

  it("is false where it offers none", async () => {
    managed.answer = {
      available: false,
      enabled: false,
      balance_usd: null,
      markup_percent: null,
    };
    renderProvider();
    expect(await screen.findByText("managedOffered=false")).toBeInTheDocument();
  });

  it("is false while the status loads and when it cannot be read", async () => {
    managed.answer = "pending";
    const { unmount } = renderProvider();
    expect(screen.getByText("managedOffered=false")).toBeInTheDocument();
    unmount();

    managed.answer = new Error("Gateway error");
    renderProvider();
    expect(await screen.findByText("managedOffered=false")).toBeInTheDocument();
  });
});
