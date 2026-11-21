import React, { useEffect, useMemo, useRef, useState } from "react";
import PropTypes from "prop-types";
import { Box, Skeleton, Stack, Typography } from "@mui/material";
import CustomTooltip from "src/components/tooltip";
import { ShowComponent } from "src/components/show";
import { pluralize } from "src/utils/utils";
import {
  filterLabelsMatchValues,
  useResolvedFilterOptions,
} from "./useResolvedFilterOptions";

const MAX_LABEL_CONTINUATION_READS = 12;

export default function FilterValueLabel({
  filter,
  source,
  variant = "body2",
  innerRef,
  onClick,
  disableTooltip = false,
}) {
  const [tooltipOpen, setTooltipOpen] = useState(false);
  const tooltipDisabled = useRef(disableTooltip);
  const values = useMemo(
    () => (Array.isArray(filter?.value) ? filter.value : []),
    [filter?.value],
  );
  // When the backend's labels are just the values there is nothing to resolve,
  // so skip the fetch — it would be a workspace-wide span scan to learn that
  // "gpt-4o" is labelled "gpt-4o". It also keeps saved chips correct: the
  // value list is time-windowed, so a filter on an older value would miss the
  // lookup and fall back to the raw value anyway.
  const labelsAreValues = filterLabelsMatchValues(filter);
  const {
    options,
    queryIdentity,
    isLoading,
    hasNextPage,
    fetchNextPage,
    isFetchingNextPage,
    isFetchNextPageError,
    cursorChainStopped,
  } = useResolvedFilterOptions(
    filter,
    source,
    values.length > 0 && !labelsAreValues,
  );

  const byValue = useMemo(
    () => new Map(options.map((o) => [o.value, o.label ?? o.value])),
    [options],
  );
  const labels = useMemo(() => {
    if (labelsAreValues) return values;
    return values.map((v) => byValue.get(v) ?? v);
  }, [labelsAreValues, byValue, values]);

  const hasValue = values.length > 0;
  // The value list is a time-sliced cursor chain: a bounded first page can be
  // empty (or hold other values) while the saved ids are still further down
  // the chain. Keep reading until every selected value has a label or the
  // chain ends; only then fall back to the raw value.
  // Bounded per chip: a value that no longer exists must not make every mount
  // walk the whole workspace vocabulary.
  // A reused widget row can switch queries without changing its selected ids.
  // A new array with the same contents for the same query is still one budget.
  const budgetKey = useMemo(
    () => JSON.stringify([queryIdentity, values]),
    [queryIdentity, values],
  );
  // Reset during render, not in an effect: `canReadMore` below reads the
  // counter in this same render, so a new selection must see its fresh
  // budget immediately rather than after some unrelated re-render.
  const continuationReads = useRef({ key: budgetKey, count: 0 });
  if (continuationReads.current.key !== budgetKey) {
    continuationReads.current = { key: budgetKey, count: 0 };
  }
  const hasUnresolved =
    hasValue && !labelsAreValues && values.some((v) => !byValue.has(v));
  const canReadMore =
    hasUnresolved &&
    Boolean(hasNextPage) &&
    !isFetchNextPageError &&
    !cursorChainStopped &&
    continuationReads.current.count < MAX_LABEL_CONTINUATION_READS;
  useEffect(() => {
    if (canReadMore && !isLoading && !isFetchingNextPage) {
      continuationReads.current.count += 1;
      // Chips sharing one query must not cancel each other's page read.
      void fetchNextPage?.({ cancelRefetch: false });
    }
  }, [budgetKey, canReadMore, isLoading, isFetchingNextPage, fetchNextPage]);
  const isResolving =
    hasValue &&
    !labelsAreValues &&
    ((isLoading && options.length === 0) ||
      (!byValue.has(values[0]) && (canReadMore || isFetchingNextPage)));
  const extra = Math.max(labels.length - 1, 0);
  const entity = (filter?.name || "item").toLowerCase();
  const entityLabel = pluralize(entity, extra);
  const sizeVariant = variant === "caption" ? "s2" : "s2_1";
  const showBadge = extra > 0 && !isResolving;

  useEffect(() => {
    tooltipDisabled.current = disableTooltip;
    if (disableTooltip || !showBadge) setTooltipOpen(false);
  }, [disableTooltip, showBadge]);

  const content = (
    <Stack
      ref={innerRef}
      onClick={onClick}
      direction="row"
      alignItems="center"
      gap={0.5}
      sx={{
        flex: 1,
        minWidth: 0,
        cursor: "pointer",
        "&:hover .filter-value-name": { color: "primary.main" },
      }}
    >
      {isResolving ? (
        <Skeleton
          variant="rounded"
          width={96}
          height={14}
          sx={{ flexShrink: 0 }}
        />
      ) : (
        <Typography
          className="filter-value-name"
          variant={sizeVariant}
          noWrap
          sx={{
            color: hasValue ? "text.primary" : "text.disabled",
            overflow: "hidden",
            textOverflow: "ellipsis",
          }}
        >
          {hasValue ? String(labels[0]) : "Select value..."}
        </Typography>
      )}
      <ShowComponent condition={showBadge}>
        <Typography
          component="span"
          variant="s3"
          fontWeight="fontWeightSemiBold"
          sx={{
            flexShrink: 0,
            px: 0.625,
            py: "1px",
            borderRadius: "4px",
            bgcolor: "action.selected",
            color: "text.secondary",
            whiteSpace: "nowrap",
          }}
        >
          +{extra} {entityLabel}
        </Typography>
      </ShowComponent>
    </Stack>
  );

  return (
    <CustomTooltip
      show={showBadge}
      // Keep the wrapper mounted: the value picker anchors to this DOM node.
      open={tooltipOpen && !disableTooltip}
      onOpen={() => {
        // MUI may call this from a hover timer queued before the picker opened.
        if (!tooltipDisabled.current) setTooltipOpen(true);
      }}
      onClose={() => setTooltipOpen(false)}
      disableHoverListener={disableTooltip}
      disableFocusListener={disableTooltip}
      disableTouchListener={disableTooltip}
      placement="top"
      size="small"
      arrow
      title={
        <Box
          component="ul"
          sx={{ m: 0, pl: 2, maxHeight: 240, overflowY: "auto" }}
        >
          {labels.map((l, idx) => (
            <Box
              component="li"
              key={`${l}-${idx}`}
              sx={{ typography: "s2", lineHeight: 1.6 }}
            >
              {String(l)}
            </Box>
          ))}
        </Box>
      }
    >
      {content}
    </CustomTooltip>
  );
}

FilterValueLabel.propTypes = {
  filter: PropTypes.shape({
    id: PropTypes.string,
    name: PropTypes.string,
    type: PropTypes.string,
    outputType: PropTypes.string,
    value: PropTypes.oneOfType([
      PropTypes.string,
      PropTypes.number,
      PropTypes.bool,
      PropTypes.arrayOf(
        PropTypes.oneOfType([
          PropTypes.string,
          PropTypes.number,
          PropTypes.bool,
        ]),
      ),
    ]),
    choices: PropTypes.arrayOf(
      PropTypes.oneOfType([
        PropTypes.string,
        PropTypes.bool,
        PropTypes.shape({
          value: PropTypes.oneOfType([
            PropTypes.string,
            PropTypes.number,
            PropTypes.bool,
          ]),
          label: PropTypes.string,
        }),
      ]),
    ),
  }),
  source: PropTypes.string,
  variant: PropTypes.string,
  innerRef: PropTypes.func,
  onClick: PropTypes.func,
  disableTooltip: PropTypes.bool,
};
