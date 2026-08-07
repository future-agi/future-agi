import { useMemo, useState } from "react";
import { Helmet } from "react-helmet-async";
import { Events, trackEvent } from "src/utils/Mixpanel";

import { Box, Button, Typography, useTheme } from "@mui/material";
import React from "react";
import KeyCard from "src/sections/develop-detail/Common/ConfigureKeys/KeyCard";
import { useQuery } from "@tanstack/react-query";
import axios, { endpoints } from "src/utils/axios";
import { ShowComponent } from "src/components/show";
import FormSearchField from "src/components/FormSearchField/FormSearchField";
import { useDebounce } from "src/hooks/use-debounce";
import CustomModalKeyCard from "src/components/custom-model-dropdown/CustomModalKeyCard";
import CloudProviderCard from "src/components/custom-model-dropdown/CloudProviderCard";
import AddCustomModal from "./AddCustomModal";
import SvgColor from "src/components/svg-color";
import {
  buttonStyles,
  getFilterOptions,
  filterAndSortProviders,
  normalizeProviderStatus,
  emptyStateContent,
} from "src/components/custom-model-dropdown/KeysHelper";
import EmptyLayout from "src/components/EmptyLayout/EmptyLayout";
import { ConfirmDialog } from "../../../components/custom-dialog";
import { LoadingButton } from "@mui/lab";
import {
  useDeleteApiKey,
  DELETE_MODAL_TYPE,
} from "src/hooks/use-delete-api-key";
import { useAuthContext } from "src/auth/hooks";
import { PERMISSIONS, RolePermission } from "src/utils/rolePermissionMapping";

const normalizeCustomModel = (model = {}) => {
  const userModelId =
    model.userModelId ?? model.user_model_id ?? model.modelName ?? "";
  const modelName = model.modelName ?? model.model_name ?? userModelId;
  const configJson = model.configJson ?? model.config_json ?? null;
  const modelProvider =
    model.modelProvider ?? model.model_provider ?? model.provider ?? "";
  const inputTokenCost = model.inputTokenCost ?? model.input_token_cost ?? "";
  const outputTokenCost =
    model.outputTokenCost ?? model.output_token_cost ?? "";

  return {
    ...model,
    userModelId,
    user_model_id: model.user_model_id ?? userModelId,
    modelName,
    model_name: model.model_name ?? modelName,
    configJson,
    config_json: model.config_json ?? configJson,
    modelProvider,
    model_provider: model.model_provider ?? modelProvider,
    inputTokenCost,
    input_token_cost: model.input_token_cost ?? inputTokenCost,
    outputTokenCost,
    output_token_cost: model.output_token_cost ?? outputTokenCost,
  };
};

const ConfigureProviders = () => {
  const { role } = useAuthContext();
  const theme = useTheme();
  const [showCreateModal, setShowCreateModal] = useState(false);
  const [searchQuery, setSearchQuery] = useState("");
  const { data, isLoading, isFetching } = useQuery({
    queryKey: ["api-key-status"],
    queryFn: () => axios.get(endpoints.develop.apiKey.status),
    select: (d) =>
      (d.data?.result?.providers || []).map(normalizeProviderStatus),
  });

  const [selectedFilter, setSelectedFilter] = useState("all");
  const searchDebounce = useDebounce(searchQuery.trim(), 300);
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

  const handleCreate = () => {
    trackEvent(Events.addCustomModelClicked);
    setShowCreateModal(true);
  };

  const defaultModelProviders = useMemo(() => {
    return filterAndSortProviders(data, "text", searchDebounce);
  }, [data, searchDebounce]);

  const cloudProviders = useMemo(() => {
    return filterAndSortProviders(data, "json", searchDebounce);
  }, [data, searchDebounce]);

  const filteredCustomModels = useMemo(() => {
    const search = searchDebounce.toLowerCase();
    return (customModalsData?.results || [])
      .map(normalizeCustomModel)
      .filter((model) => model.userModelId?.toLowerCase().includes(search));
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

  const filteredData = data?.filter((d) =>
    (d.display_name ?? "").toLowerCase().includes(searchQuery.toLowerCase()),
  );

  const containerStyles = useMemo(
    () => ({
      display: "flex",
      flexDirection: "column",
      height: "89vh",
      backgroundColor: "background.paper",
      overflow: "hidden",
    }),
    [],
  );

  const outerBoxStyles = useMemo(
    () => ({
      // padding: theme.spacing(1),
      marginBottom: theme.spacing(3),
      display: "flex",
      flexDirection: "column",
      flexGrow: 1,
      height: "100%",
    }),
    [theme],
  );

  const headingSectionStyles = useMemo(
    () => ({
      display: "flex",
      flexDirection: "column",
    }),
    [],
  );

  const filtersRowStyles = useMemo(
    () => ({
      display: "flex",
      gap: theme.spacing(1),
      flexWrap: "wrap",
      marginBottom: theme.spacing(2),
    }),
    [theme],
  );

  const searchRowStyles = useMemo(
    () => ({
      display: "flex",
      gap: theme.spacing(2),
      alignItems: "center",
      flexWrap: "wrap",
    }),
    [theme],
  );

  const keyCardsGridStyles = useMemo(
    () => ({
      display: "grid",
      gridTemplateColumns: "repeat(3, minmax(250px, 1fr))",
      gap:
        filteredData?.length === 1
          ? theme.spacing(6)
          : `${theme.spacing(2)} ${theme.spacing(2)}`,
      alignContent: "start",
      placeContent: "start",
      flexGrow: 1,
      overflowY: "auto",
      minHeight: 0,
      marginTop: theme.spacing(2),

      // Custom scrollbar styles
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
    }),
    [theme, filteredData],
  );

  const addButtonStyles = useMemo(
    () => ({
      minWidth: theme.spacing(11.25),
      border: "1px solid",
      borderRadius: theme.spacing(1),
      borderColor: "text.disabled",
      padding: theme.spacing(0.75, 2),
    }),
    [theme],
  );

  const searchPlaceholder = useMemo(() => {
    if (selectedFilter === "all") {
      return "Search AI Provider";
    } else if (selectedFilter === "default_model") {
      return "Search Default Model Provider";
    } else if (selectedFilter === "cloud") {
      return "Search Default Cloud Provider";
    } else if (selectedFilter === "custom") {
      return "Search Custom Model";
    }
  }, [selectedFilter]);

  const isEmpty =
    (selectedFilter === "all" &&
      defaultModelProviders.length === 0 &&
      cloudProviders.length === 0 &&
      filteredCustomModels.length === 0) ||
    (selectedFilter === "default_model" &&
      defaultModelProviders.length === 0) ||
    (selectedFilter === "cloud" && cloudProviders.length === 0) ||
    (selectedFilter === "custom" && filteredCustomModels.length === 0);

  return (
    <Box sx={containerStyles}>
      <ShowComponent condition={!isLoading}>
        <Box sx={outerBoxStyles}>
          <Box sx={headingSectionStyles}>
            <Box sx={filtersRowStyles}>
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

            {/* Search and Add Button Row */}
            <Box sx={searchRowStyles}>
              <Box flexGrow={1}>
                <FormSearchField
                  fullWidth
                  size="small"
                  placeholder={searchPlaceholder}
                  searchQuery={searchQuery}
                  onChange={(e) => setSearchQuery(e.target.value)}
                  InputProps={null}
                />
              </Box>

              <Button
                sx={addButtonStyles}
                onClick={handleCreate}
                disabled={!RolePermission.API_KEYS[PERMISSIONS.CREATE][role]}
                startIcon={
                  <SvgColor
                    sx={{ color: "text.primary", height: 16, width: 16 }}
                    src={"/assets/icons/ic_add.svg"}
                  />
                }
              >
                <Typography
                  typography="s2"
                  color="text.primary"
                  fontWeight="fontWeightMedium"
                >
                  Create custom model
                </Typography>
              </Button>
            </Box>
          </Box>

          {/* Unified Empty State */}
          <ShowComponent condition={isEmpty}>
            <Box
              sx={{
                gridColumn: "1 / -1",
                display: "flex",
                justifyContent: "center",
                alignItems: "center",
                minHeight: "300px",
                height: "calc(100vh - 300px)",
              }}
            >
              <EmptyLayout
                icon="/assets/icons/navbar/hugeicons.svg"
                title={emptyStateContent[selectedFilter]?.title}
                description={
                  searchDebounce !== ""
                    ? `No relevant search results were found for '${searchDebounce}'`
                    : emptyStateContent[selectedFilter]?.description
                }
                action={null}
              />
            </Box>
          </ShowComponent>

          {/* Grid Content */}
          <Box sx={keyCardsGridStyles}>
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
                    showJsonField={true}
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
          </Box>
        </Box>
      </ShowComponent>

      <AddCustomModal
        open={showCreateModal}
        onClose={() => setShowCreateModal(false)}
        data={null}
        edit={false}
      />
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
    </Box>
  );
};

const AIProviders = () => {
  return (
    <>
      <Helmet>
        <title>AI Providers</title>
      </Helmet>
      <Box
        sx={{
          display: "flex",
          flexDirection: "column",
          gap: 2,
          height: "100vh",
        }}
      >
        <Box>
          <Typography
            sx={{
              typography: "m2",
              fontWeight: "fontWeightSemiBold",
              color: "text.primary",
            }}
          >
            AI Providers
          </Typography>
          <Typography
            sx={{
              typography: "s1",
              fontWeight: "fontWeightRegular",
              color: "text.primary",
              marginTop: (theme) => theme.spacing(0.5),
            }}
          >
            Manage your AI providers
          </Typography>
        </Box>

        <ConfigureProviders />
      </Box>
    </>
  );
};

export default AIProviders;
