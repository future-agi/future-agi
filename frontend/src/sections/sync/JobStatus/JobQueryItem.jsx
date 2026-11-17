import React from "react";
import PropTypes from "prop-types";
import { Alert, Box, Chip, Typography } from "@mui/material";
import { format } from "date-fns";
import _ from "lodash";

const JobQueryItem = ({ jobQuery }) => {
  return (
    <Box
      sx={{
        paddingY: "12px",
        borderBottom: "1px dotted",
        borderColor: "divider",
      }}
    >
      <Box sx={{ display: "flex" }}>
        <Box sx={{ flex: 1, display: "flex", flexDirection: "column", gap: 2 }}>
          <Typography fontSize={13} color="text.primary">
            Query ID : {jobQuery.id}
          </Typography>
          <Typography fontSize={13} color="text.primary">
            No.of rows processed: {jobQuery.rowsProcessed}
          </Typography>
        </Box>
        <Box sx={{ flex: 1, display: "flex", flexDirection: "column", gap: 2 }}>
          <Typography fontSize={13} color="text.primary">
            Runtime :{" "}
            {format(new Date(jobQuery.created_at), "dd MMM yyyy hh:mm:ss aa")}
          </Typography>
          <Box>
            <JobStatusChip status={jobQuery?.status} />
          </Box>
        </Box>
      </Box>
      {jobQuery.status === "SYNC_FAILED" && (
        <Alert sx={{ marginTop: "24px" }} variant="standard" severity="error">
          {jobQuery?.errorMsg}
        </Alert>
      )}
    </Box>
  );
};

const JobStatusChip = ({ status }) => {
  switch (status) {
    case "SYNC_SUCCEEDED":
      return (
        <Chip size="small" variant="soft" label="Successful" color="success" />
      );
    case "SYNC_FAILED":
      return <Chip size="small" variant="soft" label="Failed" color="error" />;
    case "SYNC_PENDING":
      return (
        <Chip size="small" variant="soft" label="Pending" color="warning" />
      );
    default:
      return <Chip size="small" variant="soft" label={_.capitalize(status)} />;
  }
};

JobStatusChip.propTypes = {
  status: PropTypes.string,
};

JobQueryItem.propTypes = {
  jobQuery: PropTypes.object,
};

export default JobQueryItem;
