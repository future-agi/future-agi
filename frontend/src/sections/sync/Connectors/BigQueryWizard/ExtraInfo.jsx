import React, { useMemo, useState } from "react";
import PropTypes from "prop-types";
import {
  Alert,
  Box,
  Button,
  Chip,
  FormControl,
  FormHelperText,
  InputLabel,
  MenuItem,
  Select,
} from "@mui/material";
import { useFieldArray, useForm } from "react-hook-form";
import Iconify from "src/components/iconify";
import { useMutation, useQueryClient } from "@tanstack/react-query";
import axios, { endpoints } from "src/utils/axios";
import { useSearchParams } from "src/routes/hooks";
import { getMappedData } from "src/utils/connectors/big-query-utils";

import WizardInputHelpText from "../shared/WizardInputHelpText";
import SectionCard from "../shared/SectionCard";
import MappedTable from "../shared/MappedTable";
import BottomButtons from "../shared/BottomButtons";

const ExtraInfo = ({ setActiveStep, draftInfo }) => {
  const columns = draftInfo?.columns;

  const userColumnOptions = columns?.length
    ? columns.map((v) => ({
        label: v,
        value: v,
      }))
    : [];

  const { control, watch, handleSubmit } = useForm({
    defaultValues: {
      tags: draftInfo?.tags?.length ? draftInfo?.tags?.split(",") : [],
    },
  });

  const queryClient = useQueryClient();

  const { append, remove } = useFieldArray({ control, name: "tags" });

  const [selectedTag, setSelectedTag] = useState("");

  const tags = watch("tags");

  const [searchParams] = useSearchParams();

  const draftId = searchParams.draftId;

  const {
    isPending: isSubmitting,
    mutate,
    isError,
    error,
  } = useMutation({
    mutationFn: (d) =>
      axios.put(
        `${endpoints.connectors.updateDraft}${searchParams.draftId}/`,
        d,
      ),
    onSuccess: () => {
      queryClient.invalidateQueries({
        queryKey: ["draft", draftId],
        type: "all",
      });
      setActiveStep(4);
    },
  });

  const renderAlert = () => {
    if (!isError) {
      return <></>;
    }
    return (
      <Alert variant="standard" severity="error">
        {error?.message || "Something went wrong"}
      </Alert>
    );
  };

  const mappedData = useMemo(() => {
    return getMappedData(draftInfo?.connMappings, tags);
  }, [draftInfo, tags]);

  const onFormSubmit = (formValues) =>
    mutate({ tags: formValues.tags.join(",") });

  return (
    <>
      <Box
        sx={{
          display: "flex",
          gap: "43px",
          height: "100%",
          padding: "51px 52px 73px 52px",
          maxHeight: "100%",
          overflowY: "auto",
        }}
      >
        <Box
          sx={{
            flex: 1,
            overflow: "auto",
          }}
        >
          <Box sx={{ display: "flex", flexDirection: "column", gap: "20px" }}>
            <SectionCard title="Tags">
              <Box
                sx={{
                  padding: "20px",
                  display: "flex",
                  flexDirection: "column",
                  gap: 2,
                }}
              >
                <Box
                  sx={{
                    display: "flex",
                    alignItems: "flex-start",
                    gap: "24px",
                  }}
                >
                  <FormControl fullWidth>
                    <InputLabel>Tags</InputLabel>
                    <Select
                      value={selectedTag}
                      label="Tags"
                      onChange={(e) => setSelectedTag(e.target.value)}
                      MenuProps={{
                        PaperProps: {
                          sx: {
                            maxHeight: 150,
                          },
                        },
                      }}
                    >
                      {userColumnOptions.map(({ label, value }) => (
                        <MenuItem
                          disabled={tags.includes(value)}
                          key={value}
                          value={value}
                        >
                          {label}
                        </MenuItem>
                      ))}
                    </Select>
                    <FormHelperText>
                      <WizardInputHelpText text="Select tags column name" />
                    </FormHelperText>
                  </FormControl>
                  <Button
                    variant="contained"
                    color="primary"
                    disabled={!selectedTag.length}
                    onClick={() => {
                      append(selectedTag);
                      setSelectedTag("");
                    }}
                    sx={{
                      "& .MuiButton-startIcon": {
                        margin: 0,
                      },
                      marginTop: "10px",
                    }}
                    startIcon={<Iconify icon="solar:add-linear" />}
                  />
                </Box>
                <Box
                  sx={{
                    display: "flex",
                    columnGap: 2,
                    rowGap: 1,
                    flexWrap: "wrap",
                  }}
                >
                  {tags.map((tag, index) => {
                    return (
                      <Chip
                        key={tag}
                        variant="soft"
                        color="primary"
                        label={tag}
                        onDelete={() => remove(index)}
                      />
                    );
                  })}
                </Box>
              </Box>
            </SectionCard>
          </Box>
        </Box>
        <Box
          sx={{
            flex: 1,
            overflow: "auto",
            display: "flex",
            flexDirection: "column",
            height: "100%",
            gap: 1,
          }}
        >
          <MappedTable mappedData={mappedData} />
          {renderAlert()}
        </Box>
      </Box>
      <BottomButtons
        onNextClick={handleSubmit(onFormSubmit)}
        onBackClick={() => setActiveStep(2)}
        nextLoading={isSubmitting}
      />
    </>
  );
};

ExtraInfo.propTypes = {
  setActiveStep: PropTypes.func,
  columns: PropTypes.array,
  draftInfo: PropTypes.object,
};

export default ExtraInfo;
