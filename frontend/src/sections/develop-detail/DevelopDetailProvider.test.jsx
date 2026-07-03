import { describe, expect, it, vi, beforeEach } from "vitest";
import { render, screen, fireEvent } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import React, { useCallback } from "react";
import PropTypes from "prop-types";
import DevelopDetailProvider from "./DevelopDetailProvider";
import { useDevelopDetailContext } from "./Context/DevelopDetailContext";

const queryClient = new QueryClient({
  defaultOptions: { queries: { retry: false } },
});

function TestConsumer({ onReady }) {
  const { refreshGrid, setGridApi, setRefetchTable } =
    useDevelopDetailContext();

  const handleSetApi = useCallback(() => {
    setGridApi({ refreshServerSide: onReady.mockRefresh });
  }, [onReady.mockRefresh, setGridApi]);

  const handleSetRefetch = useCallback(() => {
    setRefetchTable(onReady.mockRefetch);
  }, [onReady.mockRefetch, setRefetchTable]);

  return (
    <div>
      <button data-testid="set-api" onClick={handleSetApi}>
        set-api
      </button>
      <button data-testid="set-refetch" onClick={handleSetRefetch}>
        set-refetch
      </button>
      <button data-testid="call-refresh" onClick={() => refreshGrid()}>
        refresh
      </button>
      <button data-testid="call-refresh-null" onClick={() => refreshGrid(null)}>
        refresh-null
      </button>
      <button
        data-testid="call-refresh-undefined"
        onClick={() => refreshGrid(undefined)}
      >
        refresh-undefined
      </button>
      <button
        data-testid="call-refresh-purge"
        onClick={() => refreshGrid({ purge: true })}
      >
        refresh-purge
      </button>
    </div>
  );
}

TestConsumer.propTypes = {
  onReady: PropTypes.shape({
    mockRefresh: PropTypes.func,
    mockRefetch: PropTypes.func,
  }).isRequired,
};

function renderWithProvider(onReady) {
  return render(
    <QueryClientProvider client={queryClient}>
      <DevelopDetailProvider>
        <TestConsumer onReady={onReady} />
      </DevelopDetailProvider>
    </QueryClientProvider>,
  );
}

describe("DevelopDetailProvider", () => {
  let onReady;

  beforeEach(() => {
    onReady = {
      mockRefresh: vi.fn(),
      mockRefetch: vi.fn(),
    };
  });

  it("calls gridApi.refreshServerSide when api is set", () => {
    renderWithProvider(onReady);
    fireEvent.click(screen.getByTestId("set-api"));
    fireEvent.click(screen.getByTestId("call-refresh"));
    expect(onReady.mockRefresh).toHaveBeenCalledWith(undefined);
  });

  it("does not crash when refreshGrid is called with null", () => {
    renderWithProvider(onReady);
    fireEvent.click(screen.getByTestId("set-api"));
    expect(() => {
      fireEvent.click(screen.getByTestId("call-refresh-null"));
    }).not.toThrow();
  });

  it("does not crash when refreshGrid is called with undefined", () => {
    renderWithProvider(onReady);
    fireEvent.click(screen.getByTestId("set-api"));
    expect(() => {
      fireEvent.click(screen.getByTestId("call-refresh-undefined"));
    }).not.toThrow();
  });

  it("calls refetchTable.current when set and refreshGrid is called", () => {
    renderWithProvider(onReady);
    fireEvent.click(screen.getByTestId("set-refetch"));
    fireEvent.click(screen.getByTestId("set-api"));
    fireEvent.click(screen.getByTestId("call-refresh"));
    expect(onReady.mockRefetch).toHaveBeenCalled();
    expect(onReady.mockRefresh).toHaveBeenCalled();
  });

  it("calls refreshServerSide with { purge: true } when purge option is passed", () => {
    renderWithProvider(onReady);
    fireEvent.click(screen.getByTestId("set-api"));
    fireEvent.click(screen.getByTestId("call-refresh-purge"));
    expect(onReady.mockRefresh).toHaveBeenCalledWith({ purge: true });
  });
});
