import React, { useMemo } from "react";
import PropTypes from "prop-types";
import { Box, Skeleton, Stack, Typography } from "@mui/material";
import CustomTooltip from "src/components/tooltip";
import { ShowComponent } from "src/components/show";
import { pluralize } from "src/utils/utils";
import {
  filterLabelsMatchValues,
  useResolvedFilterOptions,
} from "./useResolvedFilterOptions";

export default function FilterValueLabel({
  filter,
  source,
  variant = "body2",
  innerRef,
  onClick,
}) {
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
  const { options, isLoading } = useResolvedFilterOptions(
    filter,
    source,
    values.length > 0 && !labelsAreValues,
  );

  const labels = useMemo(() => {
    if (labelsAreValues) return values;
    const byValue = new Map(options.map((o) => [o.value, o.label ?? o.value]));
    return values.map((v) => byValue.get(v) ?? v);
  }, [labelsAreValues, options, values]);

  const hasValue = values.length > 0;
  const isResolving =
    hasValue && !labelsAreValues && isLoading && options.length === 0;
  const extra = Math.max(labels.length - 1, 0);
  const entity = (filter?.name || "item").toLowerCase();
  const entityLabel = pluralize(entity, extra);
  const sizeVariant = variant === "caption" ? "s2" : "s2_1";
  const showBadge = extra > 0 && !isResolving;

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
          {hasValue ? labels[0] : "Select value..."}
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
            borderRadius: 0.75,
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
              {l}
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
      PropTypes.arrayOf(
        PropTypes.oneOfType([PropTypes.string, PropTypes.number]),
      ),
    ]),
    choices: PropTypes.arrayOf(
      PropTypes.oneOfType([
        PropTypes.string,
        PropTypes.shape({
          value: PropTypes.oneOfType([PropTypes.string, PropTypes.number]),
          label: PropTypes.string,
        }),
      ]),
    ),
  }),
  source: PropTypes.string,
  variant: PropTypes.string,
  innerRef: PropTypes.func,
  onClick: PropTypes.func,
};
