import React from "react";
import { act, render, screen, userEvent } from "src/utils/test-utils";
import { describe, expect, it, vi } from "vitest";
import {
  ExactSelectorContinuationNotice,
  createExactSelectorDataSource,
} from "../exact-selector-pagination";
import { createListCursorPagination } from "src/sections/projects/LLMTracing/listCursorPagination";

const response = ({ rows = [], hasMore, cursor, totalRows }) => ({
  data: {
    result: {
      config: [],
      table: rows,
      metadata: {
        has_more: hasMore,
        next_cursor: cursor,
        ...(totalRows === undefined ? {} : { total_rows: totalRows }),
      },
    },
  },
});

const gridParams = () => ({
  request: { startRow: 0, endRow: 2, sortModel: [] },
  success: vi.fn(),
  fail: vi.fn(),
  api: {
    retryServerSideLoads: vi.fn(),
  },
});

const makeDataSource = ({ request, onPaused = vi.fn(), onFailure = vi.fn() }) =>
  createExactSelectorDataSource({
    pagination: createListCursorPagination(),
    targetRowCount: 2,
    getBaseParams: () => ({ project_id: "project-1", page_size: 2 }),
    request,
    rowsFromResponse: (nextResponse) => nextResponse?.data?.result?.table || [],
    metadataFromResponse: (nextResponse) =>
      nextResponse?.data?.result?.metadata || {},
    rowIdentity: (row) => row.id,
    onPaused,
    onFailure,
    maxContinuations: 1,
  });

describe("annotation selector exact cursor datasource", () => {
  it("follows signed cursors and publishes only a genuine visible page", async () => {
    const request = vi
      .fn()
      .mockResolvedValueOnce(
        response({ rows: [], hasMore: true, cursor: "signed-1" }),
      )
      .mockResolvedValueOnce(
        response({
          rows: [{ id: "trace-1" }, { id: "trace-2" }],
          hasMore: false,
          cursor: null,
          totalRows: 2,
        }),
      );
    const params = gridParams();

    await makeDataSource({ request }).getRows(params);

    expect(request).toHaveBeenNthCalledWith(
      1,
      expect.objectContaining({
        cursor_mode: true,
        page_number: 0,
      }),
      expect.any(AbortSignal),
    );
    expect(request).toHaveBeenNthCalledWith(
      2,
      expect.objectContaining({
        cursor_mode: true,
        cursor: "signed-1",
      }),
      expect.any(AbortSignal),
    );
    expect(params.fail).not.toHaveBeenCalled();
    expect(params.success).toHaveBeenCalledWith({
      rowData: [{ id: "trace-1" }, { id: "trace-2" }],
      rowCount: 2,
    });
  });

  it("retains the checkpoint at the safety bound and resumes manually", async () => {
    const request = vi
      .fn()
      .mockResolvedValueOnce(
        response({
          rows: [{ id: "trace-buffered" }],
          hasMore: true,
          cursor: "signed-1",
        }),
      )
      .mockResolvedValueOnce(
        response({ rows: [], hasMore: true, cursor: "signed-2" }),
      )
      .mockResolvedValueOnce(
        response({
          rows: [{ id: "trace-rare" }],
          hasMore: false,
          cursor: null,
          totalRows: 2,
        }),
      );
    const onPaused = vi.fn();
    const dataSource = makeDataSource({ request, onPaused });
    const firstAttempt = gridParams();

    await dataSource.getRows(firstAttempt);

    expect(firstAttempt.success).not.toHaveBeenCalled();
    expect(firstAttempt.fail).toHaveBeenCalledOnce();
    expect(onPaused).toHaveBeenCalledWith(expect.any(Function));

    const resume = onPaused.mock.calls[0][0];
    act(() => resume());
    expect(firstAttempt.api.retryServerSideLoads).toHaveBeenCalledOnce();

    const resumedAttempt = gridParams();
    await dataSource.getRows(resumedAttempt);

    expect(request).toHaveBeenNthCalledWith(
      3,
      expect.objectContaining({
        cursor_mode: true,
        cursor: "signed-2",
      }),
      expect.any(AbortSignal),
    );
    expect(resumedAttempt.success).toHaveBeenCalledWith({
      rowData: [{ id: "trace-buffered" }, { id: "trace-rare" }],
      rowCount: 2,
    });
  });

  it("keeps real transport failures separate from a bounded pause", async () => {
    const failure = new Error("network unavailable");
    const onPaused = vi.fn();
    const onFailure = vi.fn();
    const params = gridParams();

    await makeDataSource({
      request: vi.fn().mockRejectedValue(failure),
      onPaused,
      onFailure,
    }).getRows(params);

    expect(params.success).not.toHaveBeenCalled();
    expect(params.fail).toHaveBeenCalledOnce();
    expect(onPaused).toHaveBeenCalledWith(null);
    expect(onFailure).toHaveBeenCalledWith(failure);
  });

  it("preserves a proven checkpoint and offers retry after a later transport failure", async () => {
    const failure = new Error("network unavailable");
    const request = vi
      .fn()
      .mockResolvedValueOnce(
        response({
          rows: [{ id: "trace-buffered" }],
          hasMore: true,
          cursor: "signed-retry",
        }),
      )
      .mockRejectedValueOnce(failure)
      .mockResolvedValueOnce(
        response({
          rows: [{ id: "trace-terminal" }],
          hasMore: false,
          cursor: null,
          totalRows: 2,
        }),
      );
    const onPaused = vi.fn();
    const onFailure = vi.fn();
    const dataSource = makeDataSource({ request, onPaused, onFailure });
    const failedAttempt = gridParams();

    await dataSource.getRows(failedAttempt);

    expect(failedAttempt.success).not.toHaveBeenCalled();
    expect(failedAttempt.fail).toHaveBeenCalledOnce();
    expect(onFailure).not.toHaveBeenCalled();
    expect(onPaused).toHaveBeenCalledWith(expect.any(Function));

    const resumedAttempt = gridParams();
    await dataSource.getRows(resumedAttempt);

    expect(request).toHaveBeenNthCalledWith(
      3,
      expect.objectContaining({ cursor: "signed-retry" }),
      expect.any(AbortSignal),
    );
    expect(resumedAttempt.success).toHaveBeenCalledWith({
      rowData: [{ id: "trace-buffered" }, { id: "trace-terminal" }],
      rowCount: 2,
    });
  });

  it("rejects an A-B-A cursor cycle after a bounded manual resume", async () => {
    const request = vi
      .fn()
      .mockResolvedValueOnce(
        response({
          rows: [{ id: "trace-buffered" }],
          hasMore: true,
          cursor: "signed-a",
        }),
      )
      .mockResolvedValueOnce(
        response({ rows: [], hasMore: true, cursor: "signed-b" }),
      )
      .mockResolvedValueOnce(
        response({ rows: [], hasMore: true, cursor: "signed-a" }),
      );
    const onPaused = vi.fn();
    const onFailure = vi.fn();
    const dataSource = makeDataSource({ request, onPaused, onFailure });

    await dataSource.getRows(gridParams());
    expect(onPaused).toHaveBeenCalledWith(expect.any(Function));

    const resumedAttempt = gridParams();
    await dataSource.getRows(resumedAttempt);

    expect(request).toHaveBeenNthCalledWith(
      3,
      expect.objectContaining({ cursor: "signed-b" }),
      expect.any(AbortSignal),
    );
    expect(resumedAttempt.success).not.toHaveBeenCalled();
    expect(resumedAttempt.fail).toHaveBeenCalledOnce();
    expect(onFailure).toHaveBeenCalledWith(
      expect.objectContaining({
        message: "List API returned a repeated continuation cursor",
      }),
    );
  });
});

describe("ExactSelectorContinuationNotice", () => {
  it("uses neutral copy and requires an explicit Continue action", async () => {
    const onContinue = vi.fn();
    render(<ExactSelectorContinuationNotice pending onContinue={onContinue} />);

    expect(screen.getByRole("status")).toHaveTextContent(
      "Preparing exact results",
    );
    await userEvent.click(
      screen.getByRole("button", { name: "Continue search" }),
    );
    expect(onContinue).toHaveBeenCalledOnce();
  });
});
