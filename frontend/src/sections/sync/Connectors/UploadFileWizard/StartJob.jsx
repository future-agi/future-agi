import React from "react";
import PropTypes from "prop-types";
import {
  Alert,
  Box,
  Paper,
  Table,
  TableBody,
  TableCell,
  TableContainer,
  TableHead,
  TableRow,
  Typography,
} from "@mui/material";
import { useSearchParams } from "src/routes/hooks";
import { useMutation } from "@tanstack/react-query";
import axios, { endpoints } from "src/utils/axios";
import { enqueueSnackbar } from "src/components/snackbar";

import BottomButtons from "../shared/BottomButtons";

const StartJob = ({
  setActiveStep,
  onClose,
  sourceConfig,
  modelInfo,
  connMappings,
  tags,
}) => {
  const [searchParams] = useSearchParams();

  const draftId = searchParams.draftId;

  const {
    isPending: isSubmitting,
    mutate,
    isError,
    error,
  } = useMutation({
    mutationFn: () =>
      axios.post(`${endpoints.connections.createConnection}`, {
        connectionId: draftId,
      }),
    onSuccess: () => {
      enqueueSnackbar({
        variant: "success",
        message: "Connection Created Successfully",
      });
      onClose();
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

  return (
    <>
      <Box
        sx={{
          display: "flex",
          gap: "8px",
          height: "100%",
          padding: "51px 52px 73px 52px",
          maxHeight: "100%",
          overflowY: "auto",
          flexDirection: "column",
        }}
      >
        <TableContainer
          elevation={0}
          component={Paper}
          sx={{ flex: 1, overflow: "auto" }}
        >
          <Table>
            <TableHead>
              <TableRow>
                <TableCell>Job Summary</TableCell>
              </TableRow>
            </TableHead>
            <TableBody>
              <TableRow>
                <TableCell>
                  <Typography fontSize={13} fontWeight={600} color="primary">
                    CSV File
                  </Typography>
                  <Box
                    sx={{
                      display: "flex",
                      flexDirection: "column",
                      gap: 0.5,
                      paddingY: 1,
                    }}
                  >
                    <InfoItem
                      title="File Name:"
                      value={sourceConfig?.fileName}
                    />
                  </Box>
                  <Typography
                    sx={{ paddingTop: "10px" }}
                    fontSize={13}
                    fontWeight={600}
                    color="primary"
                  >
                    Model Info
                  </Typography>
                  <Box
                    sx={{
                      display: "flex",
                      flexDirection: "column",
                      gap: 0.5,
                      paddingY: 1,
                    }}
                  >
                    <InfoItem
                      title="Model Name:"
                      value={modelInfo?.userModelId}
                    />
                    <InfoItem
                      title="Model Type:"
                      value={modelInfo?.modelType}
                    />
                    <InfoItem
                      title="Environment:"
                      value={modelInfo?.baselineModelEnvironment}
                    />
                    <InfoItem
                      title="Version:"
                      value={modelInfo?.baselineModelVersion}
                    />
                  </Box>
                  <Typography
                    sx={{ paddingTop: "10px" }}
                    fontSize={13}
                    fontWeight={600}
                    color="primary"
                  >
                    Mapping
                  </Typography>
                  <Box
                    sx={{
                      display: "flex",
                      flexDirection: "column",
                      gap: 0.5,
                      paddingY: 1,
                    }}
                  >
                    <InfoItem
                      title="Conversation ID:"
                      value={connMappings?.conversationId}
                    />
                    <InfoItem
                      title="Timestamp:"
                      value={connMappings?.timestamp}
                    />
                    <InfoItem
                      title="Model Input:"
                      value={connMappings?.prompt
                        ?.map((v) => v.columnName)
                        .join(",")}
                    />
                    <InfoItem
                      title="Model Output:"
                      value={connMappings?.response
                        ?.map((v) => v.columnName)
                        .join(",")}
                    />
                    <InfoItem
                      title="Prompt Template:"
                      value={connMappings?.promptTemplate}
                    />
                    <InfoItem
                      title="Variables:"
                      value={connMappings?.variables}
                    />
                    <InfoItem title="Context:" value={connMappings?.context} />
                  </Box>
                  <Typography
                    sx={{ paddingTop: "10px" }}
                    fontSize={13}
                    fontWeight={600}
                    color="primary"
                  >
                    Extra Info
                  </Typography>
                  <Box
                    sx={{
                      display: "flex",
                      flexDirection: "column",
                      gap: 0.5,
                      paddingY: 1,
                    }}
                  >
                    <InfoItem title="Tags:" value={tags} />
                  </Box>
                </TableCell>
              </TableRow>
            </TableBody>
          </Table>
        </TableContainer>
        {renderAlert()}
      </Box>
      <BottomButtons
        onNextClick={() => mutate()}
        onBackClick={() => setActiveStep(3)}
        nextLoading={isSubmitting}
        nextButtonText="Start job"
      />
    </>
  );
};

const InfoItem = ({ title, value }) => {
  return (
    <Box>
      <Typography
        component="span"
        fontSize="13px"
        fontWeight={600}
        color="text.secondary"
      >
        {title}
      </Typography>{" "}
      <Typography component="span" fontSize={13} color="text.primary">
        {value}
      </Typography>
    </Box>
  );
};

InfoItem.propTypes = {
  title: PropTypes.string,
  value: PropTypes.string,
};

StartJob.propTypes = {
  setActiveStep: PropTypes.func,
  onClose: PropTypes.func,
  sourceConfig: PropTypes.object,
  modelInfo: PropTypes.object,
  connMappings: PropTypes.object,
  tags: PropTypes.string,
};

export default StartJob;
