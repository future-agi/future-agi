import { afterAll, beforeEach, describe, expect, it, vi } from "vitest";
import userEvent from "@testing-library/user-event";
import { renderWithRouter, screen, waitFor } from "src/utils/test-utils";
import { destinationTourDismissalKey } from "./destinationTourDismissal";
import DestinationTourAnchor from "./DestinationTourAnchor";

const originalScrollIntoView = window.HTMLElement.prototype.scrollIntoView;
const scrollIntoView = vi.fn();

describe("DestinationTourAnchor", () => {
  beforeEach(() => {
    scrollIntoView.mockClear();
    window.localStorage.clear();
    window.sessionStorage.clear();
    window.sessionStorage.setItem("currentUserId", "usr-tour");
    window.HTMLElement.prototype.scrollIntoView = scrollIntoView;
  });

  afterAll(() => {
    window.HTMLElement.prototype.scrollIntoView = originalScrollIntoView;
  });

  it("highlights and explains the current destination action", async () => {
    renderWithRouter(
      <>
        <button type="button" data-tour-anchor="gateway_request_button">
          Send test request
        </button>
        <DestinationTourAnchor maxAttempts={1} />
      </>,
      {
        route:
          "/dashboard/gateway?tour_anchor=gateway_request_button&journey_step=run_gateway_request",
      },
    );

    const target = screen.getByRole("button", { name: /send test request/i });
    expect(await screen.findByTestId("destination-tour-anchor")).toBeVisible();
    expect(screen.getByText("Send request")).toBeVisible();
    expect(target).toHaveAttribute("data-onboarding-tour-active", "true");
    expect(scrollIntoView).toHaveBeenCalledWith({
      block: "center",
      behavior: "smooth",
    });

    await userEvent.click(screen.getByRole("button", { name: /got it/i }));

    await waitFor(() =>
      expect(screen.queryByTestId("destination-tour-anchor")).toBeNull(),
    );
    expect(target).not.toHaveAttribute("data-onboarding-tour-active");
    expect(
      JSON.parse(
        window.localStorage.getItem(destinationTourDismissalKey("usr-tour")),
      ),
    ).toEqual(["gateway_request_button"]);
  });

  it("stays hidden when no tour anchor is present", () => {
    renderWithRouter(
      <>
        <button type="button" data-tour-anchor="gateway_request_button">
          Send test request
        </button>
        <DestinationTourAnchor maxAttempts={1} />
      </>,
      { route: "/dashboard/gateway" },
    );

    expect(screen.queryByTestId("destination-tour-anchor")).toBeNull();
  });

  it("keeps dismissed anchors hidden until replay is requested", async () => {
    window.localStorage.setItem(
      destinationTourDismissalKey("usr-tour"),
      JSON.stringify(["gateway_request_button"]),
    );

    const { unmount } = renderWithRouter(
      <>
        <button type="button" data-tour-anchor="gateway_request_button">
          Send test request
        </button>
        <DestinationTourAnchor maxAttempts={1} />
      </>,
      {
        route:
          "/dashboard/gateway?tour_anchor=gateway_request_button&journey_step=run_gateway_request",
      },
    );

    expect(screen.queryByTestId("destination-tour-anchor")).toBeNull();
    unmount();

    renderWithRouter(
      <>
        <button type="button" data-tour-anchor="gateway_request_button">
          Send test request
        </button>
        <DestinationTourAnchor maxAttempts={1} />
      </>,
      {
        route:
          "/dashboard/gateway?tour_anchor=gateway_request_button&journey_step=run_gateway_request&tour_replay=1",
      },
    );

    expect(await screen.findByTestId("destination-tour-anchor")).toBeVisible();
    await waitFor(() =>
      expect(
        JSON.parse(
          window.localStorage.getItem(destinationTourDismissalKey("usr-tour")),
        ),
      ).toEqual([]),
    );
  });

  it("shows recovery guidance when the destination action is missing", async () => {
    renderWithRouter(<DestinationTourAnchor maxAttempts={1} />, {
      route:
        "/dashboard/gateway?tour_anchor=gateway_request_button&journey_step=run_gateway_request",
    });

    const recovery = await screen.findByTestId(
      "destination-tour-missing-anchor",
    );
    expect(recovery).toBeVisible();
    expect(screen.getByText("Send request")).toBeVisible();
    expect(screen.getByRole("link", { name: /back to home/i })).toHaveAttribute(
      "href",
      "/dashboard/home?source=destination_tour_fallback&journey_step=run_gateway_request&tour_anchor=gateway_request_button",
    );

    await userEvent.click(screen.getByRole("button", { name: /dismiss/i }));

    await waitFor(() =>
      expect(
        screen.queryByTestId("destination-tour-missing-anchor"),
      ).toBeNull(),
    );
    expect(
      JSON.parse(
        window.localStorage.getItem(destinationTourDismissalKey("usr-tour")),
      ),
    ).toEqual(["gateway_request_button"]);
  });
});
