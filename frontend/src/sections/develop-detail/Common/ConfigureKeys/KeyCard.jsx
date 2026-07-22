import { zodResolver } from "@hookform/resolvers/zod";
import { Box, Typography, useTheme } from "@mui/material";
import { useMutation, useQueryClient } from "@tanstack/react-query";
import React, { useEffect, useMemo, useRef, useState } from "react";
import { useForm } from "react-hook-form";
import axios, { endpoints } from "src/utils/axios";
import { z } from "zod";
import { enqueueSnackbar } from "src/components/snackbar";
import PropTypes from "prop-types";
import { LoadingButton } from "@mui/lab";
import Image from "src/components/image";
import APIKeyForm from "src/components/custom-model-dropdown/APIKeyForm";
import { Icon } from "@iconify/react";
import { ShowComponent } from "src/components/show";
import APIKeyReadOnlyView from "src/components/custom-model-dropdown/APIKeyReadOnlyView";
import Actions from "./Actions";
import { LOGO_WITH_BLACK_BACKGROUND } from "src/components/custom-model-dropdown/common";

const KeyCardComponent = ({ data, onDeleteClick }) => {
  const theme = useTheme();
  const hasClearedOnceRef = useRef(false);
  const focusTimeoutRef = useRef(null);
  const queryClient = useQueryClient();
  const [openModal, setOpenModal] = useState(false);
  const [editMode, setEditMode] = useState(false);
  const {
    control,
    reset,
    watch,
    clearErrors,
    handleSubmit,
    setValue,
    formState: { isDirty },
    setFocus,
  } = useForm({
    defaultValues: {
      provider: data?.provider ?? "",
      key:
        data.type === "json" &&
        data.maskedKey &&
        typeof data.maskedKey === "object"
          ? JSON.stringify(data.maskedKey, null, 2)
          : data.maskedKey ?? "",
    },
    resolver: zodResolver(
      z.object({
        provider: z.string().min(1, "Provider is required"),
        key: z.string().min(1, "Key is required"),
      }),
    ),
  });

  const keyValue = watch("key");
  const isJsonKey =
    data.type === "json" &&
    (() => {
      try {
        const parsed = JSON.parse(keyValue);
        return typeof parsed === "object" && parsed !== null;
      } catch {
        return false;
      }
    })();

  //   useEffect(() => {
  //   if (data?.provider) {
  //     setValue("provider", data?.provider);
  //   }
  // }, [data?.provider, setValue]);

  useEffect(() => {
    if (!data) return;

    const isJson =
      data.type === "json" &&
      data.maskedKey &&
      typeof data.maskedKey === "object";
    reset({
      key: isJson
        ? JSON.stringify(data.maskedKey, null, 2)
        : data?.maskedKey ?? "",
      provider: data?.provider,
    });
    // Reset the cleared ref when form resets (e.g., after save or data change)
    hasClearedOnceRef.current = false;
  }, [data, reset]);

  const { mutate: createApiKey, isPending } = useMutation({
    mutationFn: (d) => axios.post(endpoints.develop.apiKey.create, d),
    onSuccess: () => {
      enqueueSnackbar("API Key created successfully", { variant: "success" });

      queryClient.invalidateQueries({
        queryKey: ["model-list"],
      });
      queryClient.invalidateQueries({ queryKey: ["api-key-status"] });
      setEditMode(false);
      hasClearedOnceRef.current = false;
    },
  });

  const { mutate: updateApiKey, isPending: isUpdatingApiKey } = useMutation({
    mutationFn: (d) => axios.post(endpoints.develop.apiKey.update, d),
    onSuccess: () => {
      enqueueSnackbar("API Key updated successfully", { variant: "success" });
      queryClient.invalidateQueries({
        queryKey: ["model-list"],
      });
      queryClient.invalidateQueries({ queryKey: ["api-key-status"] });
      setEditMode(false);
      hasClearedOnceRef.current = false;
    },
  });
  const handleFormSubmit = async (formData) => {
    // trackEvent(Events.saveApiClicked, {
    //   [PropertyName.click]: formData?.provider,
    // });

    // Add the hasKey property from the original data
    const submitData = {
      ...formData,
      has_key: data?.hasKey,
    };

    if (data?.hasKey) {
      updateApiKey(submitData);
    } else {
      createApiKey(submitData);
    }
  };

  const handleSubmitData = async (submitData) => {
    // For JSON keys, ensure provider is included from the component's data
    const dataToSubmit = {
      ...submitData,
      provider: submitData?.provider || data?.provider, // Use provider from form or fallback to component data
    };

    // trackEvent(Events.saveApiClicked, {
    //   [PropertyName.click]: dataToSubmit?.provider,
    // });
    if (data?.hasKey) {
      updateApiKey(dataToSubmit);
    } else {
      createApiKey(dataToSubmit);
    }
  };

  const onFocusInput = () => {
    // Clear the masked value on focus, but only once per edit session
    // The ref resets when edit mode changes or form resets
    if (hasClearedOnceRef.current) return;

    clearErrors("key");

    setValue("key", "", { shouldDirty: true });
    hasClearedOnceRef.current = true;

    if (focusTimeoutRef.current) {
      clearTimeout(focusTimeoutRef.current);
    }

    // Defer setFocus to ensure DOM has updated after setValue
    focusTimeoutRef.current = setTimeout(() => {
      setFocus("key");
    }, 0);

    // trackEvent(Events.apiKeyBoxClicked);
  };

  const handleEscapeKey = (event) => {
    if (event.key === "Escape" && editMode) {
      event.preventDefault();
      // Reset form to original values
      const isJson =
        data.type === "json" &&
        data.maskedKey &&
        typeof data.maskedKey === "object";
      reset({
        key: isJson
          ? JSON.stringify(data.maskedKey, null, 2)
          : data?.maskedKey ?? "",
        provider: data?.provider,
      });
      // Clear any form errors
      clearErrors();
      // Exit edit mode
      setEditMode(false);
      // Reset the cleared ref
      hasClearedOnceRef.current = false;
    }
  };

  useEffect(() => {
    return () => {
      if (focusTimeoutRef.current) {
        clearTimeout(focusTimeoutRef.current);
      }
    };
  }, []);

  const wrapperStyles = useMemo(
    () => ({
      border: "1px solid",
      borderColor: "divider",
      backgroundColor: "background.paper",
      borderRadius: "8px",
      padding: theme.spacing(2),
      display: "flex",
      position: "relative",
      flexDirection: "column",
      gap: theme.spacing(2),
    }),
    [theme],
  );

  const formContainerStyles = useMemo(
    () => ({
      display: "flex",
      flexDirection: "column",
      gap: theme.spacing(2),
    }),
    [theme],
  );

  const topRowStyles = useMemo(
    () => ({
      display: "flex",
      flexDirection: "row",
      justifyContent: "space-between",
      alignItems: "center",
      gap: theme.spacing(2),
    }),
    [theme],
  );

  const inputRowStyles = useMemo(
    () => ({
      display: "flex",
      gap: theme.spacing(2),
      alignItems: "center",
      flexWrap: "wrap",
    }),
    [theme],
  );

  const saveButtonStyles = useMemo(
    () => ({
      minWidth: "90px",
      height: theme.spacing(4.75), // 38/8
      borderRadius: "8px",
      whiteSpace: "nowrap",
      fontSize: "12px",
      fontWeight: 500,
    }),
    [theme],
  );

  return (
    <Box sx={wrapperStyles}>
      {isJsonKey ? (
        // JSON View (outside form)
        <Box sx={formContainerStyles}>
          {/* Top row: logo + name */}
          <Box sx={topRowStyles}>
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
                src={data?.logoUrl}
                alt={data?.display_name}
                width={25}
                height={25}
                disableThemeFilter={
                  !LOGO_WITH_BLACK_BACKGROUND.includes(
                    data?.provider?.toLowerCase(),
                  )
                }
                style={{ objectFit: "contain" }}
              />
              <Typography
                component="div"
                typography="s1"
                color="text.primary"
                fontWeight="fontWeightMedium"
              >
                {data?.display_name}
              </Typography>
            </Box>
            <ShowComponent condition={data?.hasKey}>
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
            </ShowComponent>
          </Box>

          <APIKeyReadOnlyView
            isJsonKey={true}
            showJsonField={data?.hasKey}
            keyValue={data?.maskedKey}
            provider={data}
            onSubmit={handleSubmitData}
            openModal={openModal}
            setOpenModal={setOpenModal}
            onDeleteClick={onDeleteClick}
          />
        </Box>
      ) : (
        // Editable Form View
        <form
          onSubmit={(e) => {
            e.preventDefault();
            e.stopPropagation();
            handleSubmit(handleFormSubmit)();
          }}
        >
          <Box sx={formContainerStyles}>
            {/* Top row: logo + name */}
            <Box sx={topRowStyles}>
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
                  src={data?.logoUrl}
                  alt={data?.display_name}
                  width={25}
                  height={25}
                  disableThemeFilter={
                    !LOGO_WITH_BLACK_BACKGROUND.includes(
                      data?.provider?.toLowerCase(),
                    )
                  }
                  style={{ objectFit: "contain" }}
                />
                <Typography
                  component="div"
                  typography="s1"
                  color="text.primary"
                  fontWeight="fontWeightMedium"
                >
                  {data?.display_name}
                </Typography>
              </Box>
              <ShowComponent condition={data?.maskedKey && !editMode}>
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
              </ShowComponent>
            </Box>

            <Box sx={inputRowStyles}>
              <Box sx={{ flexGrow: 1 }}>
                <APIKeyForm
                  control={control}
                  isJsonKey={false}
                  onFocusInput={onFocusInput}
                  onKeyDown={handleEscapeKey}
                  disabled={Boolean(!editMode && data?.maskedKey)}
                />
              </Box>
              {data?.hasKey && !editMode ? (
                <Actions
                  onEditClick={() => {
                    setEditMode(true);
                    onFocusInput();
                  }}
                  onDeleteClick={onDeleteClick}
                />
              ) : (
                <LoadingButton
                  variant="contained"
                  size="small"
                  color="primary"
                  type="submit"
                  aria-label="save"
                  loading={isPending || isUpdatingApiKey}
                  disabled={!isDirty}
                  sx={saveButtonStyles}
                >
                  {data?.maskedKey ? "Save" : "Add"}
                </LoadingButton>
              )}
            </Box>
          </Box>
        </form>
      )}
    </Box>
  );
};

KeyCardComponent.propTypes = {
  data: PropTypes.object,
  onClose: PropTypes.func,
  isFetching: PropTypes.bool,
  onDeleteClick: PropTypes.func,
};

const KeyCard = React.memo(KeyCardComponent);
KeyCard.displayName = "KeyCard";

export default KeyCard;
