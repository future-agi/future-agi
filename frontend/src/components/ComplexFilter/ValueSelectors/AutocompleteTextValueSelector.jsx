import { useCallback, useEffect, useRef, useState } from "react";
import PropTypes from "prop-types";
import { Autocomplete, TextField, CircularProgress } from "@mui/material";
import { useInfiniteQuery, useQueryClient } from "@tanstack/react-query";
import axios, { endpoints } from "src/utils/axios";
import { useDebounce } from "src/hooks/use-debounce";
import { useParams } from "react-router-dom";
import {
  FILTER_TYPE_ALLOWED_OPS,
  LIST_FILTER_OPS,
} from "src/api/contracts/filter-contract.generated";
import { accumulateUniqueListContinuations } from "src/sections/projects/LLMTracing/listCursorPagination";

const LOAD_MORE_OPTION = Object.freeze({ __loadMore: true });
const RETRY_OPTION = Object.freeze({ __retry: true });
const LIST_OPERATORS = new Set(LIST_FILTER_OPS);
// `limit_reached` is resumable when the backend supplies an advancing cursor;
// only an explicit exhaustion proof is unconditionally terminal.
const TERMINAL_BROWSE_STATUSES = new Set(["exhausted"]);
const EMPTY_CONTINUATION_GUARD_EXHAUSTED = "empty_continuation_guard_exhausted";
const FOLLOWED_CURSORS_KEY = "followed_value_cursors";
const CURSOR_STOPPED_KEY = "filter_value_cursor_stopped";
// The shared Axios client intentionally has no global timeout. Attribute
// browsing is interactive, though, and an interrupted proxy/backend response
// must not leave the picker in an endless "Loading more" state. This is just
// above the server-side four-second picker wall so ordinary server timeouts can
// retain their structured response while transport stalls still fail below the
// five-second interaction contract.
const ATTRIBUTE_VALUE_REQUEST_TIMEOUT_MS = 4_800;

const normalizeBrowseMetadata = (result = {}) =>
  TERMINAL_BROWSE_STATUSES.has(result?.browse_status)
    ? { ...result, has_more: false, next_cursor: null }
    : result;

const hasOwn = (value, key) =>
  Object.prototype.hasOwnProperty.call(value || {}, key);

const stopBrowseCursor = (result, reason) => ({
  ...result,
  [CURSOR_STOPPED_KEY]: reason,
});

const isBrowseCursorStopped = (result) =>
  typeof result?.[CURSOR_STOPPED_KEY] === "string";

const validateBrowseCursor = (result, consumedCursors = new Set()) => {
  const normalized = normalizeBrowseMetadata(result);
  const hasMoreField = hasOwn(normalized, "has_more");
  const nextCursorField = hasOwn(normalized, "next_cursor");
  if (!hasMoreField && !nextCursorField) return normalized;
  if (!hasMoreField || !nextCursorField) {
    return stopBrowseCursor(normalized, "malformed_cursor");
  }
  if (normalized.has_more === true) {
    const cursor = normalized.next_cursor;
    if (typeof cursor !== "string" || cursor.length === 0) {
      return stopBrowseCursor(normalized, "malformed_cursor");
    }
    return consumedCursors.has(cursor)
      ? stopBrowseCursor(normalized, "repeated_cursor")
      : normalized;
  }
  return normalized.has_more === false && normalized.next_cursor == null
    ? normalized
    : stopBrowseCursor(normalized, "malformed_cursor");
};

const withBrowseResult = (response, result) => ({
  ...response,
  data: {
    ...response?.data,
    result,
  },
});

const hasEmptyContinuation = (response) => {
  const result = normalizeBrowseMetadata(response?.data?.result || {});
  return (
    (result.values || []).length === 0 &&
    result.has_more === true &&
    typeof result.next_cursor === "string" &&
    result.next_cursor.length > 0
  );
};

const markEmptyContinuationGuardExhausted = (response) => ({
  ...response,
  data: {
    ...response?.data,
    result: {
      ...response?.data?.result,
      [EMPTY_CONTINUATION_GUARD_EXHAUSTED]: true,
    },
  },
});

const isPaginationOption = (option) =>
  option === LOAD_MORE_OPTION || option === RETRY_OPTION;

const optionValue = (option) =>
  option && typeof option === "object" && "value" in option
    ? option.value
    : option;

const optionStorageType = (option) => {
  if (option && typeof option === "object" && option.type) return option.type;
  const value = optionValue(option);
  if (typeof value === "number") return "number";
  if (typeof value === "boolean") return "boolean";
  return "string";
};

const optionIdentity = (option) =>
  `${optionStorageType(option)}:${JSON.stringify(optionValue(option))}`;

const storageTypeToFilterType = (type) => {
  if (type === "number") return "number";
  if (type === "boolean") return "boolean";
  return "text";
};

const normalizeAttributeType = (type) => {
  if (type === "text") return "string";
  if (["float", "integer"].includes(type)) return "number";
  return type;
};

const AutocompleteTextValueSelector = ({
  definition,
  filter,
  updateFilter,
  projectId: projectIdProp,
}) => {
  const initialValue = filter?.filter_config?.filter_value;
  const [inputValue, setInputValue] = useState(
    typeof initialValue === "string" ? initialValue : "",
  );
  // MUI mirrors the selected option label into inputValue. That reset is not a
  // free-text edit: committing it again on blur would turn 42/false back into
  // the strings "42"/"false" and silently change ClickHouse storage family.
  const freeTextDirtyRef = useRef(false);
  const debouncedInput = useDebounce(inputValue, 300);
  const queryClient = useQueryClient();
  const { observeId, id } = useParams();
  const projectId = projectIdProp || observeId || id;
  const definitionFilterType = definition?.filterType?.type || definition?.type;
  const attributeType =
    definitionFilterType &&
    definition?.attributeTypesExact === true &&
    Array.isArray(definition?.attributeTypes) &&
    definition.attributeTypes.length === 1
      ? normalizeAttributeType(definitionFilterType)
      : undefined;

  const queryKey = [
    "span-attribute-values",
    projectId,
    definition?.propertyId,
    attributeType || "all-types",
    debouncedInput,
  ];
  const nextPageRequestRef = useRef(null);
  const freshChainRetryRef = useRef(null);
  const [freshChainRetrying, setFreshChainRetrying] = useState(false);
  const autoScrollPageUsedRef = useRef(false);
  const paginationIdentity = JSON.stringify(queryKey);
  useEffect(() => {
    autoScrollPageUsedRef.current = false;
    setFreshChainRetrying(false);
    return () => {
      const activeRequest = freshChainRetryRef.current;
      if (activeRequest?.identity === paginationIdentity) {
        activeRequest.controller.abort();
        freshChainRetryRef.current = null;
      }
    };
  }, [paginationIdentity]);
  const {
    data,
    isLoading,
    isFetching,
    isError,
    hasNextPage,
    fetchNextPage,
    isFetchingNextPage,
    isFetchNextPageError,
  } = useInfiniteQuery({
    queryKey,
    queryFn: async ({ signal, pageParam }) => {
      const actionStartedAt = Date.now();
      const requestPage = (cursor, requestSignal = signal) =>
        axios.get(endpoints.dashboard.filterValues, {
          signal: requestSignal,
          timeout: ATTRIBUTE_VALUE_REQUEST_TIMEOUT_MS,
          params: {
            project_ids: projectId,
            metric_name: definition?.propertyId,
            metric_type: "custom_attribute",
            source: "traces",
            search: debouncedInput,
            page_size: 10,
            ...(attributeType ? { attribute_type: attributeType } : {}),
            ...(cursor ? { cursor } : {}),
          },
        });
      const cachedData = queryClient.getQueryData(queryKey);
      const cachedPages = cachedData?.pages || [];
      const isFreshChainRead = pageParam == null;
      const knownValueIdentities = isFreshChainRead
        ? []
        : cachedPages.flatMap((page) =>
            (page?.data?.result?.values || []).map(optionIdentity),
          );
      const consumedCursors = new Set(
        [
          ...(isFreshChainRead ? [] : cachedData?.pageParams || []),
          ...(isFreshChainRead
            ? []
            : cachedPages.flatMap(
                (page) => page?.data?.result?.[FOLLOWED_CURSORS_KEY] || [],
              )),
          pageParam,
        ].filter((cursor) => typeof cursor === "string" && cursor.length > 0),
      );
      const initialResponse = await requestPage(pageParam);
      const checkedResult = (response) =>
        validateBrowseCursor(response?.data?.result || {}, consumedCursors);
      // Every checkpoint shares the same action clock. The follower stops
      // before a continuation can multiply the four-second server wall.
      const {
        response,
        rows: values,
        followedCursors,
      } = await accumulateUniqueListContinuations({
        initialResponse,
        rowsFromResponse: (page) => page?.data?.result?.values || [],
        identityFromRow: optionIdentity,
        knownIdentities: knownValueIdentities,
        targetRowCount: isFreshChainRead ? 1 : 10,
        metadataFromResponse: (response) => {
          const checked = checkedResult(response);
          return isBrowseCursorStopped(checked)
            ? { ...checked, has_more: false, next_cursor: null }
            : checked;
        },
        nextResponse: requestPage,
        onContinuation: (metadata) => {
          if (metadata?.next_cursor) consumedCursors.add(metadata.next_cursor);
        },
        isCurrent: () => !signal.aborted,
        cancellationSignal: signal,
        startedAt: actionStartedAt,
        // One interaction owns one physical HTTP request. Empty advancing
        // checkpoints stay explicit through the signed cursor so a second
        // four-second request cannot push the same click beyond five seconds.
        maxContinuations: 0,
        maxElapsedMs: ATTRIBUTE_VALUE_REQUEST_TIMEOUT_MS,
      });
      const accumulatedResponse = withBrowseResult(response, {
        ...response?.data?.result,
        values,
      });
      // A sparse exact lookup can need more checkpoints than one browser
      // action may safely fan out. Keep the signed cursor as the next page,
      // but mark the action as bounded so the picker offers a retry instead
      // of exposing an empty transport page as ordinary pagination.
      const boundedResponse = hasEmptyContinuation(accumulatedResponse)
        ? markEmptyContinuationGuardExhausted(accumulatedResponse)
        : accumulatedResponse;
      const checkedResponse = withBrowseResult(
        boundedResponse,
        checkedResult(boundedResponse),
      );
      return {
        ...checkedResponse,
        data: {
          ...checkedResponse?.data,
          result: {
            ...checkedResponse?.data?.result,
            [FOLLOWED_CURSORS_KEY]: followedCursors,
          },
        },
      };
    },
    initialPageParam: null,
    getNextPageParam: (lastPage, allPages, lastPageParam, allPageParams) => {
      const result = normalizeBrowseMetadata(lastPage?.data?.result || {});
      if (isBrowseCursorStopped(result)) return undefined;
      const nextCursor = result.has_more === true ? result.next_cursor : null;
      if (!nextCursor) return undefined;
      const requestedCursors = new Set(
        (allPageParams || []).filter(
          (cursor) => typeof cursor === "string" && cursor.length > 0,
        ),
      );
      for (const page of allPages || []) {
        for (const cursor of page?.data?.result?.[FOLLOWED_CURSORS_KEY] || []) {
          requestedCursors.add(cursor);
        }
      }
      return nextCursor === lastPageParam || requestedCursors.has(nextCursor)
        ? undefined
        : nextCursor;
    },
    enabled: Boolean(projectId) && Boolean(definition?.propertyId),
    staleTime: 30000,
    retry: false,
    refetchOnWindowFocus: false,
    refetchOnMount: false,
    refetchOnReconnect: false,
    meta: { errorHandled: true },
  });
  const retryFreshChain = useCallback(() => {
    const activeRequest = freshChainRetryRef.current;
    if (activeRequest?.identity === paginationIdentity) {
      return activeRequest.promise;
    }

    const controller = new AbortController();
    setFreshChainRetrying(true);
    const request = (async () => {
      await queryClient.cancelQueries({ queryKey, exact: true });
      const previousData = queryClient.getQueryData(queryKey);
      const response = await axios.get(endpoints.dashboard.filterValues, {
        signal: controller.signal,
        timeout: ATTRIBUTE_VALUE_REQUEST_TIMEOUT_MS,
        params: {
          project_ids: projectId,
          metric_name: definition?.propertyId,
          metric_type: "custom_attribute",
          source: "traces",
          search: debouncedInput,
          page_size: 10,
          ...(attributeType ? { attribute_type: attributeType } : {}),
        },
      });
      const checkedResult = validateBrowseCursor(
        response?.data?.result || {},
        new Set(),
      );
      let freshResponse = withBrowseResult(response, {
        ...checkedResult,
        [FOLLOWED_CURSORS_KEY]: [],
      });
      if (hasEmptyContinuation(freshResponse)) {
        freshResponse = markEmptyContinuationGuardExhausted(freshResponse);
      }

      const seenValues = new Set();
      const retainedValues = [
        ...(previousData?.pages || []).flatMap(
          (page) => page?.data?.result?.values || [],
        ),
        ...(freshResponse?.data?.result?.values || []),
      ].filter((option) => {
        const identity = optionIdentity(option);
        if (seenValues.has(identity)) return false;
        seenValues.add(identity);
        return true;
      });
      const compactedResponse = withBrowseResult(freshResponse, {
        ...freshResponse?.data?.result,
        values: retainedValues,
      });
      // Retain selectable rows, but publish only the newly fetched transport
      // page. Calling TanStack's infinite-query refetch here would replay the
      // whole cached cursor chain before the user regains control.
      queryClient.setQueryData(queryKey, {
        pages: [compactedResponse],
        pageParams: [null],
      });
      return compactedResponse;
    })();
    const trackedRequest = {
      identity: paginationIdentity,
      controller,
      promise: null,
    };
    const settledPromise = request.finally(() => {
      if (freshChainRetryRef.current === trackedRequest) {
        freshChainRetryRef.current = null;
        setFreshChainRetrying(false);
      }
    });
    trackedRequest.promise = settledPromise;
    freshChainRetryRef.current = trackedRequest;
    return settledPromise;
  }, [
    attributeType,
    debouncedInput,
    definition?.propertyId,
    paginationIdentity,
    projectId,
    queryClient,
    queryKey,
  ]);
  const requestNextPage = useCallback(() => {
    const activeRequest = nextPageRequestRef.current;
    if (activeRequest?.identity === paginationIdentity) {
      return activeRequest.promise;
    }
    if (!hasNextPage || isFetchingNextPage) return Promise.resolve();

    const promise = Promise.resolve(fetchNextPage());
    const request = { identity: paginationIdentity, promise };
    nextPageRequestRef.current = request;
    const clearRequest = () => {
      if (nextPageRequestRef.current === request) {
        nextPageRequestRef.current = null;
      }
    };
    promise.then(clearRequest, clearRequest);
    return promise;
  }, [fetchNextPage, hasNextPage, isFetchingNextPage, paginationIdentity]);
  const seen = new Set();
  const options = (data?.pages || []).flatMap((page) =>
    (page?.data?.result?.values || []).flatMap((item) => {
      const value = optionValue(item);
      const type = optionStorageType(item);
      const key = optionIdentity(item);
      if (seen.has(key)) return [];
      seen.add(key);
      return [{ value, type }];
    }),
  );
  const continuationGuardExhausted = Boolean(
    data?.pages?.at(-1)?.data?.result?.[EMPTY_CONTINUATION_GUARD_EXHAUSTED],
  );
  const cursorChainStopped = (() => {
    const pages = data?.pages || [];
    if (pages.some((page) => isBrowseCursorStopped(page?.data?.result || {}))) {
      return true;
    }
    const lastResult = normalizeBrowseMetadata(
      pages.at(-1)?.data?.result || {},
    );
    const nextCursor =
      lastResult.has_more === true ? lastResult.next_cursor : null;
    if (typeof nextCursor !== "string" || nextCursor.length === 0) return false;
    const consumedCursors = new Set(
      (data?.pageParams || []).filter(
        (cursor) => typeof cursor === "string" && cursor.length > 0,
      ),
    );
    for (const page of pages) {
      for (const cursor of page?.data?.result?.[FOLLOWED_CURSORS_KEY] || []) {
        consumedCursors.add(cursor);
      }
    }
    return consumedCursors.has(nextCursor);
  })();
  const pickerOptions = hasNextPage
    ? [...options, LOAD_MORE_OPTION]
    : isError || cursorChainStopped
      ? [...options, RETRY_OPTION]
      : options;
  const filterConfig = filter?.filter_config || {};
  const isListOperator = LIST_OPERATORS.has(filterConfig.filter_op);
  const selectedRawValues = isListOperator
    ? Array.isArray(filterConfig.filter_value)
      ? filterConfig.filter_value
      : filterConfig.filter_value == null || filterConfig.filter_value === ""
        ? []
        : [filterConfig.filter_value]
    : [filterConfig.filter_value].filter(
        (value) => value !== undefined && value !== null && value !== "",
      );
  const selectedTypes = Array.isArray(filterConfig.attribute_value_types)
    ? filterConfig.attribute_value_types
    : [];
  const selectedOptions = selectedRawValues.map((value, index) => {
    const selectedType = selectedTypes[index];
    return (
      options.find(
        (option) =>
          Object.is(option.value, value) &&
          (!selectedType || option.type === selectedType),
      ) || { value, type: selectedType || optionStorageType(value) }
    );
  });

  const updateSelectedValues = (selection) => {
    const selected = (
      Array.isArray(selection) ? selection : [selection]
    ).filter((option) => option != null && !isPaginationOption(option));
    const values = selected.map(optionValue);
    const types = selected.map(optionStorageType);

    updateFilter(filter.id, (existingFilter) => {
      const existingConfig = existingFilter.filter_config || {};
      if (isListOperator) {
        return {
          ...existingFilter,
          filter_config: {
            ...existingConfig,
            // Typed provenance is only valid for in/not_in. Keep the wire type
            // text so a mixed scalar list is accepted, while the aligned type
            // array selects the exact ClickHouse storage family per value.
            filter_type: "text",
            filter_value: values,
            attribute_value_types: types,
          },
        };
      }

      const value = values[0] ?? "";
      const nextFilterType = storageTypeToFilterType(types[0]);
      const currentOp = existingConfig.filter_op || "equals";
      const validOps = FILTER_TYPE_ALLOWED_OPS[nextFilterType] || [];
      const nextConfig = { ...existingConfig };
      delete nextConfig.attribute_value_types;
      return {
        ...existingFilter,
        filter_config: {
          ...nextConfig,
          filter_type: nextFilterType,
          filter_op: validOps.includes(currentOp) ? currentOp : "equals",
          filter_value: value,
        },
      };
    });
  };

  return (
    <Autocomplete
      freeSolo
      multiple={isListOperator}
      size="small"
      options={pickerOptions}
      filterOptions={(availableOptions) => availableOptions}
      getOptionLabel={(option) => {
        if (option === LOAD_MORE_OPTION) {
          return isFetchNextPageError || continuationGuardExhausted
            ? "Retry loading values"
            : "Load more values";
        }
        if (option === RETRY_OPTION) return "Retry loading values";
        const value = optionValue(option);
        return typeof value === "string" ? value : JSON.stringify(value);
      }}
      isOptionEqualToValue={(option, value) =>
        Object.is(optionValue(option), optionValue(value)) &&
        optionStorageType(option) === optionStorageType(value)
      }
      renderOption={(props, option) =>
        isPaginationOption(option) ? (
          <li
            {...props}
            onMouseDown={(event) => event.preventDefault()}
            onClick={(event) => {
              event.preventDefault();
              event.stopPropagation();
              if (option === RETRY_OPTION) {
                if (!isFetching && !freshChainRetrying) {
                  void retryFreshChain().catch(() => {});
                }
              } else {
                requestNextPage();
              }
            }}
          >
            {option === RETRY_OPTION
              ? isFetching || freshChainRetrying
                ? "Retrying values…"
                : "Retry loading values"
              : isFetchingNextPage
                ? "Loading more values…"
                : isFetchNextPageError || continuationGuardExhausted
                  ? "Retry loading values"
                  : "Load more values"}
          </li>
        ) : (
          <li {...props}>
            {typeof optionValue(option) === "string"
              ? optionValue(option)
              : JSON.stringify(optionValue(option))}
          </li>
        )
      }
      loading={isLoading}
      onOpen={() => {
        autoScrollPageUsedRef.current = false;
      }}
      ListboxProps={{
        onScroll: (event) => {
          const list = event.currentTarget;
          const isNearBottom =
            list.scrollTop + list.clientHeight >= list.scrollHeight - 24;
          if (!isNearBottom) {
            autoScrollPageUsedRef.current = false;
            return;
          }
          if (
            hasNextPage &&
            !isFetchingNextPage &&
            !autoScrollPageUsedRef.current
          ) {
            // One deliberate trip to the bottom advances one page. Browsers
            // can emit more momentum/resize scroll events after a fast page
            // render; letting each event fetch would silently drain the whole
            // cursor chain and make "Load more" appear endless.
            autoScrollPageUsedRef.current = true;
            requestNextPage();
          }
        },
      }}
      inputValue={inputValue}
      onInputChange={(_, newInputValue, reason) => {
        if (
          reason === "reset" &&
          ["Load more values", "Retry loading values"].includes(newInputValue)
        ) {
          return;
        }
        freeTextDirtyRef.current = reason === "input";
        setInputValue(newInputValue);
      }}
      value={isListOperator ? selectedOptions : selectedOptions[0] || null}
      onChange={(_, newValue) => {
        freeTextDirtyRef.current = false;
        if (newValue === RETRY_OPTION) {
          if (!isFetching && !freshChainRetrying) {
            void retryFreshChain().catch(() => {});
          }
          return;
        }
        if (newValue === LOAD_MORE_OPTION) {
          requestNextPage();
          return;
        }
        if (
          Array.isArray(newValue) &&
          newValue.some((option) => isPaginationOption(option))
        ) {
          if (newValue.includes(RETRY_OPTION)) {
            if (!isFetching && !freshChainRetrying) {
              void retryFreshChain().catch(() => {});
            }
          } else {
            requestNextPage();
          }
          return;
        }
        updateSelectedValues(newValue);
      }}
      onBlur={() => {
        if (!isListOperator && freeTextDirtyRef.current) {
          freeTextDirtyRef.current = false;
          updateSelectedValues({ value: inputValue, type: "string" });
        }
      }}
      renderInput={(params) => (
        <TextField
          {...params}
          placeholder="Type or select a value..."
          variant="outlined"
          size="small"
          sx={{ minWidth: 180 }}
          InputProps={{
            ...params.InputProps,
            endAdornment: (
              <>
                {isLoading || isFetching || freshChainRetrying ? (
                  <CircularProgress color="inherit" size={16} />
                ) : null}
                {params.InputProps.endAdornment}
              </>
            ),
          }}
        />
      )}
      sx={{ minWidth: 200 }}
    />
  );
};

AutocompleteTextValueSelector.propTypes = {
  definition: PropTypes.shape({
    propertyId: PropTypes.string,
    type: PropTypes.string,
    filterType: PropTypes.shape({ type: PropTypes.string }),
    attributeTypes: PropTypes.arrayOf(PropTypes.string),
    attributeTypesExact: PropTypes.bool,
  }),
  filter: PropTypes.shape({
    filter_config: PropTypes.shape({
      filter_value: PropTypes.oneOfType([
        PropTypes.string,
        PropTypes.number,
        PropTypes.bool,
        PropTypes.array,
      ]),
      filter_op: PropTypes.string,
      filter_type: PropTypes.string,
      attribute_value_types: PropTypes.arrayOf(PropTypes.string),
    }),
    id: PropTypes.string.isRequired,
  }),
  updateFilter: PropTypes.func.isRequired,
  projectId: PropTypes.string,
};

export default AutocompleteTextValueSelector;
