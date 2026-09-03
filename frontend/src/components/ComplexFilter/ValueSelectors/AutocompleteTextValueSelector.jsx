import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import PropTypes from "prop-types";
import { Autocomplete, TextField, CircularProgress } from "@mui/material";
import { useInfiniteQuery, useQueryClient } from "@tanstack/react-query";
import axios, { endpoints } from "src/utils/axios";
import { useDebounce } from "src/hooks/use-debounce";
import { useParams } from "react-router-dom";
import BoundedCursorPaginationControl from "src/components/BoundedCursorPaginationControl";
import {
  FILTER_TYPE_ALLOWED_OPS,
  LIST_FILTER_OPS,
} from "src/api/contracts/filter-contract.generated";
import { boundPropertyCatalogSearch } from "src/hooks/useDashboards";
import { accumulateUniqueListContinuations } from "src/sections/projects/LLMTracing/listCursorPagination";
import {
  FILTER_VALUE_MIN_VISIBLE_RESULTS,
  FILTER_VALUE_REQUEST_TIMEOUT_MS,
  INTERACTIVE_TABLE_PAGE_SIZE,
  PROPERTY_CATALOG_SEARCH_DEBOUNCE_MS,
} from "src/config/runtime_limits";

const PAGINATION_SENTINEL_OPTION = Object.freeze({
  __paginationSentinel: true,
});
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
// independently configurable so ordinary server timeouts can retain their
// structured response while transport stalls still release the UI.
const ATTRIBUTE_VALUE_REQUEST_TIMEOUT_MS = FILTER_VALUE_REQUEST_TIMEOUT_MS;

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

const isPaginationOption = (option) => option === PAGINATION_SENTINEL_OPTION;

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
  const debouncedInput = useDebounce(
    inputValue,
    PROPERTY_CATALOG_SEARCH_DEBOUNCE_MS,
  );
  const boundedDebouncedInput = boundPropertyCatalogSearch(debouncedInput);
  const queryClient = useQueryClient();
  const { observeId, id } = useParams();
  const projectId = projectIdProp || observeId || id;
  const definitionFilterType = definition?.filterType?.type || definition?.type;
  const propertyRegistryId = definition?.propertyId
    ? definition?.registryId || `custom_attribute:${definition.propertyId}`
    : "";
  const attributeType =
    definitionFilterType &&
    definition?.attributeTypesExact === true &&
    Array.isArray(definition?.attributeTypes) &&
    definition.attributeTypes.length === 1
      ? normalizeAttributeType(definitionFilterType)
      : undefined;

  const queryKey = useMemo(
    () => [
      "span-attribute-values",
      projectId,
      propertyRegistryId,
      attributeType || "all-types",
      boundedDebouncedInput,
    ],
    [attributeType, boundedDebouncedInput, projectId, propertyRegistryId],
  );
  const valueOptionsListRef = useRef(null);
  const nextPageRequestRef = useRef(null);
  const freshChainRetryRef = useRef(null);
  const [freshChainRetrying, setFreshChainRetrying] = useState(false);
  const [paginationChainGeneration, setPaginationChainGeneration] = useState(0);
  const paginationIdentity = JSON.stringify(queryKey);
  useEffect(() => {
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
            property_id: propertyRegistryId,
            metric_name: definition?.propertyId,
            metric_type: "custom_attribute",
            source: "traces",
            search: boundedDebouncedInput,
            page_size: INTERACTIVE_TABLE_PAGE_SIZE,
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
        targetRowCount: isFreshChainRead
          ? FILTER_VALUE_MIN_VISIBLE_RESULTS
          : INTERACTIVE_TABLE_PAGE_SIZE,
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
    enabled: Boolean(projectId) && Boolean(propertyRegistryId),
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
          property_id: propertyRegistryId,
          metric_name: definition?.propertyId,
          metric_type: "custom_attribute",
          source: "traces",
          search: boundedDebouncedInput,
          page_size: INTERACTIVE_TABLE_PAGE_SIZE,
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
      setPaginationChainGeneration((generation) => generation + 1);
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
    boundedDebouncedInput,
    definition?.propertyId,
    propertyRegistryId,
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
  const pages = data?.pages || [];
  const lastResult = normalizeBrowseMetadata(pages.at(-1)?.data?.result || {});
  const continuationKey =
    lastResult.has_more === true &&
    typeof lastResult.next_cursor === "string" &&
    lastResult.next_cursor.length > 0
      ? lastResult.next_cursor
      : null;
  const cursorChainStopped = (() => {
    if (pages.some((page) => isBrowseCursorStopped(page?.data?.result || {}))) {
      return true;
    }
    if (!continuationKey) return false;
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
    return consumedCursors.has(continuationKey);
  })();
  const paginationError = Boolean(
    isError ||
      isFetchNextPageError ||
      cursorChainStopped ||
      continuationGuardExhausted,
  );
  const retryRequiresFreshChain =
    cursorChainStopped || (isError && !isFetchNextPageError);
  const paginationLoadAction = retryRequiresFreshChain
    ? retryFreshChain
    : requestNextPage;
  const showPaginationSentinel = Boolean(
    hasNextPage || paginationError || isFetchingNextPage || freshChainRetrying,
  );
  const pickerOptions = showPaginationSentinel
    ? [...options, PAGINATION_SENTINEL_OPTION]
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
        if (isPaginationOption(option)) return "";
        const value = optionValue(option);
        return typeof value === "string" ? value : JSON.stringify(value);
      }}
      getOptionDisabled={isPaginationOption}
      isOptionEqualToValue={(option, value) =>
        Object.is(optionValue(option), optionValue(value)) &&
        optionStorageType(option) === optionStorageType(value)
      }
      renderOption={(props, option) => {
        if (isPaginationOption(option)) {
          const sentinelProps = { ...props };
          const optionKey = sentinelProps.key;
          delete sentinelProps.key;
          return (
            <li
              {...sentinelProps}
              key={optionKey}
              role="presentation"
              onMouseDown={(event) => event.preventDefault()}
              onClick={(event) => {
                event.preventDefault();
                event.stopPropagation();
              }}
            >
              <BoundedCursorPaginationControl
                resetKey={`${paginationIdentity}:${paginationChainGeneration}`}
                rootRef={valueOptionsListRef}
                autoAdvanceWhileVisible={false}
                requireUserAdvanceGesture
                testId="attribute-value-pagination-sentinel"
                loadingLabel="Loading more values…"
                retryLabel="Retry loading values"
                channels={[
                  {
                    channelKey: "attribute-values",
                    hasNextPage: Boolean(hasNextPage),
                    continuationKey,
                    isFetching:
                      isFetchingNextPage || freshChainRetrying || isFetching,
                    error: paginationError,
                    loadNextPage: paginationLoadAction,
                  },
                ]}
              />
            </li>
          );
        }
        return (
          <li {...props}>
            {typeof optionValue(option) === "string"
              ? optionValue(option)
              : JSON.stringify(optionValue(option))}
          </li>
        );
      }}
      loading={isLoading}
      ListboxProps={{ ref: valueOptionsListRef }}
      inputValue={inputValue}
      onInputChange={(_, newInputValue, reason) => {
        freeTextDirtyRef.current = reason === "input";
        setInputValue(newInputValue);
      }}
      value={isListOperator ? selectedOptions : selectedOptions[0] || null}
      onChange={(_, newValue) => {
        freeTextDirtyRef.current = false;
        if (
          isPaginationOption(newValue) ||
          (Array.isArray(newValue) &&
            newValue.some((option) => isPaginationOption(option)))
        ) {
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
    registryId: PropTypes.string,
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
