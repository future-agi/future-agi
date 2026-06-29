import {
  Box,
  Skeleton,
  Stack,
  Typography,
  Button,
  Chip,
  Menu,
  MenuItem,
  FormControlLabel,
  Checkbox,
  Radio,
  RadioGroup,
  useTheme,
} from "@mui/material";
import React, { useEffect, useMemo, useState } from "react";
import {
  FaceBadge,
  FaceInfo,
  FaceItem,
  FaceRow,
  FaceWrapper,
} from "./huggingFaceStyle";
import Divider from "@mui/material/Divider";
import Iconify from "src/components/iconify";
import { useLocation } from "react-router-dom";
import { useForm, useWatch } from "react-hook-form";
import { useMutation, useQuery } from "@tanstack/react-query";
import axios, { endpoints } from "src/utils/axios";
import { enqueueSnackbar } from "notistack";
import { useNavigate } from "react-router";
import { paths } from "src/routes/paths";
import { useDebounce } from "src/hooks/use-debounce";
import { zodResolver } from "@hookform/resolvers/zod";
import {
  HuggingFaceDatasetValidationSchema1,
  HuggingFaceDatasetValidationSchema2,
} from "../develop/AddDatasetDrawer/validation";
import { trackEvent, Events, PropertyName } from "src/utils/Mixpanel";
import {
  ComputerVisionTasks,
  AudioTasks,
  NLPTasks,
  SizeRanges,
  SortOptions,
  TabularTasks,
  ReinforcementLearningTasks,
  GraphMLTasks,
} from "src/utils/constant";
import HuggingFaceDetailDrawer from "./HuggingFaceDetailDrawer";
import CustomRowCountSlider from "src/components/custom-slider/CustomRowCountSlider";
import PaginationBox from "src/components/custom-pagination/PaginationBox";
import { SortIcon } from "src/components/custom-components/FilterChip";
import FormSearchField from "src/components/FormSearchField/FormSearchField";
import logger from "src/utils/logger";
import { getRequestErrorMessage } from "src/utils/errorUtils";

const getDefaultValue = () => {
  return {
    name: "",
    huggingface_dataset_config: "",
    huggingface_dataset_split: "",
    num_rows: 1,
  };
};

const HuggingFaceView = () => {
  const theme = useTheme();
  const [show, setShow] = useState(false);
  const [searchQuery, setSearchQuery] = useState("");
  const debouncedSearchQuery = useDebounce(searchQuery.trim(), 500);
  const [selectedId, setSelectedId] = useState(null);
  const location = useLocation();
  const [sortOption, setSortOption] = useState("Trending");
  const [anchorEl, setAnchorEl] = useState(null);

  const [, setSortSearchQuery] = useState("");

  // Filter states
  const [modalityFilters, setModalityFilters] = useState([]);
  const [formatFilters, setFormatFilters] = useState([]);
  const [rowCount, setRowCount] = useState([0, 1000000000]);
  const [tempRowCount, setTempRowCount] = useState([0, 1000000000]);
  const [mainFilter, setMainFilter] = useState("Main");
  const [selectedTask, setSelectedTask] = useState(null);
  const [, setTotalDatasets] = useState(0);

  // Add pagination state
  const [page, setPage] = useState(1); // Initialize page state here
  const itemsPerPage = 30; // Changed from 21 to 30

  const validationSchema = location.state
    ? HuggingFaceDatasetValidationSchema2
    : HuggingFaceDatasetValidationSchema1;

  const { control, handleSubmit, watch, reset } = useForm({
    defaultValues: getDefaultValue(),
    resolver: zodResolver(validationSchema),
  });
  const navigate = useNavigate();
  const datasetId = location?.state?.datasetId;
  const colors = [
    "primary.main",
    "#2F7CF7",
    "#B8AC47",
    "#DB2F2D",
    "#47B8AC",
    "#F77C2F",
    "#57FC78",
    "#AC47B8",
  ];

  // Filter options
  const modalityOptions = [
    "3D",
    "Timeseries",
    "Audio",
    "Geospatial",
    "Text",
    "Image",
    "Tabular",
    "Video",
  ];
  const formatOptions = [
    "CSV",
    "JSON",
    "Parquet",
    "Imagefolder",
    "Soundfolder",
    "Webdataset",
    "Arrow",
    "Text",
  ];
  const filterOptions = [
    "Main",
    "Tasks",
    //  "Libraries", "Languages", "Others"
  ];

  // Handle sort dropdown
  const handleSortClick = (event) => {
    setAnchorEl(event.currentTarget);
    setSortSearchQuery(""); // Reset search when opening dropdown
  };

  // Handle sort dropdown close
  const handleSortClose = (option) => {
    if (option !== undefined) {
      setSortOption(option);
    }
    setAnchorEl(null);
  };

  // Handle filter clicks
  const handleModalityFilter = (modality) => {
    if (modalityFilters.includes(modality)) {
      setModalityFilters(modalityFilters.filter((m) => m !== modality));
      setSelectedFilters(selectedFilters.filter((f) => f.label !== modality));
    } else {
      setModalityFilters([...modalityFilters, modality]);
      setSelectedFilters([
        ...selectedFilters,
        { label: modality, type: "modality" },
      ]);
    }
  };

  const handleFormatFilter = (format) => {
    if (formatFilters.includes(format)) {
      setFormatFilters(formatFilters.filter((f) => f !== format));
      setSelectedFilters(selectedFilters.filter((f) => f.label !== format));
    } else {
      setFormatFilters([format]); // Only one format can be selected
      setSelectedFilters([
        ...selectedFilters.filter((f) => !formatOptions.includes(f.label)),
        { label: format, type: "format" },
      ]);
    }
  };

  const hashStringToIndex = (str) => {
    let hash = 0;
    for (let i = 0; i < str.length; i++) {
      hash = (hash + str.charCodeAt(i) * i) % colors.length;
    }
    return hash;
  };

  const getColor = (val) => {
    if (!val) return "#DB2F2D";
    const index = hashStringToIndex(val);
    return colors[index];
  };

  // Update the getSizeCategory function
  const getSizeCategory = (rowCount) => {
    const [min, max] = rowCount;
    const categories = [];

    // Use the SizeRanges constant
    SizeRanges.forEach((range) => {
      if (min < range.max && max > range.min) {
        categories.push(range.label);
      }
    });

    return categories.length > 0 ? `or:(${categories.join(",")})` : "";
  };

  // Handle slider change (while dragging)
  const handleSliderChange = (event, newValue) => {
    setTempRowCount(newValue); // Update temporary value while dragging
  };

  // Handle slider commit (when dragging ends)
  const handleSliderChangeCommitted = (event, newValue) => {
    setRowCount(newValue); // Update the actual value after dragging
    setTempRowCount(newValue); // Sync temporary value
  };

  // Move taskFilters declaration before the useQuery hook
  const [taskFilters, setTaskFilters] = useState([]);

  const {
    data: huggingFaceList,
    isPending: isLoadingHuggingFaceList,
    isFetching,
  } = useQuery({
    queryKey: [
      "hugging-face-list",
      debouncedSearchQuery,
      modalityFilters,
      formatFilters,
      taskFilters,
      page,
      sortOption,
      rowCount,
    ],
    queryFn: () => {
      return axios.post(
        endpoints.huggingFace.list,
        {
          search_query: debouncedSearchQuery,
          filter_params: {
            modalities: modalityFilters.map((modality) =>
              modality.toLowerCase(),
            ),
            page_number: page - 1,
            page_size: itemsPerPage,
            sort: sortOption.toLowerCase().replace(" ", "_"),
            task_categories: taskFilters[0]
              ? taskFilters[0].toLowerCase().replace(/\s+/g, "-") ==
                "graph-machine-learning"
                ? "graph-ml"
                : taskFilters[0].toLowerCase().replace(/\s+/g, "-")
              : "",
            size_categories: getSizeCategory(rowCount),
            format: formatFilters.map((format) =>
              format.toLowerCase() === "soundfolder"
                ? "audiofolder"
                : format.toLowerCase(),
            ),
          },
        },
        {
          headers: { "Content-Type": "application/json" },
        },
      );
    },
    select: (data) => {
      if (huggingFaceList?.data?.result?.totalDatasets !== undefined) {
        setTotalDatasets(huggingFaceList?.data?.result?.totalDatasets);
      }
      return data;
    },
  });

  // Modify the filtered datasets to include pagination
  const paginatedDatasets = useMemo(() => {
    return huggingFaceList?.data?.result?.datasets || [];
  }, [huggingFaceList]);

  const MAX_PAGES = 100; // Define maximum number of pages

  const totalPages = useMemo(() => {
    const total = huggingFaceList?.data?.result?.totalDatasets || 0;
    const calculatedPages = Math.ceil(total / itemsPerPage);
    // Limit to maximum 100 pages
    return Math.min(MAX_PAGES, Math.max(1, calculatedPages));
  }, [huggingFaceList?.data?.result?.totalDatasets, itemsPerPage]);

  const {
    mutate: createHuggingFaceDataset,
    isPending: isLoadingCreateDataset,
  } = useMutation({
    mutationFn: (d) =>
      axios.post(endpoints.develop.createHuggingFaceDataset, d, {
        headers: { "Content-Type": "multipart/form-data" },
      }),
    onSuccess: (data) => {
      enqueueSnackbar("Dataset created successfully", {
        variant: "success",
      });
      setShow(false);
      navigate(`/dashboard/develop/${data?.data?.result?.datasetId}`);
    },
    onError: (error) => {
      enqueueSnackbar(
        getRequestErrorMessage(error, "Failed to create dataset", {
          retryAction: "creating this dataset from Hugging Face",
        }),
        { variant: "error" },
      );
    },
  });

  const { mutate: createHuggingRow, isPending } = useMutation({
    mutationFn: (data) =>
      axios.post(endpoints.huggingFace.addHuggingFaceRow(datasetId), data, {
        headers: { "Content-Type": "application/json" },
      }),
    onSuccess: () => {
      enqueueSnackbar("Dataset created successfully", {
        variant: "success",
      });
      navigate(`/dashboard/develop/${datasetId}?tab=data`);
    },
    onError: (error) => {
      enqueueSnackbar(
        getRequestErrorMessage(error, "Failed to add rows from Hugging Face", {
          retryAction: "adding rows from Hugging Face",
        }),
        { variant: "error" },
      );
    },
  });

  const { data: huggingFaceDetail } = useQuery({
    queryKey: ["huggingFaceDetail", selectedId],
    queryFn: () =>
      axios.post(
        endpoints.huggingFace.detail,
        { dataset_id: selectedId },
        {
          headers: { "Content-Type": "multipart/form-data" },
        },
      ),
    enabled: !!selectedId,
  });

  const {
    data: loadedDataset,
    isError: isErrorFetchingHuggingFaceDatasetConfig,
    error: huggingFaceDatasetConfigError,
  } = useQuery({
    queryKey: ["loadedDataset", selectedId],
    queryFn: () =>
      axios.post(
        endpoints.develop.getHuggingFaceDataset,
        { dataset_path: selectedId },
        {
          headers: { "Content-Type": "multipart/form-data" },
        },
      ),
    enabled: !!selectedId,
    meta: {
      errorHandled: true,
    },
    retry: (failureCount, error) => {
      // don't retry if response is false
      if (!error?.status) {
        return false;
      }
      return failureCount < 3; // Default retry
    },
  });

  const subset = useWatch({ control, name: "huggingface_dataset_config" });

  const { subsetOptions, splitOptions } = useMemo(() => {
    let subsetOptions = [];
    let splitOptions = [];

    const datasetInfo = loadedDataset?.data?.result?.dataset_info?.splits;

    if (datasetInfo) {
      subsetOptions = Object.keys(datasetInfo).map((subset) => ({
        label: subset,
        value: subset,
      }));
    }

    if (subset && datasetInfo && datasetInfo[subset]) {
      splitOptions = datasetInfo[subset].map((split) => ({
        label: split,
        value: split,
      }));
    }

    return { subsetOptions, splitOptions };
  }, [loadedDataset, subset]);

  const onDataSetClick = (id) => {
    setSelectedId(id);
    setShow(true);
  };

  const onSubmit = handleSubmit(async (data) => {
    try {
      trackEvent(Events.datasetFromHuggingFaceSuccessful, {
        [PropertyName.name]: data?.name,
        [PropertyName.subset]: data?.huggingface_dataset_config,
        [PropertyName.split]: data?.huggingface_dataset_split,
        [PropertyName.addSelectedRows]: data?.num_rows,
      });

      if (location.state) {
        createHuggingRow({
          ...data,
          huggingface_dataset_name:
            huggingFaceDetail?.data?.result?.dataset?.id,
        });
      } else {
        createHuggingFaceDataset({
          ...data,
          num_rows: data?.num_rows,
          huggingface_dataset_name:
            huggingFaceDetail?.data?.result?.dataset?.id,
          model_type: "GenerativeLLM",
        });
      }
    } catch (error) {
      logger.error("Error in onSubmit:", error);
      enqueueSnackbar("Failed to submit dataset request", { variant: "error" });
    }
  });

  const onClose = () => {
    setShow(false);
    reset(getDefaultValue());
  };

  const handlePageChange = (newPage) => {
    setPage(newPage);
  };

  const [selectedFilters, setSelectedFilters] = useState([]);

  const handleTaskFilter = (task) => {
    if (taskFilters[0] === task) {
      setTaskFilters([]); // Clear if same task is clicked
      setSelectedFilters(selectedFilters.filter((f) => f.label !== task));
    } else {
      setTaskFilters([task]); // Only allow one task selection
      // Remove any existing task filter and add new one
      setSelectedFilters([
        ...selectedFilters.filter((f) => !taskFilters.includes(f.label)),
        { label: task, type: "task" },
      ]);
    }
  };

  const handleFilterChipDelete = (filter) => {
    setSelectedFilters(selectedFilters.filter((f) => f.label !== filter.label));

    if (filter.type === "modality") {
      setModalityFilters(modalityFilters.filter((m) => m !== filter.label)); // Remove from modality filters
    } else if (filter.type === "format") {
      setFormatFilters(formatFilters.filter((f) => f !== filter.label)); // Remove from format filters
    } else if (taskFilters.includes(filter.label)) {
      setTaskFilters(taskFilters.filter((t) => t !== filter.label)); // Remove from task filters
    }
  };

  const [taskSearchQuery, setTaskSearchQuery] = useState("");

  const renderFilters = () => {
    if (mainFilter === "Tasks") {
      return (
        <Box sx={{ mt: 2, maxHeight: "650px", overflowY: "auto" }}>
          <Divider sx={{ width: "100%", mb: 2 }} />
          <FormSearchField
            size="small"
            fullWidth
            placeholder="Search"
            disableUnderline
            searchQuery={taskSearchQuery}
            onChange={(e) => setTaskSearchQuery(e.target.value)}
            sx={{
              flex: 1,
              marginBottom: (theme) => theme.spacing(2.5),
              borderRadius: "1px",
              color: "text.primary",
            }}
          />

          {/* Computer Vision */}
          <Typography
            fontSize="14px"
            fontWeight="500"
            color="text.primary"
            mb={2}
          >
            Computer Vision
          </Typography>
          {ComputerVisionTasks.filter((task) =>
            task.toLowerCase().includes(taskSearchQuery.toLowerCase()),
          )
            .sort((a, b) => sortTasks(a, b, taskSearchQuery))
            .map((task) => (
              <FormControlLabel
                key={task}
                control={
                  <Radio
                    checked={taskFilters[0] === task}
                    onChange={() => handleTaskFilter(task)}
                  />
                }
                label={task}
                sx={{
                  pl: 1,
                  "& .MuiFormControlLabel-label": {
                    color:
                      theme.palette.mode === "light"
                        ? "text.primary"
                        : "common.white",
                  },
                }}
              />
            ))}

          {/* Natural Language Processing */}
          <Typography
            fontSize="14px"
            fontWeight="500"
            color="text.primary"
            mb={2}
            mt={2}
          >
            Natural Language Processing
          </Typography>
          {NLPTasks.filter((task) =>
            task.toLowerCase().includes(taskSearchQuery.toLowerCase()),
          )
            .sort((a, b) => sortTasks(a, b, taskSearchQuery))
            .map((task) => (
              <FormControlLabel
                key={task}
                control={
                  <Radio
                    checked={taskFilters[0] === task}
                    onChange={() => handleTaskFilter(task)}
                  />
                }
                label={task}
                sx={{
                  pl: 1,
                  "& .MuiFormControlLabel-label": {
                    color:
                      theme.palette.mode === "light"
                        ? "text.primary"
                        : "common.white",
                  },
                }}
              />
            ))}

          {/* Audio */}
          <Typography
            fontSize="14px"
            fontWeight="500"
            color="text.primary"
            mb={2}
            mt={2}
          >
            Audio
          </Typography>
          {AudioTasks.filter((task) =>
            task.toLowerCase().includes(taskSearchQuery.toLowerCase()),
          )
            .sort((a, b) => sortTasks(a, b, taskSearchQuery))
            .map((task) => (
              <FormControlLabel
                key={task}
                control={
                  <Radio
                    checked={taskFilters[0] === task}
                    onChange={() => handleTaskFilter(task)}
                  />
                }
                label={task}
                sx={{
                  pl: 1,
                  "& .MuiFormControlLabel-label": {
                    color:
                      theme.palette.mode === "light"
                        ? "text.primary"
                        : "common.white",
                  },
                }}
              />
            ))}

          {/* Tabular */}
          <Typography
            fontSize="14px"
            fontWeight="500"
            color="text.primary"
            mb={2}
            mt={2}
          >
            Tabular
          </Typography>
          {TabularTasks.filter((task) =>
            task.toLowerCase().includes(taskSearchQuery.toLowerCase()),
          )
            .sort((a, b) => sortTasks(a, b, taskSearchQuery))
            .map((task) => (
              <FormControlLabel
                key={task}
                control={
                  <Radio
                    checked={taskFilters[0] === task}
                    onChange={() => handleTaskFilter(task)}
                  />
                }
                label={task}
                sx={{
                  pl: 1,
                  "& .MuiFormControlLabel-label": {
                    color:
                      theme.palette.mode === "light"
                        ? "text.primary"
                        : "common.white",
                  },
                }}
              />
            ))}

          {/* Reinforcement Learning */}
          <Typography
            fontSize="14px"
            fontWeight="500"
            color="text.primary"
            mb={2}
            mt={2}
          >
            Reinforcement Learning
          </Typography>
          {ReinforcementLearningTasks.filter((task) =>
            task.toLowerCase().includes(taskSearchQuery.toLowerCase()),
          )
            .sort((a, b) => sortTasks(a, b, taskSearchQuery))
            .map((task) => (
              <FormControlLabel
                key={task}
                control={
                  <Radio
                    checked={taskFilters[0] === task}
                    onChange={() => handleTaskFilter(task)}
                  />
                }
                label={task}
                sx={{
                  pl: 1,
                  "& .MuiFormControlLabel-label": {
                    color:
                      theme.palette.mode === "light"
                        ? "text.primary"
                        : "common.white",
                  },
                }}
              />
            ))}

          {/* Others */}
          <Typography
            fontSize="14px"
            fontWeight="500"
            color="text.primary"
            mb={2}
            mt={2}
          >
            Others
          </Typography>
          {GraphMLTasks.filter((task) =>
            task.toLowerCase().includes(taskSearchQuery.toLowerCase()),
          )
            .sort((a, b) => sortTasks(a, b, taskSearchQuery))
            .map((task) => (
              <FormControlLabel
                key={task}
                control={
                  <Radio
                    checked={taskFilters[0] === task}
                    onChange={() => handleTaskFilter(task)}
                  />
                }
                label={task}
                sx={{
                  pl: 1,
                  "& .MuiFormControlLabel-label": {
                    color:
                      theme.palette.mode === "light"
                        ? "text.primary"
                        : "common.white",
                  },
                }}
              />
            ))}
        </Box>
      );
    } else {
      return (
        <>
          <Typography
            fontSize="14px"
            fontWeight="500"
            color="text.primary"
            mb={2}
            mt={2}
          >
            Modalities
          </Typography>
          <Box>
            {modalityOptions.map((modality) => (
              <FormControlLabel
                key={modality}
                control={
                  <Checkbox
                    checked={modalityFilters.includes(modality)}
                    onChange={() => handleModalityFilter(modality)}
                  />
                }
                label={modality}
                sx={{
                  "& .MuiFormControlLabel-label": {
                    color:
                      theme.palette.mode === "light"
                        ? "text.primary"
                        : "common.white",
                  },
                }}
              />
            ))}
          </Box>

          <Box sx={{}}>
            <Typography
              fontSize="14px"
              fontWeight="500"
              color="text.primary"
              mb={1}
              mt={2}
            >
              No. of rows
            </Typography>
            <Box sx={{ px: 1, py: 4 }}>
              <Box
                sx={{
                  display: "flex",
                  justifyContent: "space-between",
                  mb: 1,
                }}
              ></Box>
              <CustomRowCountSlider
                value={tempRowCount}
                onChange={handleSliderChange}
                onChangeCommitted={handleSliderChangeCommitted}
              />
            </Box>
          </Box>

          <Typography
            fontSize="14px"
            fontWeight="500"
            color="text.primary"
            mb={2}
          >
            Format
          </Typography>
          <Box>
            {formatOptions.map((format) => (
              <FormControlLabel
                key={format}
                control={
                  <Radio
                    checked={formatFilters.includes(format)}
                    onClick={() => handleFormatFilter(format)}
                  />
                }
                label={format}
                sx={{
                  "& .MuiFormControlLabel-label": {
                    color:
                      theme.palette.mode === "light"
                        ? "text.primary"
                        : "common.white",
                  },
                }}
              />
            ))}
          </Box>
        </>
      );
    }
  };

  // Update this function to manage selected filters
  const handleMainFilterChange = (filter) => {
    setMainFilter(filter);
    setSelectedTask(null);
    // Remove the filter from selectedFilters if it's a main filter option
    setSelectedFilters((prev) =>
      prev.filter((f) => !filterOptions.includes(f)),
    );
  };

  // Add this helper function outside the component
  const sortTasks = (a, b, searchQuery) => {
    const aExactMatch = a.toLowerCase() === searchQuery.toLowerCase();
    const bExactMatch = b.toLowerCase() === searchQuery.toLowerCase();

    if (aExactMatch && !bExactMatch) return -1;
    if (!aExactMatch && bExactMatch) return 1;

    const aIndex = a.toLowerCase().indexOf(searchQuery.toLowerCase());
    const bIndex = b.toLowerCase().indexOf(searchQuery.toLowerCase());

    if (aIndex < bIndex) return -1;
    if (aIndex > bIndex) return 1;

    return a.localeCompare(b);
  };

  // Reset pagination when filters change
  useEffect(() => {
    setPage(1); // Reset to the first page
  }, [modalityFilters, formatFilters, rowCount, sortOption, selectedTask]); // Add any other filter states you want to monitor

  // handle other snackbar errors of dataset config
  useEffect(() => {
    if (isErrorFetchingHuggingFaceDatasetConfig) {
      const message = getRequestErrorMessage(
        huggingFaceDatasetConfigError,
        "An unexpected error occurred.",
        { retryAction: "loading this Hugging Face dataset" },
      );

      if (message.includes("Dataset unavailable")) {
        return;
      } else {
        enqueueSnackbar(message, {
          variant: "error",
        });
      }
    }
  }, [isErrorFetchingHuggingFaceDatasetConfig, huggingFaceDatasetConfigError]);

  return (
    <Box sx={{ display: "flex" }}>
      {/* Main content */}
      <FaceWrapper sx={{ flex: 1, overflowY: "auto", height: "703px" }}>
        {/* Regular header content that scrolls */}
        <Box sx={{ textAlign: "center", position: "relative" }}>
          <Button
            size="small"
            startIcon={
              <Iconify
                icon="octicon:chevron-left-24"
                width="20px"
                sx={{ color: "text.primary" }}
              />
            }
            onClick={() => navigate(paths.dashboard.develop)}
            sx={{
              border: "1px solid",
              borderColor: "action.hover",
              color: "text.primary",
              display: "flex",
              alignItems: "center",
              flexDirection: "row",
              borderRadius: "4px",
              "& .MuiButton-startIcon": {
                marginRight: "4px",
              },
            }}
          >
            Back
          </Button>
          <img
            src="/favicon/apple-touch-icon.png"
            style={{ width: "51px", marginBottom: "27px" }}
          />
          <Typography
            fontSize="20px"
            fontWeight="600"
            color="text.primary"
            mb={2}
          >
            Experiment with Hugging Face Dataset 🤗
          </Typography>
          <Typography
            fontSize="14px"
            fontWeight="400"
            color="text.secondary"
            mb={3}
          >
            Choose a HuggingFace dataset, and start experimenting in Future AGI
          </Typography>
        </Box>

        {/* Sticky search field */}
        <Box
          sx={{
            position: "sticky",
            top: -28,
            backgroundColor: "background.paper",
            zIndex: 1000,
            py: 2,
            borderBottom: "1px solid",
            borderColor: "divider",
          }}
        >
          <Box
            sx={{
              display: "flex",
              alignItems: "center",
              height: "35px",
              maxWidth: "617px",
              marginX: "auto",
              marginY: (theme) => theme.spacing(2),
            }}
          >
            <FormSearchField
              fullWidth
              placeholder="Search"
              disableUnderline
              searchQuery={searchQuery}
              onChange={(e) => {
                setSearchQuery(e.target.value);
              }}
              sx={{
                flex: 1,
                color: "text.primary",
              }}
            />
          </Box>
        </Box>

        <Box sx={{ display: "flex", justifyContent: "space-between" }}>
          {/* Left sidebar with filters - always visible */}
          <Box
            sx={{
              width: "300px",
              borderRight: "1px solid var(--border-default)",
              p: 2,
              height: "60rem",
              flexShrink: 0,
            }}
          >
            {/* Main Filters */}
            <RadioGroup
              value={mainFilter}
              onChange={(e) => {
                handleMainFilterChange(e.target.value);
              }}
              sx={{
                "& .MuiFormControlLabel-label": {
                  color: "text.primary",
                },
              }}
            >
              {filterOptions.map((option) => (
                <FormControlLabel
                  key={option}
                  control={<Radio />}
                  label={option}
                  value={option}
                  onChange={() => {
                    if (option === "Tasks") {
                      setSelectedTask("Tasks");
                    }
                  }}
                />
              ))}
            </RadioGroup>

            {/* Render filters based on selected main filter */}
            {renderFilters()}
          </Box>

          {/* Dataset section */}
          <Box
            sx={{
              marginLeft: "20px",
              marginTop: "20px",
              display: "flex",
              flexDirection: "column",
              flex: 1,
            }}
          >
            {/* Flex container for dataset count and sort controls */}
            <Box
              sx={{
                display: "flex",
                justifyContent: "space-between",
                alignItems: "center",
              }}
            >
              <Typography fontSize="12px" color="text.secondary">
                <b style={{ fontSize: "14px" }}>Total Datasets : </b>
                {huggingFaceList?.data?.result?.totalDatasets || 0}
              </Typography>

              {/* Sort controls */}
              <Box
                onClick={handleSortClick}
                sx={{
                  display: "flex",
                  alignItems: "center",
                  cursor: "pointer",
                  py: 1,
                  px: 1.5,
                  color: "text.primary",
                }}
              >
                <SortIcon />
                <Typography fontSize="14px" color="text.primary" ml={1}>
                  Sort: {sortOption}
                </Typography>
              </Box>
            </Box>

            {/* Sort dropdown menu */}
            <Menu
              anchorEl={anchorEl}
              open={Boolean(anchorEl)}
              onClose={() => handleSortClose()}
              PaperProps={{
                sx: {
                  width: 150,
                  maxHeight: 300,
                },
              }}
            >
              {SortOptions.map((option) => (
                <MenuItem
                  key={option}
                  onClick={() => handleSortClose(option)}
                  selected={option === sortOption}
                  sx={{
                    minWidth: 200,
                    "&.Mui-selected": {
                      backgroundColor: "primary.lighter",
                      color: "primary.main",
                      "&:hover": {
                        backgroundColor: "primary.lighter",
                      },
                    },
                  }}
                >
                  {option}
                </MenuItem>
              ))}
            </Menu>

            {/* Render selected filters as chips below the dataset count */}
            <Box sx={{ display: "flex", flexWrap: "wrap", gap: 1, mb: 2 }}>
              {selectedFilters.map((filter) => (
                <Chip
                  key={filter.label}
                  label={filter.label}
                  onDelete={() => handleFilterChipDelete(filter)}
                  deleteIcon={
                    <Iconify icon="eva:close-fill" width={16} height={16} />
                  }
                  sx={{
                    backgroundColor:
                      theme.palette.mode === "light"
                        ? "primary.main"
                        : "primary.light",
                    color:
                      theme.palette.mode === "light"
                        ? "common.white"
                        : "primary.dark",
                    borderRadius: "4px",
                    "&:hover": {
                      backgroundColor:
                        theme.palette.mode === "light"
                          ? theme.palette.primary.dark
                          : theme.palette.primary.light,
                      opacity: theme.palette.mode === "light" ? 1 : 0.8,
                      cursor: "default",
                    },
                  }}
                />
              ))}
            </Box>

            {isLoadingHuggingFaceList || isFetching ? (
              <Box sx={{ display: "flex", flexWrap: "wrap", gap: 2 }}>
                {Array.from({ length: 21 }).map((_, idx) => (
                  <Box
                    key={idx}
                    sx={{
                      flex: "0 0 calc(33.33% - 16px)",
                      boxSizing: "border-box",
                    }}
                  >
                    <Box>
                      <Skeleton
                        variant="text"
                        width="60%"
                        height={20}
                        sx={{ mb: 1 }}
                      />
                      <Stack
                        direction="row"
                        justifyContent="space-between"
                        gap="10px"
                      >
                        <Skeleton
                          variant="rectangular"
                          width={50}
                          height={20}
                          sx={{ borderRadius: "4px" }}
                        />
                        <Stack
                          direction="row"
                          gap="10px"
                          justifyContent="space-between"
                          ml="auto"
                        >
                          <Skeleton variant="text" width={40} height={20} />
                          <Skeleton variant="text" width={40} height={20} />
                        </Stack>
                      </Stack>
                    </Box>
                  </Box>
                ))}
              </Box>
            ) : (
              <>
                <FaceRow sx={{ display: "flex", flexWrap: "wrap", gap: 2 }}>
                  {paginatedDatasets.map((item, idx) => (
                    <Box
                      key={idx}
                      sx={{
                        flex: "0 0 calc(33.33% - 16px)",
                        boxSizing: "border-box",
                      }}
                    >
                      <FaceItem onClick={() => onDataSetClick(item?.id)}>
                        <Typography
                          flex={1}
                          fontSize="12px"
                          color="text.primary"
                          fontWeight="500"
                          mb={1}
                          sx={{
                            wordBreak: "break-all",
                          }}
                        >
                          {item?.id}
                        </Typography>
                        <Stack direction="row" gap="10px">
                          {item?.author && (
                            <FaceBadge
                              color={getColor(item?.author)}
                              bgcolor={getColor(item?.author) + "16"}
                            >
                              {item?.author}
                            </FaceBadge>
                          )}
                          <Stack
                            direction="row"
                            gap="10px"
                            justifyContent="space-between"
                            ml="auto"
                          >
                            <FaceInfo>
                              <Iconify icon="material-symbols:download-sharp" />{" "}
                              {item?.downloads ? item?.downloads : 0}
                            </FaceInfo>
                            <FaceInfo>
                              <Iconify icon="weui:like-outlined" />{" "}
                              {item?.likes ? item?.likes : 0}
                            </FaceInfo>
                          </Stack>
                        </Stack>
                      </FaceItem>
                    </Box>
                  ))}
                </FaceRow>
              </>
            )}
          </Box>
        </Box>
        <Divider sx={{ width: "100%" }} />

        {/* Replace the existing pagination with the new component */}
        <Box sx={{ ml: "315px" }}>
          <PaginationBox
            page={page}
            totalPages={isLoadingHuggingFaceList ? 1 : totalPages}
            onPageChange={handlePageChange}
            totalItems={huggingFaceList?.data?.result?.totalDatasets || 0}
            itemsPerPage={itemsPerPage}
            isLoading={isLoadingHuggingFaceList}
          />
        </Box>

        <HuggingFaceDetailDrawer
          show={show}
          setShow={setShow}
          reset={reset}
          control={control}
          huggingFaceDetail={huggingFaceDetail?.data?.result?.dataset}
          watch={watch}
          subsetOptions={subsetOptions}
          splitOptions={splitOptions}
          onSubmit={onSubmit}
          onClose={onClose}
          isLoadingCreateDataset={isLoadingCreateDataset || isPending}
          showNameField={!location.state}
          huggingFaceDatasetConfigError={huggingFaceDatasetConfigError?.result}
        />
      </FaceWrapper>
    </Box>
  );
};

export default HuggingFaceView;
