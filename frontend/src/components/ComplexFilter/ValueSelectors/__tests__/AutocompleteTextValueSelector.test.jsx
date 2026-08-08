import React from "react";
import PropTypes from "prop-types";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

const mocks = vi.hoisted(() => ({ get: vi.fn(), params: {} }));

vi.mock("src/hooks/use-debounce", () => ({
  useDebounce: (value) => value,
}));
vi.mock("react-router-dom", async (importOriginal) => ({
  ...(await importOriginal()),
  useParams: () => mocks.params,
}));
vi.mock("src/utils/axios", () => ({
  default: mocks,
  endpoints: { dashboard: { filterValues: "/filter-values/" } },
}));

import AutocompleteTextValueSelector from "../AutocompleteTextValueSelector";

function Wrapper({ children }) {
  const client = new QueryClient({
    defaultOptions: { queries: { retry: false } },
  });
  return <QueryClientProvider client={client}>{children}</QueryClientProvider>;
}
Wrapper.propTypes = { children: PropTypes.node };

describe("AutocompleteTextValueSelector", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    mocks.params = { observeId: "project-large" };
  });

  it("loads exact cursor pages on scroll and normalizes the attribute type", async () => {
    mocks.get
      .mockResolvedValueOnce({
        data: {
          result: {
            values: [{ value: "completed", type: "string" }],
            query_complete: true,
            query_status: "complete",
            has_more: true,
            next_cursor: "page-2",
          },
        },
      })
      .mockResolvedValueOnce({
        data: {
          result: {
            values: [
              { value: "completed", type: "string" },
              { value: "ended", type: "string" },
            ],
            query_complete: true,
            query_status: "complete",
            has_more: false,
            next_cursor: null,
          },
        },
      });

    render(
      <AutocompleteTextValueSelector
        definition={{
          propertyId: "call.status",
          filterType: { type: "text" },
          attributeTypes: ["string"],
          attributeTypesExact: true,
        }}
        filter={{ id: "filter-1", filter_config: { filter_value: "" } }}
        updateFilter={vi.fn()}
      />,
      { wrapper: Wrapper },
    );

    fireEvent.mouseDown(screen.getByRole("combobox"));
    expect(
      await screen.findByRole("option", { name: "completed" }),
    ).toBeVisible();
    expect(mocks.get).toHaveBeenNthCalledWith(
      1,
      "/filter-values/",
      expect.objectContaining({
        timeout: 35_000,
        params: expect.objectContaining({
          project_ids: "project-large",
          metric_name: "call.status",
          metric_type: "custom_attribute",
          attribute_type: "string",
          page_size: 10,
        }),
      }),
    );

    const listbox = screen.getByRole("listbox");
    let scrollTop = 80;
    Object.defineProperties(listbox, {
      scrollTop: {
        configurable: true,
        get: () => scrollTop,
        set: (value) => {
          scrollTop = value;
        },
      },
      clientHeight: { configurable: true, get: () => 20 },
      scrollHeight: { configurable: true, get: () => 100 },
    });
    fireEvent.scroll(listbox);

    expect(await screen.findByRole("option", { name: "ended" })).toBeVisible();
    expect(screen.getAllByRole("option", { name: "completed" })).toHaveLength(
      1,
    );
    await waitFor(() => expect(mocks.get).toHaveBeenCalledTimes(2));
    expect(mocks.get).toHaveBeenNthCalledWith(
      2,
      "/filter-values/",
      expect.objectContaining({
        params: expect.objectContaining({
          project_ids: "project-large",
          cursor: "page-2",
        }),
      }),
    );
    expect(screen.queryByText(/incomplete|sample/i)).not.toBeInTheDocument();
  });

  it("uses an explicit selected project across task, eval, and annotation consumers", async () => {
    mocks.params = { observeId: "route-project" };
    mocks.get.mockResolvedValue({
      data: {
        result: {
          values: [{ value: "completed", type: "string" }],
          query_complete: true,
          query_status: "complete",
          has_more: false,
          next_cursor: null,
        },
      },
    });

    render(
      <AutocompleteTextValueSelector
        projectId="selected-project"
        definition={{ propertyId: "call.status", type: "text" }}
        filter={{ id: "filter-1", filter_config: { filter_value: "" } }}
        updateFilter={vi.fn()}
      />,
      { wrapper: Wrapper },
    );

    fireEvent.mouseDown(screen.getByRole("combobox"));
    await screen.findByRole("option", { name: "completed" });
    expect(mocks.get).toHaveBeenCalledWith(
      "/filter-values/",
      expect.objectContaining({
        params: expect.objectContaining({
          project_ids: "selected-project",
        }),
      }),
    );
  });

  it("automatically follows an empty continuation until values arrive", async () => {
    mocks.get
      .mockResolvedValueOnce({
        data: {
          result: {
            values: [],
            query_complete: true,
            query_status: "complete",
            has_more: true,
            next_cursor: "older-page",
          },
        },
      })
      .mockResolvedValueOnce({
        data: {
          result: {
            values: [{ value: "completed", type: "string" }],
            query_complete: true,
            query_status: "complete",
            has_more: false,
            next_cursor: null,
          },
        },
      });

    render(
      <AutocompleteTextValueSelector
        definition={{ propertyId: "call.status", type: "text" }}
        filter={{ id: "filter-1", filter_config: { filter_value: "" } }}
        updateFilter={vi.fn()}
      />,
      { wrapper: Wrapper },
    );

    fireEvent.mouseDown(screen.getByRole("combobox"));
    expect(
      await screen.findByRole("option", { name: "completed" }),
    ).toBeVisible();
    expect(mocks.get).toHaveBeenNthCalledWith(
      2,
      "/filter-values/",
      expect.objectContaining({
        params: expect.objectContaining({ cursor: "older-page" }),
      }),
    );
    expect(
      screen.queryByRole("option", { name: "Load more values" }),
    ).not.toBeInTheDocument();
  });

  it("automatically follows a page containing only already-loaded typed values", async () => {
    mocks.get
      .mockResolvedValueOnce({
        data: {
          result: {
            values: [{ value: "completed", type: "string" }],
            query_complete: true,
            query_status: "complete",
            has_more: true,
            next_cursor: "duplicate-page",
          },
        },
      })
      .mockResolvedValueOnce({
        data: {
          result: {
            values: [{ value: "completed", type: "string" }],
            query_complete: true,
            query_status: "complete",
            has_more: true,
            next_cursor: "new-value-page",
          },
        },
      })
      .mockResolvedValueOnce({
        data: {
          result: {
            values: [{ value: "ended", type: "string" }],
            query_complete: true,
            query_status: "complete",
            has_more: false,
            next_cursor: null,
          },
        },
      });

    render(
      <AutocompleteTextValueSelector
        definition={{ propertyId: "call.status", type: "text" }}
        filter={{ id: "filter-1", filter_config: { filter_value: "" } }}
        updateFilter={vi.fn()}
      />,
      { wrapper: Wrapper },
    );

    fireEvent.mouseDown(screen.getByRole("combobox"));
    expect(
      await screen.findByRole("option", { name: "completed" }),
    ).toBeVisible();
    fireEvent.click(screen.getByRole("option", { name: "Load more values" }));

    expect(await screen.findByRole("option", { name: "ended" })).toBeVisible();
    expect(screen.getAllByRole("option", { name: "completed" })).toHaveLength(
      1,
    );
    await waitFor(() => expect(mocks.get).toHaveBeenCalledTimes(3));
    expect(mocks.get).toHaveBeenNthCalledWith(
      3,
      "/filter-values/",
      expect.objectContaining({
        params: expect.objectContaining({ cursor: "new-value-page" }),
      }),
    );
  });

  it("bounds a 48-page empty chain and resumes it through explicit retry actions", async () => {
    let responseIndex = 0;
    mocks.get.mockImplementation(async () => {
      const current = responseIndex;
      responseIndex += 1;
      if (current < 48) {
        return {
          data: {
            result: {
              values: [],
              query_complete: true,
              query_status: "complete",
              has_more: true,
              next_cursor: `cursor-${current + 1}`,
            },
          },
        };
      }
      return {
        data: {
          result: {
            values: [{ value: "eventually-found", type: "string" }],
            query_complete: true,
            query_status: "complete",
            has_more: false,
            next_cursor: null,
          },
        },
      };
    });

    render(
      <AutocompleteTextValueSelector
        definition={{ propertyId: "sparse.status", type: "text" }}
        filter={{ id: "filter-1", filter_config: { filter_value: "" } }}
        updateFilter={vi.fn()}
      />,
      { wrapper: Wrapper },
    );

    fireEvent.mouseDown(screen.getByRole("combobox"));
    await screen.findByRole("option", { name: "Retry loading values" });
    expect(mocks.get).toHaveBeenCalledTimes(13);

    for (const expectedRequestCount of [26, 39]) {
      fireEvent.click(
        screen.getByRole("option", { name: "Retry loading values" }),
      );
      await waitFor(() =>
        expect(mocks.get).toHaveBeenCalledTimes(expectedRequestCount),
      );
      await screen.findByRole("option", { name: "Retry loading values" });
    }

    fireEvent.click(
      screen.getByRole("option", { name: "Retry loading values" }),
    );
    expect(
      await screen.findByRole("option", { name: "eventually-found" }),
    ).toBeVisible();
    expect(mocks.get).toHaveBeenCalledTimes(49);
    expect(mocks.get).toHaveBeenNthCalledWith(
      49,
      "/filter-values/",
      expect.objectContaining({
        params: expect.objectContaining({ cursor: "cursor-48" }),
      }),
    );
    expect(
      screen.queryByRole("option", { name: "Load more values" }),
    ).not.toBeInTheDocument();
  });

  it("stops a repeated empty cursor and offers a bounded retry", async () => {
    mocks.get.mockResolvedValue({
      data: {
        result: {
          values: [],
          query_complete: true,
          query_status: "complete",
          has_more: true,
          next_cursor: "repeated-cursor",
        },
      },
    });

    render(
      <AutocompleteTextValueSelector
        definition={{ propertyId: "broken.status", type: "text" }}
        filter={{ id: "filter-1", filter_config: { filter_value: "" } }}
        updateFilter={vi.fn()}
      />,
      { wrapper: Wrapper },
    );

    fireEvent.mouseDown(screen.getByRole("combobox"));
    const retry = await screen.findByRole("option", {
      name: "Retry loading values",
    });
    expect(mocks.get).toHaveBeenCalledTimes(2);
    expect(screen.queryByRole("progressbar")).not.toBeInTheDocument();

    fireEvent.click(retry);
    await waitFor(() => expect(mocks.get).toHaveBeenCalledTimes(4));
    expect(
      await screen.findByRole("option", { name: "Retry loading values" }),
    ).toBeVisible();
    expect(screen.queryByRole("progressbar")).not.toBeInTheDocument();
  });

  it("preserves loaded values when next-page retry reaches the empty-hop bound", async () => {
    let requestNumber = 0;
    mocks.get.mockImplementation(async () => {
      requestNumber += 1;
      if (requestNumber === 1) {
        return {
          data: {
            result: {
              values: [{ value: "completed", type: "string" }],
              query_complete: true,
              query_status: "complete",
              has_more: true,
              next_cursor: "retry-start",
            },
          },
        };
      }
      if (requestNumber === 2) {
        throw new Error("transient continuation failure");
      }
      if (requestNumber <= 15) {
        return {
          data: {
            result: {
              values: [],
              query_complete: true,
              query_status: "complete",
              has_more: true,
              next_cursor: `empty-${requestNumber - 2}`,
            },
          },
        };
      }
      return {
        data: {
          result: {
            values: [{ value: "recovered", type: "string" }],
            query_complete: true,
            query_status: "complete",
            has_more: false,
            next_cursor: null,
          },
        },
      };
    });

    render(
      <AutocompleteTextValueSelector
        definition={{ propertyId: "retry.status", type: "text" }}
        filter={{ id: "filter-1", filter_config: { filter_value: "" } }}
        updateFilter={vi.fn()}
      />,
      { wrapper: Wrapper },
    );

    fireEvent.mouseDown(screen.getByRole("combobox"));
    expect(
      await screen.findByRole("option", { name: "completed" }),
    ).toBeVisible();
    fireEvent.click(screen.getByRole("option", { name: "Load more values" }));

    const retry = await screen.findByRole("option", {
      name: "Retry loading values",
    });
    expect(mocks.get).toHaveBeenCalledTimes(2);
    expect(screen.getByRole("option", { name: "completed" })).toBeVisible();

    fireEvent.click(retry);
    await waitFor(() => expect(mocks.get).toHaveBeenCalledTimes(15));
    // Twelve continuations and the 30-second soft check are per-action guards.
    // The latter is evaluated only between completed HTTP requests, so the
    // deterministic hop bound is what this regression exercises.
    expect(screen.getByRole("option", { name: "completed" })).toBeVisible();
    const retryBoundedContinuation = await screen.findByRole("option", {
      name: "Retry loading values",
    });
    expect(mocks.get).toHaveBeenNthCalledWith(
      15,
      "/filter-values/",
      expect.objectContaining({
        params: expect.objectContaining({ cursor: "empty-12" }),
      }),
    );

    fireEvent.click(retryBoundedContinuation);
    expect(
      await screen.findByRole("option", { name: "recovered" }),
    ).toBeVisible();
    expect(screen.getByRole("option", { name: "completed" })).toBeVisible();
    expect(mocks.get).toHaveBeenCalledTimes(16);
    expect(mocks.get).toHaveBeenNthCalledWith(
      16,
      "/filter-values/",
      expect.objectContaining({
        params: expect.objectContaining({ cursor: "empty-13" }),
      }),
    );
  });

  it("stops automatically at an empty terminal continuation", async () => {
    mocks.get
      .mockResolvedValueOnce({
        data: {
          result: {
            values: [],
            query_complete: true,
            query_status: "complete",
            has_more: true,
            next_cursor: "terminal-page",
          },
        },
      })
      .mockResolvedValueOnce({
        data: {
          result: {
            values: [],
            query_complete: true,
            query_status: "complete",
            has_more: false,
            next_cursor: null,
          },
        },
      });

    render(
      <AutocompleteTextValueSelector
        definition={{ propertyId: "absent.status", type: "text" }}
        filter={{ id: "filter-1", filter_config: { filter_value: "" } }}
        updateFilter={vi.fn()}
      />,
      { wrapper: Wrapper },
    );

    fireEvent.mouseDown(screen.getByRole("combobox"));
    await waitFor(() => expect(mocks.get).toHaveBeenCalledTimes(2));
    await waitFor(() =>
      expect(screen.queryByRole("progressbar")).not.toBeInTheDocument(),
    );
    expect(
      screen.queryByRole("option", { name: /load more|retry loading/i }),
    ).not.toBeInTheDocument();
  });

  it.each(["exhausted", "limit_reached"])(
    "stops on terminal %s metadata even when stale cursor fields claim more data",
    async (browseStatus) => {
      mocks.get.mockResolvedValue({
        data: {
          result: {
            values: [],
            query_complete: true,
            query_status: "complete",
            browse_status: browseStatus,
            has_more: true,
            next_cursor: "stale-terminal-cursor",
          },
        },
      });

      render(
        <AutocompleteTextValueSelector
          definition={{ propertyId: "terminal.status", type: "text" }}
          filter={{ id: "filter-1", filter_config: { filter_value: "" } }}
          updateFilter={vi.fn()}
        />,
        { wrapper: Wrapper },
      );

      fireEvent.mouseDown(screen.getByRole("combobox"));
      await waitFor(() => expect(mocks.get).toHaveBeenCalledOnce());
      await waitFor(() =>
        expect(screen.queryByRole("progressbar")).not.toBeInTheDocument(),
      );
      expect(
        screen.queryByRole("option", { name: /load more|retry loading/i }),
      ).not.toBeInTheDocument();
    },
  );

  it("queries all typed stores when an attribute has mixed storage types", async () => {
    mocks.get.mockResolvedValue({
      data: {
        result: {
          values: [
            { value: "completed", type: "string" },
            { value: 1, type: "number" },
          ],
          query_complete: true,
          query_status: "complete",
          has_more: false,
          next_cursor: null,
        },
      },
    });

    render(
      <AutocompleteTextValueSelector
        definition={{
          propertyId: "mixed.status",
          type: "text",
          attributeTypes: ["string", "number"],
        }}
        filter={{ id: "filter-1", filter_config: { filter_value: "" } }}
        updateFilter={vi.fn()}
      />,
      { wrapper: Wrapper },
    );

    fireEvent.mouseDown(screen.getByRole("combobox"));
    await screen.findByRole("option", { name: "completed" });
    const params = mocks.get.mock.calls[0][1].params;
    expect(params.metric_name).toBe("mixed.status");
    expect(params).not.toHaveProperty("attribute_type");
  });

  it("does not pin a bounded singleton type hint", async () => {
    mocks.get.mockResolvedValue({
      data: {
        result: {
          values: [{ value: "completed", type: "string" }],
          query_complete: true,
          query_status: "complete",
          has_more: false,
          next_cursor: null,
        },
      },
    });

    render(
      <AutocompleteTextValueSelector
        definition={{
          propertyId: "possibly.mixed",
          type: "text",
          attributeTypes: ["string"],
          attributeTypesExact: false,
        }}
        filter={{ id: "filter-1", filter_config: { filter_value: "" } }}
        updateFilter={vi.fn()}
      />,
      { wrapper: Wrapper },
    );

    fireEvent.mouseDown(screen.getByRole("combobox"));
    await screen.findByRole("option", { name: "completed" });
    expect(mocks.get.mock.calls[0][1].params).not.toHaveProperty(
      "attribute_type",
    );
  });

  it.each([
    {
      label: "numeric",
      option: { value: 42, type: "number" },
      optionName: "42",
      expectedType: "number",
      expectedValue: 42,
    },
    {
      label: "boolean",
      option: { value: false, type: "boolean" },
      optionName: "false",
      expectedType: "boolean",
      expectedValue: false,
    },
  ])(
    "preserves a selected $label value and storage type through blur",
    async ({ option, optionName, expectedType, expectedValue }) => {
      mocks.get.mockResolvedValue({
        data: {
          result: {
            values: [option],
            query_complete: true,
            query_status: "complete",
            has_more: false,
            next_cursor: null,
          },
        },
      });
      const updateFilter = vi.fn();
      const filter = {
        id: "typed-filter",
        filter_config: {
          col_type: "SPAN_ATTRIBUTE",
          filter_type: "text",
          filter_op: "equals",
          filter_value: "",
          attribute_value_types: ["string"],
        },
      };

      render(
        <AutocompleteTextValueSelector
          definition={{
            propertyId: "mixed.value",
            type: "text",
            attributeTypes: ["string", "number", "boolean"],
          }}
          filter={filter}
          updateFilter={updateFilter}
        />,
        { wrapper: Wrapper },
      );

      const combobox = screen.getByRole("combobox");
      fireEvent.mouseDown(combobox);
      fireEvent.click(await screen.findByRole("option", { name: optionName }));

      expect(updateFilter).toHaveBeenCalledTimes(1);
      expect(updateFilter.mock.calls[0][0]).toBe("typed-filter");
      const nextFilter = updateFilter.mock.calls[0][1](filter);
      expect(nextFilter.filter_config).toEqual({
        col_type: "SPAN_ATTRIBUTE",
        filter_type: expectedType,
        filter_op: "equals",
        filter_value: expectedValue,
      });

      // MUI writes the display label into the input after selection. Blurring
      // must not issue a second update that coerces 42/false back to text.
      fireEvent.blur(combobox);
      expect(updateFilter).toHaveBeenCalledTimes(1);
    },
  );

  it("serializes list selections with aligned ClickHouse value provenance", async () => {
    mocks.get.mockResolvedValue({
      data: {
        result: {
          values: [{ value: 42, type: "number" }],
          query_complete: true,
          query_status: "complete",
          has_more: false,
          next_cursor: null,
        },
      },
    });
    const updateFilter = vi.fn();
    const filter = {
      id: "list-filter",
      filter_config: {
        col_type: "SPAN_ATTRIBUTE",
        filter_type: "text",
        filter_op: "in",
        filter_value: [],
      },
    };

    render(
      <AutocompleteTextValueSelector
        definition={{
          propertyId: "mixed.value",
          type: "text",
          attributeTypes: ["string", "number", "boolean"],
        }}
        filter={filter}
        updateFilter={updateFilter}
      />,
      { wrapper: Wrapper },
    );

    fireEvent.mouseDown(screen.getByRole("combobox"));
    fireEvent.click(await screen.findByRole("option", { name: "42" }));

    expect(updateFilter).toHaveBeenCalledTimes(1);
    const nextFilter = updateFilter.mock.calls[0][1](filter);
    expect(nextFilter.filter_config).toEqual({
      col_type: "SPAN_ATTRIBUTE",
      filter_type: "text",
      filter_op: "in",
      filter_value: [42],
      attribute_value_types: ["number"],
    });
  });
});
