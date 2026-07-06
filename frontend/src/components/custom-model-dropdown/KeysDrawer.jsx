import {
  Box,
  Button,
  Drawer,
  IconButton,
  InputAdornment,
  LinearProgress,
  TextField,
  Typography,
  useTheme,
} from "@mui/material";
import PropTypes from "prop-types";
import React, { useEffect, useMemo, useRef, useState } from "react";
import { useApiKeysStatus } from "src/api/model/api-keys";
import KeyCard from "src/sections/develop-detail/Common/ConfigureKeys/KeyCard";
import Iconify from "../iconify";
import { useDebounce } from "src/hooks/use-debounce";
import SvgColor from "../svg-color";
import axios, { endpoints } from "src/utils/axios";
import { useQuery } from "@tanstack/react-query";
import CloudProviderCard from "./CloudProviderCard";
import CustomModalKeyCard from "./CustomModalKeyCard";
import { ShowComponent } from "../show";
import {
  buttonStyles,
  createSortBySelectedProvider,
  filterAndSortProviders,
  getFilterOptions,
} from "./KeysHelper";
import { ConfirmDialog } from "../custom-dialog";
import { LoadingButton } from "@mui/lab";
import {
  useDeleteApiKey,
  DELETE_MODAL_TYPE,
} from "src/hooks/use-delete-api-key";

const KeyConfigDrawerChild = ({
  onClose,
  searchQuery,
  setSearchQuery,
  selectedModel,
  onAddCustomModel,
}) => {
  const { data, isFetching, isLoading } = useApiKeysStatus({
    enabled: true,
  });

  const prioritizedProvider = selectedModel?.providers?.toLowerCase();
  const theme = useTheme();
  const [selectedFilter, setSelectedFilter] = useState("all");
  const isMountedRef = useRef(true);

  const { data: customModalsData } = useQuery({
    queryKey: ["customModals"],
    queryFn: async () => {
      const { data } = await axios.get(
        endpoints.settings.customModal.getCustomModal,
      );
      return data;
    },
  });

  const {
    openDeleteModal,
    setOpenDeleteModal,
    handleDeleteApiKey,
    isDeleting,
  } = useDeleteApiKey();

  const searchDebounce = useDebounce(searchQuery.trim(), 300);

  const sortBySelectedProvider = useMemo(() => {
    return createSortBySelectedProvider(prioritizedProvider);
  }, [prioritizedProvider]);

  // === Group providers based on their type ===
  const defaultModelProviders = useMemo(() => {
    return filterAndSortProviders(
      data,
      "text",
      searchDebounce,
      sortBySelectedProvider,
    );
  }, [data, searchDebounce, sortBySelectedProvider]);

  const cloudProviders = useMemo(() => {
    return filterAndSortProviders(
      data,
      "json",
      searchDebounce,
      sortBySelectedProvider,
    );
  }, [data, searchDebounce, sortBySelectedProvider]);

  const filteredCustomModels = useMemo(() => {
    const search = searchDebounce.toLowerCase();
    return (customModalsData?.results || []).filter((model) =>
      model.userModelId?.toLowerCase().includes(search),
    );
  }, [searchDebounce, customModalsData]);

  const filterOptions = useMemo(
    () =>
      getFilterOptions({
        filteredCustomModels,
        defaultModelProviders,
        cloudProviders,
      }),
    [filteredCustomModels, defaultModelProviders, cloudProviders],
  );

  // Cleanup ref on unmount
  useEffect(() => {
    return () => {
      isMountedRef.current = false;
    };
  }, []);

  // Memoize the filter determination to prevent unnecessary recalculations
  const shouldUpdateFilter = useMemo(() => {
    if (!prioritizedProvider || !data) return null;

    const isInDefault = data?.some(
      (d) =>
        d.type === "text" && d.provider?.toLowerCase() === prioritizedProvider,
    );

    const isInCloud = data?.some(
      (d) =>
        d.type === "json" && d.provider?.toLowerCase() === prioritizedProvider,
    );

    if (isInDefault) return "default_model";
    if (isInCloud) return "cloud";
    return null;
  }, [prioritizedProvider, data]);

  useEffect(() => {
    // Only update state if component is still mounted and filter should change
    if (isMountedRef.current && shouldUpdateFilter) {
      setSelectedFilter(shouldUpdateFilter);
    }
  }, [shouldUpdateFilter]);

  return (
    <>
      <Box
        sx={{
          border: "1px solid",
          borderBottom: "0px",
          borderColor: "divider",
          borderRadius: "8px 8px 0px 0px",
          padding: 2,
          display: "grid",
          gridTemplateColumns: "1fr",
          gap: 2,
          overflow: "auto",
          zIndex: 9999,
          position: "relative",
        }}
      >
        <Box
          sx={{
            gap: 2,
            display: "flex",
            flexDirection: "column",
          }}
        >
          <Box sx={{ display: "flex", flexDirection: "column", gap: 0.5 }}>
            <Typography
              typography="m3"
              fontWeight={"fontWeightSemiBold"}
              color="text.primary"
            >
              Configure API keys
            </Typography>
            <Typography
              typography="s1"
              fontWeight={"fontWeightRegular"}
              color="text.secondary"
            >
              Configure your LLM API keys to run evals and prompts, All keys are
              encrypted and securely stored.
            </Typography>
          </Box>
          <IconButton
            onClick={onClose}
            sx={{
              borderRadius: "4px",
              position: "absolute",
              top: "16px",
              right: "16px",
            }}
          >
            <Iconify icon="mingcute:close-line" color="text.primary" />
          </IconButton>
          <Box sx={{ display: "flex", gap: 1, flexWrap: "wrap" }}>
            {filterOptions.map((button, index) => {
              const isSelected = selectedFilter === button.value;
              return (
                <Button
                  key={index}
                  size="small"
                  onClick={() => setSelectedFilter(button.value)}
                  sx={buttonStyles(isSelected, theme)}
                >
                  {`${button.title} (${button.count})`}
                </Button>
              );
            })}
          </Box>
          <Box sx={{ display: "flex", gap: 2 }}>
            <Box flexGrow={1}>
              <TextField
                fullWidth
                size="small"
                placeholder="Search API provider"
                value={searchQuery}
                onChange={(e) => setSearchQuery(e.target.value)}
                InputProps={{
                  startAdornment: (
                    <InputAdornment position="start">
                      <Iconify
                        icon="eva:search-fill"
                        sx={{ color: "text.disabled" }}
                      />
                    </InputAdornment>
                  ),
                  sx: {
                    "& input": {
                      height: "22px !important",
                      lineHeight: "normal !important",
                    },
                    "& input::placeholder": {
                      fontSize: "12px",
                    },
                  },
                }}
              />
            </Box>
            <Box sx={{ display: "flex", alignItems: "center" }}>
              <Button
                fullWidth
                onClick={onAddCustomModel}
                sx={{
                  minWidth: "90px",
                  border: "1px solid",
                  borderRadius: "8px",
                  borderColor: "text.disabled",
                  padding: "6px 16px",
                }}
                startIcon={
                  <SvgColor
                    sx={{
                      color: "text.primary",
                      height: 16,
                      width: 16,
                    }}
                    src={"/assets/icons/ic_add.svg"}
                  />
                }
              >
                <Typography
                  typography={"s2"}
                  color={"text.primary"}
                  fontWeight={"fontWeightMedium"}
                >
                  Create custom model
                </Typography>
              </Button>
            </Box>
          </Box>
        </Box>
        <ShowComponent condition={isLoading}>
          <LinearProgress />
        </ShowComponent>
        <ShowComponent condition={!isLoading}>
          <Box
            sx={{
              height: "100%",
              overflowY: "auto",
              gap: 2,
              display: "flex",
              flexDirection: "column",
              "&::-webkit-scrollbar": {
                width: "8px", // required
              },
              "&::-webkit-scrollbar-thumb": {
                backgroundColor: "rgba(0, 0, 0, 0.3)",
                borderRadius: "4px",
              },
              "&::-webkit-scrollbar-track": {
                backgroundColor: "transparent",
              },
            }}
          >
            <ShowComponent
              condition={
                selectedFilter === "all" || selectedFilter === "default_model"
              }
            >
              {defaultModelProviders.map((d) => (
                <KeyCard
                  key={d.provider}
                  data={d}
                  onClose={() => {}}
                  isFetching={isFetching}
                  onDeleteClick={() =>
                    setOpenDeleteModal({
                      id: d.id,
                      type: DELETE_MODAL_TYPE.NORMAL,
                    })
                  }
                />
              ))}
            </ShowComponent>
            <ShowComponent
              condition={
                selectedFilter === "all" || selectedFilter === "custom"
              }
            >
              {filteredCustomModels.map((model) => (
                <CustomModalKeyCard
                  key={model.id}
                  data={model}
                  onDeleteClick={() =>
                    setOpenDeleteModal({
                      id: model?.id,
                      type: DELETE_MODAL_TYPE.CUSTOM,
                    })
                  }
                />
              ))}
            </ShowComponent>

            <ShowComponent
              condition={selectedFilter === "all" || selectedFilter === "cloud"}
            >
              {cloudProviders.map((provider) =>
                provider.type === "json" ? (
                  <CloudProviderCard
                    key={provider.provider}
                    provider={provider}
                    showJsonField={provider?.type === "json"}
                    onDeleteClick={() =>
                      setOpenDeleteModal({
                        id: provider.id,
                        type: DELETE_MODAL_TYPE.NORMAL,
                      })
                    }
                  />
                ) : (
                  <KeyCard
                    key={provider.provider}
                    data={provider}
                    onClose={() => {}}
                    isFetching={isFetching}
                    onDeleteClick={() =>
                      setOpenDeleteModal({
                        id: provider.id,
                        type: DELETE_MODAL_TYPE.NORMAL,
                      })
                    }
                  />
                ),
              )}
            </ShowComponent>
            <ShowComponent
              condition={
                defaultModelProviders.length === 0 &&
                filteredCustomModels.length === 0 &&
                cloudProviders.length === 0
              }
            >
              <Typography sx={{ textAlign: "center", padding: 2 }}>
                No Providers found.
              </Typography>
            </ShowComponent>
          </Box>
        </ShowComponent>
      </Box>
      <ConfirmDialog
        content="Are you sure you want to delete this API key?"
        action={
          <LoadingButton
            loading={isDeleting}
            size="small"
            variant="contained"
            color="error"
            onClick={() => handleDeleteApiKey()}
            sx={{ color: "common.white" }}
            startIcon={
              <SvgColor
                // @ts-ignore
                sx={{ height: 2, width: 2, mt: -0.5 }}
                src={"/assets/icons/ic_delete.svg"}
              />
            }
          >
            Delete
          </LoadingButton>
        }
        open={!!openDeleteModal}
        onClose={() => setOpenDeleteModal(null)}
        title="Delete API key"
      />
    </>
  );
};

KeyConfigDrawerChild.propTypes = {
  onClose: PropTypes.func,
  searchQuery: PropTypes.string,
  setSearchQuery: PropTypes.func,
  selectedModel: PropTypes.object,
  onAddCustomModel: PropTypes.func,
};

const KeysDrawer = ({ open, onClose, selectedModel, onAddCustomModel }) => {
  const [searchQuery, setSearchQuery] = useState("");

  const closeModal = () => {
    onClose();
    setSearchQuery("");
  };

  return (
    <Drawer
      anchor="right"
      open={open}
      onClose={closeModal}
      variant="temporary"
      PaperProps={{
        sx: {
          height: "100vh",
          width: "589px",
          position: "fixed",
          // zIndex: 9999,
          borderRadius: "10px",
          backgroundColor: "background.paper",
          pointerEvents: "auto", // allow interaction inside drawer
        },
      }}
      ModalProps={{
        BackdropProps: {
          style: { backgroundColor: "transparent" },
        },
        hideBackdrop: true, // disables backdrop (which blocks clicks)
        disableEscapeKeyDown: true, // disables ESC key close
        sx: {
          pointerEvents: "none", // allow background interaction
        },
      }}
    >
      <KeyConfigDrawerChild
        onClose={closeModal}
        searchQuery={searchQuery}
        setSearchQuery={setSearchQuery}
        selectedModel={selectedModel}
        onAddCustomModel={onAddCustomModel}
      />
    </Drawer>
  );
};

export default KeysDrawer;

KeysDrawer.propTypes = {
  open: PropTypes.bool,
  onClose: PropTypes.func,
  selectedModel: PropTypes.object,
  onAddCustomModel: PropTypes.func,
};
