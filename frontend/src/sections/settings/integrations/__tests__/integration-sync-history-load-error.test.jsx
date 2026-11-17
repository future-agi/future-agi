import { beforeEach, describe, expect, it, vi } from "vitest";
import { render, screen, within } from "src/utils/test-utils";
import { useSyncLogs } from "src/api/integrations";
import IntegrationSyncHistory from "../IntegrationSyncHistory";

const h = vi.hoisted(() => ({
  isLoading: true,
  isError: false,
}));

vi.mock("src/api/integrations", () => ({
  useSyncLogs: vi.fn(() => ({
    data: undefined,
    isLoading: h.isLoading,
    isError: h.isError,
  })),
}));
vi.mock("src/hooks/use-ag-theme", () => ({ useAgTheme: () => undefined }));
vi.mock("ag-grid-react", () => ({ AgGridReact: () => null }));

// Keep ErrorState and EmptyContent real: a mock alert would conceal F01.
beforeEach(() => {
  vi.clearAllMocks();
  h.isLoading = true;
  h.isError = false;
});

describe("Integration sync-history load errors", () => {
  it.each([false, true])(
    "announces a failed query after loading (isExport=%s)",
    (isExport) => {
      const { rerender } = render(
        <IntegrationSyncHistory
          connectionId="synthetic-sync-error-connection"
          isExport={isExport}
        />,
      );
      expect(screen.queryByRole("alert")).not.toBeInTheDocument();

      h.isLoading = false;
      h.isError = true;
      rerender(
        <IntegrationSyncHistory
          connectionId="synthetic-sync-error-connection"
          isExport={isExport}
        />,
      );

      const alerts = screen.getAllByRole("alert");
      expect(alerts).toHaveLength(1);
      expect(
        within(alerts[0]).getByText("Failed to load sync history", {
          exact: true,
        }),
      ).toBeVisible();
      expect(useSyncLogs).toHaveBeenCalledWith(
        "synthetic-sync-error-connection",
      );
    },
  );
});
