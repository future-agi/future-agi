import {
  Box,
  InputAdornment,
  MenuItem,
  Popover,
  Select,
  styled,
  TextField,
  Typography,
} from "@mui/material";
import React, { useEffect, useMemo, useRef, useState } from "react";
import { useLocation, useNavigate, useParams } from "react-router";
import { useInfiniteExperimentList } from "src/api/develop/experiment-detail";
import Iconify from "src/components/iconify";
import { useDebounce } from "src/hooks/use-debounce";
import ScrollingWrapper from "src/components/custom-model-dropdown/ScrollingWrapper.jsx";

const DevelopSelect = styled(Select)(({ theme }) => ({
  "& .MuiSelect-select": {
    paddingTop: 4,
    paddingBottom: 4,
    color: theme.palette.text.primary,
    fontWeight: 500,
    width: "245px",
  },
}));

const DevelopExperimentSelect = () => {
  const [selectOpen, setSelectOpen] = useState(false);
  const anchorRef = useRef(null);
  const location = useLocation();
  const queryParams = new URLSearchParams(location.search);
  const datasetId = queryParams.get("datasetId");
  const navigate = useNavigate();
  const { experimentId } = useParams();
  const [selectedExperiment, setSelectedExperiment] = useState(null);
  const [searchText, setSearchText] = useState("");
  const debouncedSearchText = useDebounce(searchText.trim(), 500);
  const { hasNextPage, isFetchingNextPage, fetchNextPage, data } =
    useInfiniteExperimentList(debouncedSearchText);
  const experimentList = useMemo(() => data?.allResults ?? [], [data]);
  const payload = JSON.parse(decodeURIComponent(queryParams.get("payload")));
  // eslint-disable-next-line react-hooks/exhaustive-deps
  const payloadExperiment = payload
    ? { label: payload.name, value: payload.id }
    : null;

  const experimentOptions = useMemo(
    () =>
      experimentList?.map(({ id, name }) => ({
        label: name,
        value: id,
      })),
    [experimentList],
  );

  useEffect(() => {
    // Convert IDs to string for safe comparison
    const selectedId = selectedExperiment?.value?.toString();
    const payloadId = payloadExperiment?.value?.toString();

    // 1️⃣ If selectedExperiment is null, initialize from list or payload
    if (!selectedExperiment) {
      // Try experimentList first
      if (experimentList?.length > 0) {
        const found = experimentList.find(
          (item) => String(item.id) === String(experimentId),
        );
        if (found) {
          setSelectedExperiment({ label: found.name, value: found.id });
          return;
        }
      }

      // Fallback to payload if not in list
      if (payloadExperiment) {
        setSelectedExperiment(payloadExperiment);
      }
      return;
    }

    // 2️⃣ If payloadExperiment is different from selectedExperiment, update it
    if (payloadExperiment && payloadId !== selectedId) {
      setSelectedExperiment(payloadExperiment);
    }
  }, [experimentList, experimentId, selectedExperiment, payloadExperiment]);

  const selectedOption = useMemo(() => {
    const fromList = experimentOptions?.find((o) => o.value === experimentId);
    return fromList || payloadExperiment;
  }, [experimentId, experimentOptions, payloadExperiment]);

  const renderValue = () => {
    return selectedExperiment?.label || "Select an experiment";
  };

  return (
    <>
      <DevelopSelect
        size="small"
        open={selectOpen}
        onOpen={() => setSelectOpen(true)}
        onClose={() => setSelectOpen(false)}
        ref={anchorRef}
        MenuProps={{
          PaperProps: {
            style: {
              display: "none",
            },
          },
        }}
        value={experimentId}
        renderValue={renderValue}
        sx={{ width: "max-content" }}
      >
        <MenuItem value={experimentId}>{selectedOption?.label}</MenuItem>
      </DevelopSelect>
      <Popover
        open={selectOpen}
        anchorEl={anchorRef.current}
        onClose={() => {
          setSelectOpen(false);
          setSearchText("");
        }}
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
            minWidth: anchorRef.current?.clientWidth,
          },
        }}
      >
        <Box>
          <TextField
            placeholder="Search Experiment"
            size="small"
            value={searchText}
            onChange={(e) => setSearchText(e.target.value)}
            InputProps={{
              startAdornment: (
                <InputAdornment position="start">
                  <Iconify icon="eva:search-fill" sx={{ color: "divider" }} />
                </InputAdornment>
              ),
            }}
            fullWidth
          />
          <Typography
            sx={{ paddingX: 1, paddingTop: 1 }}
            color="text.disabled"
            fontWeight={600}
            fontSize={12}
          >
            All Experiments
          </Typography>
          <ScrollingWrapper
            position={{ height: 220 }}
            width={anchorRef.current?.clientWidth - 26}
            scrollFunction={() => {
              if (hasNextPage && !isFetchingNextPage) {
                fetchNextPage();
              }
            }}
            dependancies={[isFetchingNextPage, hasNextPage, selectOpen]}
          >
            {experimentOptions?.map((option) => (
              <MenuItem
                key={option.value}
                value={option.value}
                onClick={() => {
                  const encoded = encodeURIComponent(
                    JSON.stringify({
                      id: option?.value,
                      name: option?.label,
                    }),
                  );
                  setSelectedExperiment(option);
                  navigate(
                    `/dashboard/develop/experiment/${option.value}/data?datasetId=${datasetId}&payload=${encoded}`,
                  );
                  setSelectOpen(false);
                  setSearchText("");
                }}
              >
                {option.label}
              </MenuItem>
            ))}
          </ScrollingWrapper>
        </Box>
      </Popover>
    </>
  );
};

export default DevelopExperimentSelect;
