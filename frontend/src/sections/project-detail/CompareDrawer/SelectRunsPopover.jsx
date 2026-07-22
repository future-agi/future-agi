import {
  Box,
  Checkbox,
  InputAdornment,
  ListItem,
  ListItemButton,
  ListItemIcon,
  ListItemText,
  Popover,
  Skeleton,
  TextField,
  Typography,
} from "@mui/material";
import { useInfiniteQuery } from "@tanstack/react-query";
import PropTypes from "prop-types";
import React, { useMemo, useState } from "react";
import { useParams } from "react-router";
import Iconify from "src/components/iconify";
import { useScrollEnd } from "src/hooks/use-scroll-end";
import axios, { endpoints } from "src/utils/axios";
import { Events, PropertyName, trackEvent } from "src/utils/Mixpanel";

const getProjectVersionIds = (result) =>
  result?.projectVersionIds || result?.project_version_ids || [];

const SelectRunsPopover = ({
  open,
  onClose,
  anchorElement,
  selectedRuns,
  setSelectedRuns,
}) => {
  const [searchText, setSearchText] = useState("");
  const { projectId } = useParams();

  const { data, isLoading, fetchNextPage, isFetchingNextPage } =
    useInfiniteQuery({
      queryKey: ["runs-list", searchText],
      queryFn: ({ pageParam }) =>
        axios.get(endpoints.project.runListSearch(), {
          params: {
            search_name: searchText,
            page_number: pageParam,
            project_id: projectId,
          },
        }),
      getNextPageParam: (data, p) => {
        return data?.data?.result?.next ? p.length : null;
      },
      initialPageParam: 0,
    });

  const list = useMemo(
    () =>
      data?.pages.reduce(
        (acc, curr) => [...acc, ...getProjectVersionIds(curr.data.result)],
        [],
      ) || [],
    [data],
  );

  const scrollRef = useScrollEnd(() => {
    if (isFetchingNextPage || isLoading) {
      return;
    }
    fetchNextPage();
  }, [isFetchingNextPage, isLoading]);

  return (
    <Popover
      open={open}
      onClose={onClose}
      anchorEl={anchorElement}
      anchorOrigin={{
        vertical: "bottom",
        horizontal: "right",
      }}
      transformOrigin={{
        vertical: "top",
        horizontal: "right",
      }}
      PaperProps={{
        sx: {
          minWidth: anchorElement?.clientWidth,
        },
      }}
    >
      <Box sx={{ gap: "6px", display: "flex", flexDirection: "column" }}>
        <TextField
          placeholder="Search runs to compare"
          size="small"
          fullWidth
          value={searchText}
          onChange={(e) => setSearchText(e.target.value)}
          InputProps={{
            startAdornment: (
              <InputAdornment position="start">
                <Iconify icon="eva:search-fill" sx={{ color: "divider" }} />
              </InputAdornment>
            ),
          }}
        />
        <Typography fontWeight={600} fontSize="12px" color="text.disabled">
          All runs
        </Typography>
        <Box
          sx={{
            display: "flex",
            maxHeight: "226px",
            overflowY: "auto",
            flexDirection: "column",
          }}
          ref={scrollRef}
        >
          {isLoading ? (
            <SkeletonRun count={3} />
          ) : !list?.length ? (
            <NoRunsFound />
          ) : (
            list.map((item) => (
              <RunItem
                item={item}
                key={item.id}
                selected={selectedRuns.includes(item.id)}
                onChange={(newValue) => {
                  if (newValue) {
                    trackEvent(Events.runsSelected, {
                      [PropertyName.click]: item,
                    });
                  }
                  setSelectedRuns((prev) => {
                    if (newValue) {
                      return prev.includes(item.id) ? prev : [...prev, item.id];
                    } else {
                      return prev.filter((id) => id !== item.id);
                    }
                  });
                }}
              />
            ))
          )}
        </Box>
      </Box>
    </Popover>
  );
};

const RunItem = ({ item, selected, onChange }) => {
  return (
    <Box sx={{ display: "flex", alignItems: "center" }}>
      <ListItem key={item.id} disablePadding sx={{ borderRadius: "6px" }} dense>
        <ListItemButton
          dense
          sx={{ paddingY: 0, paddingX: 1.5 }}
          onClick={() => onChange(!selected)}
        >
          <ListItemIcon sx={{ marginRight: 0 }}>
            <Checkbox
              edge="start"
              checked={selected}
              onChange={(e, checked) => onChange(checked)}
              tabIndex={-1}
              disableRipple
              inputProps={{ "aria-labelledby": item.id }}
            />
          </ListItemIcon>
          <ListItemText
            primaryTypographyProps={{
              fontWeight: 500,
              fontSize: "14px",
              borderRadius: "6px",
              maxWidth: "170px",
              overflow: "hidden",
              textOverflow: "ellipsis",
              whiteSpace: "nowrap",
            }}
            id={item.id}
            primary={item.name}
          />
        </ListItemButton>
      </ListItem>
    </Box>
  );
};

RunItem.propTypes = {
  item: PropTypes.object,
  selected: PropTypes.bool,
  onChange: PropTypes.func,
};

const NoRunsFound = () => {
  return (
    <Box
      sx={{
        display: "flex",
        justifyContent: "center",
        width: "100%",
        minHeight: "100px",
        alignItems: "center",
      }}
    >
      <Typography variant="caption">No runs found</Typography>
    </Box>
  );
};

const SkeletonRun = ({ count = 3 }) => {
  return (
    <>
      {Array.from({ length: count }).map((_, index) => (
        <Skeleton
          variant="rectangular"
          key={index}
          sx={{ height: "30px", marginY: "2px", borderRadius: "6px" }}
        />
      ))}
    </>
  );
};

SkeletonRun.propTypes = {
  count: PropTypes.number,
};

SelectRunsPopover.propTypes = {
  open: PropTypes.bool,
  onClose: PropTypes.func,
  anchorElement: PropTypes.object,
  selectedRuns: PropTypes.array,
  setSelectedRuns: PropTypes.func,
};

export default SelectRunsPopover;
