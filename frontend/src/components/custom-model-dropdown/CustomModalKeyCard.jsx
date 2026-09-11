import { Box, Button, Typography, useTheme } from "@mui/material";
import { useMutation, useQueryClient } from "@tanstack/react-query";
import React, { useState } from "react";
import axios, { endpoints } from "src/utils/axios";
import PropTypes from "prop-types";
import { enqueueSnackbar } from "notistack";
import { ShowComponent } from "../show";
import APIKeyReadOnlyView from "./APIKeyReadOnlyView";
import { Icon } from "@iconify/react";
import CustomModalAvatar from "./CustomModalAvatar";

const CustomModalKeyCard = ({ data, onDeleteClick }) => {
  const queryClient = useQueryClient();
  const [openModal, setOpenModal] = useState(false);
  const theme = useTheme();
  const modelName =
    data?.userModelId ??
    data?.user_model_id ??
    data?.modelName ??
    data?.model_name ??
    "";
  const configJson = data?.configJson ?? data?.config_json ?? null;
  const inputTokenCost = data?.inputTokenCost ?? data?.input_token_cost;
  const outputTokenCost = data?.outputTokenCost ?? data?.output_token_cost;
  const hasConfigJson =
    !!configJson &&
    (typeof configJson !== "object" || Object.keys(configJson).length > 0);

  const { mutate: updateCustomModel } = useMutation({
    /**
     *
     * @param {Object} d
     * @returns
     */
    mutationFn: ({ configJson, payload }) => {
      return axios.patch(endpoints.settings.customModal.editCustomModel, {
        ...payload,
        id: data.id,
        config_json: configJson,
      });
    },
    onSuccess: () => {
      enqueueSnackbar("Custom model updated successfully", {
        variant: "success",
      });
      queryClient.invalidateQueries({ queryKey: ["model-list"] });
      queryClient.invalidateQueries({ queryKey: ["custom-models"] });
      queryClient.invalidateQueries({ queryKey: ["customModals"] });
      setOpenModal(false);
    },
  });

  const onSubmit = ({ configJson: submittedConfigJson, payload }) => {
    const newConfigJson = { ...submittedConfigJson };
    /// if this was custom provider we need to always send customProvider true
    if (
      configJson?.customProvider ||
      configJson?.custom_provider ||
      submittedConfigJson?.customProvider ||
      submittedConfigJson?.custom_provider
    ) {
      newConfigJson.custom_provider = true;
    }
    updateCustomModel({
      configJson: newConfigJson,
      payload,
    });
  };

  const handleFormSubmit = (formData) => {
    let configJson;
    try {
      configJson = JSON.parse(formData?.key);
    } catch (error) {
      enqueueSnackbar("Invalid JSON", {
        variant: "error",
      });
      return;
    }
    const payload = {
      model_name: modelName,
      input_token_cost: inputTokenCost,
      output_token_cost: outputTokenCost,
    };
    onSubmit({ ...formData, configJson, payload });
  };

  return (
    <Box
      sx={{
        border: "1px solid",
        borderColor: "divider",
        borderRadius: (theme) => theme.spacing(1),
        backgroundColor: "background.paper",
        padding: (theme) => theme.spacing(2),
        display: "flex",
        flexDirection: "column",
        gap: (theme) => theme.spacing(2),
      }}
    >
      <Box
        sx={{
          display: "flex",
          alignItems: "center",
          justifyContent: "space-between",
          width: "100%",
        }}
      >
        <Box
          sx={{
            display: "flex",
            alignItems: "center",
            gap: (theme) => theme.spacing(1.5),
          }}
        >
          {/* Avatar Circle */}
          <CustomModalAvatar text={modelName} />

          {/* Title + Subtitle Column */}
          <Box sx={{ display: "flex", flexDirection: "column" }}>
            <Typography
              typography="s1"
              fontWeight="fontWeightMedium"
              color="text.primary"
            >
              {modelName}
            </Typography>
          </Box>
        </Box>

        {hasConfigJson ? (
          <Icon
            icon="gg-check-o"
            width={16}
            height={16}
            style={{
              color: theme.palette.green[400],
              marginRight: "2px",
              marginLeft: "0px",
            }}
          />
        ) : (
          <Button
            variant="contained"
            size="small"
            color="primary"
            sx={{
              paddingX: (theme) => theme.spacing(3),
              minWidth: "90px",
              height: (theme) => theme.spacing(38 / 8),
            }}
            onClick={() => setOpenModal(true)}
          >
            {"Add"}
          </Button>
        )}
      </Box>
      <ShowComponent condition={hasConfigJson}>
        <APIKeyReadOnlyView
          isJsonKey={true}
          showJsonField={true}
          openModal={openModal}
          setOpenModal={setOpenModal}
          keyValue={configJson}
          provider={{
            ...data,
            maskedKey: configJson,
            logoUrl: "",
            displayName: modelName,
            display_name: modelName,
            type: "json",
            hasKey: true,
          }}
          onSubmit={handleFormSubmit}
          onDeleteClick={onDeleteClick}
        />
      </ShowComponent>
    </Box>
  );
};

CustomModalKeyCard.propTypes = {
  key: PropTypes.any,
  data: PropTypes.object,
  onDeleteClick: PropTypes.func,
};

export default CustomModalKeyCard;
