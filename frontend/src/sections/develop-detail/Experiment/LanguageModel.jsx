import {
  Box,
  Chip,
  FormControl,
  FormHelperText,
  MenuItem,
  Typography,
} from "@mui/material";
import _ from "lodash";
import PropTypes from "prop-types";
import React, { useMemo, useRef, useState } from "react";
import { useFieldArray, useFormState, useWatch } from "react-hook-form";
import { getRandomId } from "src/utils/utils";
import CustomModelSelectionPopover from "../Common/CustomModelSelection/CustomModelSelectionPopover";
import ConfigureKeys from "../Common/ConfigureKeys/ConfigureKeys";
import { useInfiniteQuery } from "@tanstack/react-query";
import axios, { endpoints } from "src/utils/axios";
import DropdownWithSearch from "src/sections/common/DropdownWithSearch";
import SvgColor from "src/components/svg-color";

const LanguageModel = ({ control, fieldName }) => {
  const { fields, append, remove } = useFieldArray({
    control,
    name: fieldName,
  });
  const [selectedModel, setSelectedModel] = useState(null);
  const [isOpen, setIsOpen] = useState(false);
  const anchorEl = useRef(null);

  const wactchedModels = useWatch({
    control,
    name: fieldName,
  });

  const [search, setSearch] = useState("");

  const {
    data: modelList,
    isLoading: isLoadingModelList,
    fetchNextPage,
    isFetchingNextPage,
  } = useInfiniteQuery({
    queryKey: ["model-list", search],
    queryFn: ({ pageParam }) =>
      axios.get(endpoints.develop.modelList, {
        params: { page: pageParam, search: search },
      }),
    getNextPageParam: (o) => (o.data.next ? o.data.current_page + 1 : null),
    initialPageParam: 1,
  });

  const modelOptions = useMemo(() => {
    // Extract existing model names from fields
    const existingModelNames = fields.map((field) => field?.value);

    return (
      modelList?.pages.reduce(
        (acc, curr) => [
          ...acc,
          ...curr.data.results.filter(
            (model) => !existingModelNames.includes(model.model_name),
          ),
        ],
        [],
      ) || []
    );
  }, [modelList, fields]);

  const { errors } = useFormState({ control });

  const errorMessage = _.get(errors, `${fieldName}`)?.message;
  const isError = !!errorMessage;

  const handleOpen = () => {
    setIsOpen(true);
  };

  const handleClose = () => {
    setIsOpen(false);
  };

  const [isApiConfigurationOpen, setApiConfigurationOpen] = useState(false);

  return (
    <Box sx={{ display: "flex", flexDirection: "column", gap: 1 }}>
      <ConfigureKeys
        open={isApiConfigurationOpen}
        onClose={() => setApiConfigurationOpen(false)}
      />
      <Typography
        fontWeight={"fontWeightMedium"}
        variant="s1"
        color="text.primary"
      >
        Language Model
      </Typography>
      <Box
        sx={{
          display: "flex",
          alignItems: "flex-start",
          gap: (theme) => theme.spacing(0.5),
          flexDirection: "column",
          mt: (theme) => theme.spacing(0.5),
        }}
      >
        <FormControl fullWidth size="small">
          <DropdownWithSearch
            size="small"
            label="Model"
            value={selectedModel}
            // onChange={(e) => setSelectedModel(e.target.value)}
            onOpen={handleOpen}
            open={isOpen}
            autoFocus={isError}
            options={modelOptions}
            renderValue={(value) => <MenuItem value={value}>{value}</MenuItem>}
            onSelect={(value) => {
              if (Array.isArray(value)) {
                value.forEach((v) => {
                  append({ value: v, id: getRandomId() });
                });
              } else {
                append({ value: value, id: getRandomId() });
              }
              setSelectedModel(null);
            }}
            anchorRef={anchorEl}
            onClose={handleClose}
            anchorElement={anchorEl}
          />
          <CustomModelSelectionPopover
            isOpen={isOpen}
            handleClose={handleClose}
            options={modelOptions}
            setIsOpen={setIsOpen}
            onChange={(values) => {
              if (Array.isArray(values)) {
                values.forEach((value) => {
                  append({ value, id: getRandomId() });
                });
              } else {
                append({ value: values, id: getRandomId() });
              }
              setSelectedModel(null);
            }}
            onConfigOpen={() => setApiConfigurationOpen(true)}
            anchorEl={anchorEl}
            search={search}
            setSearch={setSearch}
            isLoadingModelList={isLoadingModelList}
            isFetchingNextPage={isFetchingNextPage}
            fetchNextPage={fetchNextPage}
            multiSelect={true}
            fields={fields}
          />
        </FormControl>
        {isError && (
          <FormHelperText
            sx={{
              margin: 0,
            }}
            error
          >
            {errorMessage}
          </FormHelperText>
        )}
      </Box>
      {wactchedModels?.length > 0 ? (
        <Box sx={{ display: "flex", gap: 1, flexWrap: "wrap" }}>
          {wactchedModels?.map((field, index) => (
            <Chip
              key={field.id}
              onDelete={() => remove(index)}
              label={field.value}
              variant="soft"
              color="primary"
              deleteIcon={
                <SvgColor width={14} src="/assets/icons/ic_close.svg" />
              }
              sx={{
                padding: (theme) => theme.spacing(0.5, 1),
                "& .MuiChip-label": {
                  color: "primary.main",
                  typography: "s2",
                  padding: 0,
                  marginRight: (theme) => theme.spacing(0.5),
                },
              }}
            />
          ))}
        </Box>
      ) : (
        <></>
      )}
    </Box>
  );
};

LanguageModel.propTypes = {
  control: PropTypes.object,
  fieldName: PropTypes.string,
};

export default LanguageModel;
