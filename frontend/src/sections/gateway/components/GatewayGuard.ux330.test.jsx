import React from "react";
import { createTheme, getContrastRatio } from "@mui/material/styles";
import { HelmetProvider } from "react-helmet-async";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { render, screen, userEvent, within } from "src/utils/test-utils";
import { palette } from "src/theme/palette";
import GatewayGuard from "./GatewayGuard";
import GatewayEmptyState from "./GatewayEmptyState";

const context = vi.hoisted(() => ({ value: {} }));
vi.mock("../context/useGatewayContext", () => ({
  useGatewayContext: () => context.value,
}));

const renderGuard = (options) =>
  render(
    <HelmetProvider>
      <GatewayGuard>
        <div>Gateway content</div>
      </GatewayGuard>
    </HelmetProvider>,
    options,
  );

beforeEach(() => {
  context.value = {
    gatewayId: null,
    isLoading: false,
    error: null,
    refreshGateways: vi.fn(),
  };
});

describe("GWUX-01 gateway unavailable presentation", () => {
  it("keeps the shared page frame and h1 when no gateway resolves", () => {
    renderGuard();
    const frame = screen.getByTestId("gateway-guard-empty");
    expect(
      within(frame).getByRole("heading", { level: 1, name: "AI Gateway" }),
    ).toBeInTheDocument();
    expect(
      frame.querySelector('[data-clearance="floating-button"]'),
    ).toBeInTheDocument();
    expect(screen.queryByText("Gateway content")).not.toBeInTheDocument();
  });

  it("gives hosted and self-hosted users neutral next steps and keeps Retry", async () => {
    renderGuard();
    expect(
      screen.getByText(
        "We couldn't load the gateway page. Retry in a moment. If this continues, contact your administrator.",
      ),
    ).toBeInTheDocument();
    expect(screen.queryByText(/gateway container/i)).not.toBeInTheDocument();
    await userEvent.click(screen.getByRole("button", { name: "Retry" }));
    expect(context.value.refreshGateways).toHaveBeenCalledTimes(1);
  });

  it.each(["light", "dark"])(
    "shows only the fixed failure message with accessible danger text in %s mode",
    (mode) => {
      const theme = createTheme({ palette: palette(mode) });
      context.value.error = Object.assign(
        new Error("Unable to load gateways"),
        {
          stack: "PRIVATE_STACK_TRACE",
          response: { data: "RAW_RESPONSE_BODY" },
        },
      );
      renderGuard({ theme });
      const detail = screen.getByText("The gateway request failed.");
      expect(detail).toHaveStyle({ color: theme.palette.status.danger.fg });
      expect(
        getContrastRatio(
          window.getComputedStyle(detail).color,
          theme.palette.background.paper,
        ),
      ).toBeGreaterThanOrEqual(4.5);
      expect(
        screen.queryByText(
          /Unable to load gateways|PRIVATE_STACK_TRACE|RAW_RESPONSE_BODY/,
        ),
      ).not.toBeInTheDocument();
      expect(screen.getByRole("button", { name: "Retry" })).toBeInTheDocument();
    },
  );

  describe.each(["guard", "empty state"])(
    "%s error-message hardening",
    (subject) => {
      it.each([
        ["object", { detail: "NOT_A_STRING" }],
        ["array", ["NOT_A_STRING"]],
        ["number", 12345],
        ["zero", 0],
        ["boolean", true],
        ["null", null],
        ["undefined", undefined],
        ["empty string", ""],
        ["whitespace", " \n\t "],
      ])(
        "never displays a %s message or breaks recovery",
        (_label, message) => {
          context.value.error = { message };
          if (subject === "guard") {
            renderGuard();
          } else {
            render(
              <GatewayEmptyState
                variant="no-gateway"
                errorMessage={message}
                onAction={context.value.refreshGateways}
              />,
            );
          }
          const description = screen.getByText(
            "We couldn't load the gateway page. Retry in a moment. If this continues, contact your administrator.",
          );
          if (subject === "guard") {
            expect(description.querySelector("span")).toHaveTextContent(
              "The gateway request failed.",
            );
          } else {
            expect(description.querySelector("span")).toBeNull();
            expect(description.textContent.trim()).toBe(
              "We couldn't load the gateway page. Retry in a moment. If this continues, contact your administrator.",
            );
          }
          expect(
            screen.queryByText(/NOT_A_STRING|12345/),
          ).not.toBeInTheDocument();
          expect(screen.getByRole("button", { name: "Retry" })).toBeEnabled();
        },
      );
    },
  );

  it.each([null, undefined])(
    "omits failure detail when error is %s",
    (error) => {
      context.value.error = error;
      renderGuard();
      expect(
        screen.queryByText("The gateway request failed."),
      ).not.toBeInTheDocument();
      expect(screen.getByRole("button", { name: "Retry" })).toBeEnabled();
    },
  );

  it("does not read the server-provided message", () => {
    context.value.error = {
      get message() {
        throw new Error("Server message must not be read");
      },
    };
    renderGuard();
    expect(screen.getByText("The gateway request failed.")).toBeInTheDocument();
  });

  it("preserves loading and resolved gateway behaviour", () => {
    context.value.isLoading = true;
    const view = renderGuard();
    expect(screen.getByTestId("gateway-guard-loading")).toBeInTheDocument();
    expect(
      screen.queryByRole("button", { name: "Retry" }),
    ).not.toBeInTheDocument();
    context.value = {
      ...context.value,
      isLoading: false,
      gatewayId: "gateway-1",
    };
    view.rerender(
      <HelmetProvider>
        <GatewayGuard>
          <div>Gateway content</div>
        </GatewayGuard>
      </HelmetProvider>,
    );
    expect(screen.getByText("Gateway content")).toBeInTheDocument();
    expect(screen.queryByTestId("gateway-guard-empty")).not.toBeInTheDocument();
  });
});
