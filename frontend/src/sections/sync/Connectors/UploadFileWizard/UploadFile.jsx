import { Alert, Box, IconButton, Input, TextField } from "@mui/material";
import React, { useState } from "react";
import Iconify from "src/components/iconify";
import SvgColor from "src/components/svg-color";
import PropTypes from "prop-types";
import { useMutation, useQueryClient } from "@tanstack/react-query";
import axios, { endpoints } from "src/utils/axios";
import { useSearchParams } from "src/routes/hooks";
import { BigQueryMappedDummyData } from "src/utils/constant";

import SectionCard from "../shared/SectionCard";
import WizardInputHelpText from "../shared/WizardInputHelpText";
import MappedTable from "../shared/MappedTable";
import BottomButtons from "../shared/BottomButtons";

const UploadFile = ({ setActiveStep, onClose }) => {
  const queryClient = useQueryClient();

  const [searchParams] = useSearchParams();

  const [selectedFile, setSelectedFile] = useState(null);

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

  const handleFileUpload = (event) => {
    event.preventDefault();
    const file = event.target.files[0];
    setSelectedFile(file);
    setTestingStatus({
      alertOpen: false,
      isTested: false,
      status: null,
    });
  };

  const getJsonFileName = () => {
    if (!selectedFile) {
      return undefined;
    }

    return (
      <Box sx={{ display: "flex", gap: 1, alignItems: "center" }}>
        <SvgColor
          src={`/assets/icons/navbar/ic_file.svg`}
          sx={{ color: "primary.main" }}
        />
        {selectedFile.name}
      </Box>
    );
  };

  const draftId = searchParams.draftId;

  const handleTestConnection = () => {
    const formData = new FormData();
    formData.append("file", selectedFile);
    formData.append("definitions_name", "UploadFile");
    formData.append("connection_id", draftId);

    testConnection(formData);
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
          Your file upload is successful— Click next
        </Alert>
      );
    } else if (testingStatus.isTested && testingStatus.status === "fail") {
      return (
        <Alert
          variant="standard"
          severity="error"
          onClose={() => setTestingStatus((e) => ({ ...e, alertOpen: false }))}
        >
          There is some issue with the uploaded file— Try again!
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
            <SectionCard title="Upload CSV file">
              <Box
                sx={{
                  padding: "20px",
                }}
              >
                <Input
                  type="file"
                  inputProps={{ accept: ".csv" }}
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
                  label="CSV file"
                  fullWidth
                  // error={!!jsonFormState.error?.message}
                  helperText={<WizardInputHelpText text="Upload CSV file" />}
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
        onTestClick={() => handleTestConnection()}
        isNextDisabled={testingStatus.status !== "success"}
        testLoading={isTestingConnection}
      />
    </>
  );
};

UploadFile.propTypes = {
  setActiveStep: PropTypes.func,
  onClose: PropTypes.func,
};

export default UploadFile;
