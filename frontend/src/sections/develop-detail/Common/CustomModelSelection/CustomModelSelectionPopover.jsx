import {
  Box,
  Button,
  Checkbox,
  MenuItem,
  Popover,
  Skeleton,
  Stack,
  Typography,
  useTheme,
} from "@mui/material";
import PropTypes from "prop-types";
import React, { useState } from "react";
import FormSearchField from "src/components/FormSearchField/FormSearchField";
import Image from "src/components/image";
import { useScrollEnd } from "src/hooks/use-scroll-end";
import { LOGO_WITH_BLACK_BACKGROUND } from "src/components/custom-model-dropdown/common";

const PopoverOptions = ({
  options,
  isLoadingModelList,
  onChange,
  onConfigOpen,
  setIsOpen,
  isFetchingNextPage,
  fetchNextPage,
  multiSelect,
  selectedModels,
  setSelectedModels,
  valueIsObject,
}) => {
  const theme = useTheme();

  const getModelValue = (modelName) => {
    return valueIsObject ? { modelName } : modelName;
  };

  const isModelSelected = (modelName) => {
    const value = getModelValue(modelName);
    return selectedModels.some((item) =>
      valueIsObject ? item?.modelname === value?.modelname : item === value,
    );
  };

  const scrollRef = useScrollEnd(() => {
    if (isFetchingNextPage || isLoadingModelList) return;
    fetchNextPage();
  }, [isFetchingNextPage, isLoadingModelList]);

  const handleModelSelect = (option) => {
    const { modelName, providers, isAvailable, logoUrl } = option;

    if (!isAvailable) {
      onConfigOpen?.();
      return;
    }

    const value = valueIsObject
      ? {
          modelName,
          providers,
          isAvailable,
          logoUrl,
        }
      : modelName;

    if (multiSelect) {
      setSelectedModels((prev) => {
        const exists = prev.some((item) =>
          valueIsObject ? item.model_name === value.model_name : item === value,
        );
        return exists
          ? prev.filter((item) =>
              valueIsObject
                ? item.model_name !== value.model_name
                : item !== value,
            )
          : [...prev, value];
      });
    } else {
      onChange?.(value);
      setIsOpen(false);
    }
  };

  return (
    <Box ref={scrollRef} sx={{ height: "100%", overflowY: "auto", flex: 1 }}>
      {isLoadingModelList && (
        <Box>
          <Skeleton variant="text" height={34} />
          <Skeleton variant="text" height={34} />
          <Skeleton variant="text" height={34} />
        </Box>
      )}
      {!isLoadingModelList &&
        options.map((option) => {
          const { modelName, providers, isAvailable, logoUrl } = option;
          return (
            <MenuItem
              disabled={!isAvailable}
              key={modelName}
              onClick={() => handleModelSelect(option)}
              sx={{
                pointerEvents: "all",
                overflow: "hidden",
                textOverflow: "ellipsis",
                display: "flex",
                alignItems: "center",
                gap: theme.spacing(1.5),
                "&.Mui-disabled": {
                  pointerEvents: "auto",
                  cursor: "pointer",
                  opacity: 1,
                  // color: "error.main",
                },
                backgroundColor:
                  multiSelect && isModelSelected(modelName)
                    ? "action.selected"
                    : "transparent",

                ml: multiSelect ? 0 : 0,
              }}
            >
              {multiSelect && (
                <Checkbox
                  checked={isModelSelected(modelName)}
                  onClick={(e) => e.stopPropagation()}
                  onChange={(e) => {
                    e.stopPropagation();
                    handleModelSelect(option);
                  }}
                  sx={{ py: 0 }}
                />
              )}
              <Image
                src={logoUrl}
                alt=""
                width={theme.spacing(2)}
                disableThemeFilter={
                  !LOGO_WITH_BLACK_BACKGROUND.includes(providers?.toLowerCase())
                }
              />
              <Stack
                direction={"row"}
                flexWrap={"wrap"}
                gap={(theme) => theme.spacing(0.5)}
              >
                <Typography
                  variant="s2"
                  color={"text.primary"}
                  fontWeight={"fontWeightRegular"}
                >
                  {modelName}
                </Typography>
                {!isAvailable && (
                  <Typography
                    variant="s3"
                    fontWeight={"fontWeightRegular"}
                    sx={{
                      color: "error.main",
                    }}
                  >
                    {`configure an API ey for ${providers}`}
                  </Typography>
                )}
              </Stack>
            </MenuItem>
          );
        })}
      {isFetchingNextPage && (
        <Box>
          <Skeleton variant="text" height={34} />
          <Skeleton variant="text" height={34} />
          <Skeleton variant="text" height={34} />
        </Box>
      )}
    </Box>
  );
};

PopoverOptions.propTypes = {
  options: PropTypes.array,
  isLoadingModelList: PropTypes.bool,
  isFetchingNextPage: PropTypes.bool,
  fetchNextPage: PropTypes.func,
  onChange: PropTypes.func,
  onConfigOpen: PropTypes.func,
  setIsOpen: PropTypes.func,
  multiSelect: PropTypes.bool,
  selectedModels: PropTypes.array,
  setSelectedModels: PropTypes.func,
  fields: PropTypes.arrayOf(
    PropTypes.shape({
      id: PropTypes.string.isRequired,
      value: PropTypes.string.isRequired,
    }),
  ),
  setSelectedModelProvider: PropTypes.func,
  valueIsObject: PropTypes.bool,
};

//@ts-ignore
const CustomModelSelectionPopover = ({
  isOpen,
  setIsOpen,
  handleClose,
  anchorEl,
  options,
  onChange,
  onConfigOpen,
  search,
  setSearch,
  isLoadingModelList,
  isFetchingNextPage,
  fetchNextPage,
  multiSelect = false,
  fields,
  valueIsObject = false,
}) => {
  const [selectedModels, setSelectedModels] = useState([]);

  const handleCancel = () => {
    setSelectedModels([]);
    handleClose();
  };

  const handleAdd = () => {
    if (selectedModels.length > 0) {
      const uniqueModels = selectedModels.filter((model) =>
        valueIsObject
          ? !fields.some((field) => field.value.name === model.name)
          : !fields.some((field) => field.value === model),
      );
      if (uniqueModels.length > 0) {
        onChange?.(uniqueModels);
      }
      setSelectedModels([]);
    }
    setIsOpen(false);
  };

  return (
    <Popover
      open={isOpen}
      onClose={handleClose}
      anchorEl={anchorEl?.current}
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
          width: anchorEl?.current?.clientWidth,
          maxHeight: 300,
          display: "flex",
          flexDirection: "column",
          gap: 1,
        },
      }}
    >
      <FormSearchField
        label="Search"
        variant="outlined"
        fullWidth
        size="small"
        autoFocus
        searchQuery={search}
        sx={{ marginTop: (theme) => theme.spacing(0.5) }}
        onChange={(e) => setSearch(e.target.value)}
      />
      <PopoverOptions
        options={options}
        isLoadingModelList={isLoadingModelList}
        isFetchingNextPage={isFetchingNextPage}
        fetchNextPage={fetchNextPage}
        onChange={onChange}
        onConfigOpen={onConfigOpen}
        setIsOpen={setIsOpen}
        multiSelect={multiSelect}
        selectedModels={selectedModels}
        setSelectedModels={setSelectedModels}
        fields={fields}
        valueIsObject={valueIsObject}
      />
      {multiSelect && (
        <Box
          sx={{
            display: "flex",
            gap: 1,
            justifyContent: "flex-end",
            margin: 1,
          }}
        >
          <Button onClick={handleCancel} size="small" variant="outlined">
            Cancel
          </Button>
          <Button
            size="small"
            onClick={handleAdd}
            variant="contained"
            color="primary"
            disabled={
              selectedModels.length === 0 ||
              selectedModels.some((modelName) =>
                fields?.some((field) => field.value === modelName),
              )
            }
          >
            Add
          </Button>
        </Box>
      )}
    </Popover>
  );
};

CustomModelSelectionPopover.propTypes = {
  isOpen: PropTypes.bool,
  handleClose: PropTypes.func,
  anchorEl: PropTypes.any,
  options: PropTypes.array,
  onChange: PropTypes.func,
  onConfigOpen: PropTypes.func,
  setIsOpen: PropTypes.func,
  search: PropTypes.string,
  setSearch: PropTypes.func,
  isLoadingModelList: PropTypes.bool,
  isFetchingNextPage: PropTypes.bool,
  fetchNextPage: PropTypes.func,
  multiSelect: PropTypes.bool,
  fields: PropTypes.arrayOf(
    PropTypes.shape({
      id: PropTypes.string.isRequired,
      value: PropTypes.string.isRequired,
    }),
  ),
  setSelectedModelProvider: PropTypes.func,
  valueIsObject: PropTypes.bool,
};

export default CustomModelSelectionPopover;
