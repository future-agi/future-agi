import {
  Alert,
  Box,
  IconButton,
  Input,
  TextField,
  Typography,
} from "@mui/material";
import React, { useState } from "react";
import Iconify from "src/components/iconify";
import { useController, useForm } from "react-hook-form";
import SvgColor from "src/components/svg-color";
import PropTypes from "prop-types";
import { zodResolver } from "@hookform/resolvers/zod";
import { useMutation, useQueryClient } from "@tanstack/react-query";
import axios, { endpoints } from "src/utils/axios";
import { useSearchParams } from "src/routes/hooks";
import { BigQueryMappedDummyData } from "src/utils/constant";
import FormTextFieldV2 from "src/components/FormTextField/FormTextFieldV2";

import BottomButtons from "../shared/BottomButtons";
import WizardInputHelpText from "../shared/WizardInputHelpText";
import SectionCard from "../shared/SectionCard";
import MappedTable from "../shared/MappedTable";

import { TablePermissionValidationSchema } from "./validation";
import { logger } from "@sentry/react";

const TablePermission = ({ setActiveStep, onClose }) => {
  const { control } = useForm({
    defaultValues: { tableId: "", credentialsJson: null },
    resolver: zodResolver(TablePermissionValidationSchema),
  });

  const queryClient = useQueryClient();

  const [searchParams] = useSearchParams();

  const [fileName, setFileName] = useState("");

  const [testingStatus, setTestingStatus] = useState({
    isTested: false,
    status: null,
    alertOpen: false,
  });

  const { mutate: testConnection, isPending: isTestingConnection } =
    useMutation({
      mutationFn: (d) => {
        return axios.post(endpoints.connectors.testConnection, d);
      },
      onSuccess: () => {
        setTestingStatus({
          isTested: true,
          status: "success",
          alertOpen: true,
        });
      },
      onError: () => {
        setTestingStatus({ isTested: true, status: "fail", alertOpen: true });
      },
    });

  const { field: jsonField, fieldState: jsonFormState } = useController({
    control,
    name: "credentialsJson",
  });

  const handleFileUpload = (event) => {
    const file = event.target.files[0];
    setFileName(file.name);
    const reader = new FileReader();
    setTestingStatus({
      alertOpen: false,
      isTested: false,
      status: null,
    });
    reader.onload = (e) => {
      try {
        const json = e.target.result;
        jsonField.onChange(json);
      } catch (error) {
        logger.error("Error parsing JSON:", error);
      }
    };
    reader.readAsText(file);
  };

  const getJsonFileName = () => {
    if (!jsonField.value) {
      return undefined;
    }

    return (
      <Box sx={{ display: "flex", gap: 1, alignItems: "center" }}>
        <SvgColor
          src={`/assets/icons/navbar/ic_file.svg`}
          sx={{ color: "primary.main" }}
        />
        {fileName}
      </Box>
    );
  };

  const draftId = searchParams.draftId;

  const handleTestConnection = (formValues) => {
    // const formData = new FormData()
    const result = {
      table_id: formValues.tableId,
      credentials_json: JSON.parse(formValues.credentialsJson),
      definitions_name: "BigQuery",
      connection_id: draftId,
    };
    // Object.keys(result).forEach(key => formData.append(key, object[key]));
    testConnection(result);
  };

  const renderAlert = () => {
    if (!testingStatus.alertOpen) {
      return <></>;
    }
    if (testingStatus.isTested && testingStatus.status === "success") {
      return (
        <Alert
          variant="filled"
          severity="success"
          onClose={() => setTestingStatus((e) => ({ ...e, alertOpen: false }))}
        >
          Your connection to BigQuery is successful— Click next
        </Alert>
      );
    } else if (testingStatus.isTested && testingStatus.status === "fail") {
      return (
        <Alert
          variant="standard"
          severity="error"
          onClose={() => setTestingStatus((e) => ({ ...e, alertOpen: false }))}
        >
          Your connection to BigQuery has failed— Try again!
        </Alert>
      );
    }
  };

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
            <SectionCard title="BigQuery Permission Setup">
              <Box
                sx={{
                  padding: "20px",
                  display: "flex",
                  flexDirection: "column",
                  gap: 1,
                }}
              >
                <Typography variant="caption" color="text.secondary">
                  In order to ingest your data, you will need to grant access
                </Typography>
                <Typography
                  component="a"
                  href="https://google.com"
                  variant="caption"
                  color="primary.main"
                  sx={{ display: "flex", alignItems: "center", gap: 1 }}
                >
                  BigQuery setup guide
                  <Iconify icon="solar:arrow-right-up-linear" />
                </Typography>
              </Box>
            </SectionCard>
            <SectionCard title="Dataset  Configuration">
              <Box
                sx={{
                  padding: "20px",
                }}
              >
                <FormTextFieldV2
                  label="Table ID"
                  fullWidth
                  control={control}
                  fieldName="tableId"
                  placeholder="Enter table id"
                  onChange={() => {
                    setTestingStatus({
                      alertOpen: false,
                      isTested: false,
                      status: null,
                    });
                  }}
                  helperText={
                    <Box>
                      <Box>
                        <Typography
                          variant="caption"
                          color="text.primary"
                          component="span"
                          fontWeight={500}
                        >
                          Dataset:{" "}
                        </Typography>
                        <Typography
                          variant="caption"
                          color="text.secondary"
                          component="span"
                        >
                          example-dataset,
                        </Typography>
                      </Box>
                      <Box>
                        <Typography
                          variant="caption"
                          color="text.primary"
                          component="span"
                          fontWeight={500}
                        >
                          Table Name:{" "}
                        </Typography>
                        <Typography
                          variant="caption"
                          color="text.secondary"
                          component="span"
                        >
                          example-table
                        </Typography>
                      </Box>
                      <Box>
                        <Typography
                          variant="caption"
                          color="text.primary"
                          component="span"
                          fontWeight={500}
                        >
                          GCP Project ID:{" "}
                        </Typography>
                        <Typography
                          variant="caption"
                          color="text.secondary"
                          component="span"
                        >
                          gcp-project-id
                        </Typography>
                      </Box>
                    </Box>
                  }
                />
              </Box>
            </SectionCard>
            <SectionCard title="Permission Configuration">
              <Box
                sx={{
                  padding: "20px",
                }}
              >
                <Input
                  type="file"
                  inputProps={{ accept: ".json" }}
                  onChange={handleFileUpload}
                  style={{ display: "none" }}
                  id="upload-input"
                />
                <TextField
                  disabled
                  InputProps={{
                    startAdornment: getJsonFileName(),
                    endAdornment: (
                      <>
                        <label htmlFor="upload-input">
                          <IconButton color="primary" component="span">
                            <Iconify
                              width={24}
                              icon="solar:upload-minimalistic-linear"
                              sx={{ color: "primary.main" }}
                            />
                          </IconButton>
                        </label>
                      </>
                    ),
                  }}
                  label="JSON permission"
                  fullWidth
                  error={!!jsonFormState.error?.message}
                  helperText={
                    jsonFormState.error?.message || (
                      <WizardInputHelpText text="Upload JSON file" />
                    )
                  }
                />
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
          <MappedTable
            mappedData={
              testingStatus.status === "success"
                ? BigQueryMappedDummyData
                : null
            }
          />
          {renderAlert()}
        </Box>
      </Box>
      <BottomButtons
        onNextClick={() => {
          queryClient.invalidateQueries({
            queryKey: ["draft", draftId],
            type: "all",
          });
          setActiveStep(1);
        }}
        onCancelClick={() => onClose()}
        onTestClick={control.handleSubmit(handleTestConnection)}
        isNextDisabled={testingStatus.status !== "success"}
        testLoading={isTestingConnection}
      />
    </>
  );
};

TablePermission.propTypes = {
  setActiveStep: PropTypes.func,
  onClose: PropTypes.func,
};

export default TablePermission;
