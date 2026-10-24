import { Box, Button, Stack, Typography, useTheme } from "@mui/material";
import PropTypes from "prop-types";
import React, { useMemo, useState } from "react";
import Image from "src/components/image";
import { Icon } from "@iconify/react";
import APIKeyReadOnlyView from "./APIKeyReadOnlyView";
import { enqueueSnackbar } from "../snackbar";
import { useMutation, useQueryClient } from "@tanstack/react-query";
import axios, { endpoints } from "src/utils/axios";
import Actions from "src/sections/develop-detail/Common/ConfigureKeys/Actions";
import { ShowComponent } from "../show";
import { LOGO_WITH_BLACK_BACKGROUND } from "./common";

const CloudProviderCardComponent = ({
  provider,
  showJsonField,
  onDeleteClick,
}) => {
  const [openModal, setOpenModal] = useState(false);
  const isJsonEditor =
    provider.type === "json" && typeof provider.maskedKey === "object";

  const queryClient = useQueryClient();

  const { mutate: createOrUpdateKey } = useMutation({
    /**
     *
     * @param {Object} data
     * @returns
     */
    mutationFn: (data) =>
      axios.post(
        provider?.hasKey
          ? endpoints.develop.apiKey.update
          : endpoints.develop.apiKey.create,
        {
          provider: provider?.provider,
          key: data.key,
        },
      ),
    onSuccess: () => {
      enqueueSnackbar("Config saved successfully", { variant: "success" });
      queryClient.invalidateQueries({ queryKey: ["model-list"] });
      queryClient.invalidateQueries({ queryKey: ["api-key-status"] });
      setOpenModal(false);
    },
    onError: () => {
      enqueueSnackbar("Failed to save config", { variant: "error" });
    },
  });

  const theme = useTheme();
  const rootBoxStyles = useMemo(
    () => ({
      border: "1px solid",
      borderColor: "divider",
      backgroundColor: "background.paper",
      borderRadius: "8px",
      padding: theme.spacing(2),
      display: "flex",
      flexDirection: "column",
      gap: theme.spacing(2),
      position: "relative",
    }),
    [theme],
  );

  const onSubmit = (data) => {
    // trackEvent(Events.saveApiClicked, {
    //   [PropertyName.click]: data.provider,
    // });
    createOrUpdateKey(data);
  };

  const contentBoxStyles = useMemo(
    () => ({
      display: "flex",
      flexDirection: "column",
      height: "100%",
      justifyContent: "center",
      width: "100%",
      gap: theme.spacing(2),
    }),
    [theme],
  );

  const headerBoxStyles = useMemo(
    () => ({
      display: "flex",
      alignItems: "center",
      justifyContent: "space-between",
      gap: theme.spacing(2),
    }),
    [theme],
  );

  const buttonStyles = useMemo(
    () => ({
      minWidth: "90px",
      whiteSpace: "nowrap",
    }),
    [theme],
  );

  return (
    <Box sx={rootBoxStyles}>
      <Box sx={contentBoxStyles}>
        <Box sx={headerBoxStyles}>
          <Box
            sx={{
              flexShrink: 0,
              display: "flex",
              flexDirection: "row",
              alignItems: "center",
              gap: theme.spacing(1),
            }}
          >
            <Image
              src={provider.logoUrl}
              alt={provider.display_name}
              width={25}
              height={25}
              disableThemeFilter={
                !LOGO_WITH_BLACK_BACKGROUND.includes(
                  provider?.provider?.toLowerCase(),
                )
              }
              style={{ objectFit: "contain" }}
            />
            <Typography
              typography="s1"
              color="text.primary"
              fontWeight="fontWeightMedium"
            >
              {provider.display_name}
            </Typography>
          </Box>

          {provider.hasKey ? (
            <Stack direction="row" alignItems="center" gap={1}>
              <ShowComponent condition={!showJsonField}>
                <Actions
                  onEditClick={() => setOpenModal(true)}
                  onDeleteClick={onDeleteClick}
                />
              </ShowComponent>
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
            </Stack>
          ) : (
            <Button
              variant={provider.hasKey ? "outlined" : "contained"}
              size="small"
              color="primary"
              onClick={() => setOpenModal(true)}
              sx={buttonStyles}
            >
              {"Add"}
            </Button>
          )}
        </Box>

        {/* API Key Form (if key exists) */}
        <APIKeyReadOnlyView
          isJsonKey={isJsonEditor}
          showJsonField={showJsonField && provider?.hasKey}
          keyValue={provider?.maskedKey}
          provider={provider}
          onSubmit={onSubmit}
          openModal={openModal}
          setOpenModal={setOpenModal}
          onDeleteClick={onDeleteClick}
        />
      </Box>
    </Box>
  );
};

CloudProviderCardComponent.propTypes = {
  provider: PropTypes.object,
  showJsonField: PropTypes.bool,
  onDeleteClick: PropTypes.func,
};

const CloudProviderCard = React.memo(CloudProviderCardComponent);
CloudProviderCard.displayName = "CloudProviderCard";

export default CloudProviderCard;
