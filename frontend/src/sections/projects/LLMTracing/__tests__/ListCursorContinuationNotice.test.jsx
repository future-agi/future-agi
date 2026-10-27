import React, { useState } from "react";
import PropTypes from "prop-types";
import { describe, expect, it, vi } from "vitest";
import { render, screen, userEvent } from "src/utils/test-utils";

import ListCursorContinuationNotice from "../ListCursorContinuationNotice";
import {
  createListCursorPagination,
  retryServerSideCursorLoad,
} from "../listCursorPagination";

function RetryHarness({ api }) {
  const [pending, setPending] = useState(true);

  return (
    <ListCursorContinuationNotice
      pending={pending}
      onContinue={() => {
        if (retryServerSideCursorLoad(api)) setPending(false);
      }}
    />
  );
}

RetryHarness.propTypes = {
  api: PropTypes.object.isRequired,
};

describe("ListCursorContinuationNotice", () => {
  it("waits for one explicit click, retries once, and retains the checkpoint", async () => {
    const pagination = createListCursorPagination();
    pagination.recordEmptyContinuation(0, {
      has_more: true,
      next_cursor: "signed-checkpoint",
    });
    const api = {
      retryServerSideLoads: vi.fn(),
      refreshServerSide: vi.fn(),
    };

    render(<RetryHarness api={api} />);

    expect(screen.getByRole("status")).toBeVisible();
    expect(
      screen.getByRole("button", { name: "Continue search" }),
    ).toBeVisible();
    expect(api.retryServerSideLoads).not.toHaveBeenCalled();
    expect(api.refreshServerSide).not.toHaveBeenCalled();

    await userEvent.click(
      screen.getByRole("button", { name: "Continue search" }),
    );

    expect(api.retryServerSideLoads).toHaveBeenCalledOnce();
    expect(api.refreshServerSide).not.toHaveBeenCalled();
    expect(
      screen.queryByRole("button", { name: "Continue search" }),
    ).not.toBeInTheDocument();
    expect(pagination.requestParams(0, { page_size: 25 })).toEqual({
      page_size: 25,
      cursor_mode: true,
      cursor: "signed-checkpoint",
    });
  });

  it("falls back to a non-purging server-side refresh", async () => {
    const api = { refreshServerSide: vi.fn() };

    render(<RetryHarness api={api} />);
    expect(api.refreshServerSide).not.toHaveBeenCalled();

    await userEvent.click(
      screen.getByRole("button", { name: "Continue search" }),
    );

    expect(api.refreshServerSide).toHaveBeenCalledOnce();
    expect(api.refreshServerSide).toHaveBeenCalledWith({ purge: false });
  });

  it("renders nothing when no continuation is pending", () => {
    render(
      <ListCursorContinuationNotice pending={false} onContinue={vi.fn()} />,
    );

    expect(screen.queryByRole("status")).not.toBeInTheDocument();
    expect(
      screen.queryByRole("button", { name: "Continue search" }),
    ).not.toBeInTheDocument();
  });

  it("keeps the continuation actionable when the grid cannot schedule a retry", async () => {
    render(<RetryHarness api={{}} />);

    await userEvent.click(
      screen.getByRole("button", { name: "Continue search" }),
    );

    expect(screen.getByRole("status")).toBeVisible();
    expect(
      screen.getByRole("button", { name: "Continue search" }),
    ).toBeVisible();
  });
});
