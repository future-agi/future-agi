import { beforeEach, describe, expect, it, vi } from "vitest";
import { screen, renderWithRouter } from "src/utils/test-utils";
import NavGatewayPanel from "./NavGatewayPanel";
import { useGatewayOpen } from "./states";

vi.mock("./config-navigation", () => ({
  useNavGatewayData: () => [
    {
      subheader: "Configure",
      items: [
        {
          title: "Providers",
          path: "/dashboard/gateway/providers",
          iconName: "mdi:server",
        },
      ],
    },
  ],
}));

vi.mock("src/components/scrollbar", () => ({
  default: ({ children }) => <div>{children}</div>,
}));

describe("NavGatewayPanel", () => {
  beforeEach(() => {
    useGatewayOpen.setState({ gatewayOpen: false });
  });

  it("hides stale gateway navigation outside gateway routes", () => {
    useGatewayOpen.setState({ gatewayOpen: true });

    renderWithRouter(<NavGatewayPanel />, { route: "/dashboard/home" });

    expect(screen.getByTestId("nav-gateway-panel")).toHaveAttribute(
      "aria-hidden",
      "true",
    );
  });

  it("shows gateway navigation on gateway routes when opened", () => {
    useGatewayOpen.setState({ gatewayOpen: true });

    renderWithRouter(<NavGatewayPanel />, {
      route: "/dashboard/gateway/providers",
    });

    expect(screen.getByTestId("nav-gateway-panel")).toHaveAttribute(
      "aria-hidden",
      "false",
    );
    expect(screen.getByText("Providers")).toBeVisible();
  });
});
