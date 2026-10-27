import React from "react";
import { describe, it, expect, vi, beforeEach } from "vitest";
import { act, fireEvent, render, screen, waitFor } from "src/utils/test-utils";
import { createTheme } from "@mui/material/styles";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import UploadLimitNotification from "../RateLimitModal";

const mocks = vi.hoisted(() => ({
  socket: new EventTarget(),
  navigate: vi.fn(),
  get: vi.fn(),
  post: vi.fn(),
}));

vi.mock("src/hooks/use-socket", () => ({
  useSocket: () => ({ socket: mocks.socket }),
}));

vi.mock("react-router", async (importOriginal) => {
  const actual = await importOriginal();
  return {
    ...actual,
    useNavigate: () => mocks.navigate,
  };
});

vi.mock("src/utils/axios", () => ({
  default: {
    get: (...args) => mocks.get(...args),
    post: (...args) => mocks.post(...args),
  },
  endpoints: {
    settings: {
      v2: {
        plansAndAddons: "/usage/v2/plans-and-addons/",
        upgradeToPayg: "/usage/v2/upgrade-to-payg/",
      },
    },
  },
}));

vi.mock("notistack", () => ({
  enqueueSnackbar: vi.fn(),
  closeSnackbar: vi.fn(),
}));

const theme = createTheme({
  palette: {
    primary: {
      main: "#6f4ef2",
      lighter: "#ede8ff",
    },
    red: {
      500: "#f04438",
    },
    pink: {
      500: "#ec4899",
    },
  },
});

describe("UploadLimitNotification", () => {
  beforeEach(() => {
    mocks.socket = new EventTarget();
    mocks.navigate.mockReset();
    mocks.get.mockResolvedValue({
      data: {
        result: {
          current_plan: "free",
          tiers: [
            {
              key: "free",
              display_name: "Free",
              platform_fee_monthly: 0,
              features: {
                monitors: 3,
                retention_traces_days: 30,
              },
            },
            {
              key: "payg",
              display_name: "Pay-as-you-go",
              platform_fee_monthly: 0,
              features: {
                monitors: -1,
                has_agentic_eval: true,
              },
            },
          ],
          addons: [
            {
              key: "enterprise",
              display_name: "Enterprise",
              platform_fee_monthly: 2000,
              features: {
                monitors: -1,
                has_scim: true,
              },
            },
          ],
        },
      },
    });
    mocks.post.mockResolvedValue({
      data: {
        result: {
          checkout_url: "https://checkout.stripe.test/session",
        },
      },
    });
  });

  it("opens pricing dialog from upgrade CTA without routing away", async () => {
    const qc = new QueryClient({
      defaultOptions: { queries: { retry: false } },
    });
    render(
      <QueryClientProvider client={qc}>
        <UploadLimitNotification />
      </QueryClientProvider>,
      { theme },
    );

    act(() => {
      mocks.socket.dispatchEvent(
        new MessageEvent("message", {
          data: JSON.stringify({
            type: "rate_limit_notification",
            alert_title: "Minutely Turing large evaluator limit reached.",
            alert_description:
              "You have reached the minutely turing large evaluator limit.",
            subscription_title: "Want to process more data per minute?",
            subscription_description: "Upgrade to continue immediately.",
          }),
        }),
      );
    });

    const upgradeLink = await screen.findByText("Upgrade now");

    fireEvent.click(upgradeLink);

    expect(
      await screen.findByText("Want to process more data per minute?"),
    ).toBeInTheDocument();
    expect(screen.getByText("Upgrade to PAYG")).toBeInTheDocument();
    expect(screen.getByText("Usage-based")).toBeInTheDocument();
    expect(screen.getByText("No monthly platform fee")).toBeInTheDocument();
    expect(mocks.get).toHaveBeenCalledWith("/usage/v2/plans-and-addons/");
    await waitFor(() => expect(mocks.navigate).not.toHaveBeenCalled());
  });
});
