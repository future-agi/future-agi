import PropTypes from "prop-types";
import { describe, expect, it, vi } from "vitest";
import { renderWithRouter, screen } from "src/utils/test-utils";

import ApiKeysLandingPage from "./ApiKeysLandingPage";

vi.mock("ag-grid-react", async () => {
  const React = await import("react");
  const AgGridReact = React.forwardRef((_props, ref) => (
    <div ref={ref} data-testid="api-keys-grid" />
  ));
  AgGridReact.displayName = "AgGridReact";

  return {
    AgGridReact,
  };
});

vi.mock("src/hooks/use-ag-theme", () => ({
  useAgThemeWith: () => ({}),
}));

vi.mock("src/utils/axios", () => ({
  default: {
    get: vi.fn(),
  },
  endpoints: {
    keys: {
      getKeys: "/accounts/key/get_secret_keys/",
    },
  },
}));

vi.mock("./CreateApiKey", () => {
  const CreateApiKey = ({ completionHref, initialKeyName, open }) => (
    <div
      data-completion-href={completionHref || ""}
      data-testid="create-api-key-dialog"
      data-initial-key-name={initialKeyName || ""}
      data-open={open ? "true" : "false"}
    >
      {open ? "Create key dialog open" : "Create key dialog closed"}
    </div>
  );

  CreateApiKey.propTypes = {
    completionHref: PropTypes.string,
    initialKeyName: PropTypes.string,
    open: PropTypes.bool,
  };

  return {
    default: CreateApiKey,
  };
});

describe("ApiKeysLandingPage onboarding handoff", () => {
  it("opens key creation from the observe first-trace deep link", () => {
    renderWithRouter(<ApiKeysLandingPage />, {
      route:
        "/dashboard/settings/api_keys?source=onboarding&target=observe_first_trace&action=create&key_name=Observe+first+trace&return_to=%2Fdashboard%2Fobserve%3Fsetup%3Dtrue%26source%3Donboarding",
    });

    expect(screen.getByTestId("api-keys-grid")).toBeVisible();
    expect(screen.getByTestId("create-api-key-dialog")).toHaveAttribute(
      "data-open",
      "true",
    );
    expect(screen.getByTestId("create-api-key-dialog")).toHaveAttribute(
      "data-initial-key-name",
      "Observe first trace",
    );
    expect(screen.getByTestId("create-api-key-dialog")).toHaveAttribute(
      "data-completion-href",
      "/dashboard/observe?setup=true&source=onboarding",
    );
  });

  it("drops unsafe onboarding return targets", () => {
    renderWithRouter(<ApiKeysLandingPage />, {
      route:
        "/dashboard/settings/api_keys?source=onboarding&target=observe_first_trace&action=create&return_to=https%3A%2F%2Fexample.com",
    });

    expect(screen.getByTestId("create-api-key-dialog")).toHaveAttribute(
      "data-completion-href",
      "",
    );
  });

  it("keeps key creation closed without the onboarding action", () => {
    renderWithRouter(<ApiKeysLandingPage />, {
      route: "/dashboard/settings/api_keys",
    });

    expect(screen.getByTestId("create-api-key-dialog")).toHaveAttribute(
      "data-open",
      "false",
    );
  });
});
