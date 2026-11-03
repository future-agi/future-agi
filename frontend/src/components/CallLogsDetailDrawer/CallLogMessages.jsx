import React from "react";
import PropTypes from "prop-types";
import { Box, Stack, Typography } from "@mui/material";
import CustomJsonViewer from "../custom-json-viewer/CustomJsonViewer";

const CallLogMessages = ({ data }) => {
  const messages = data?.messages ?? [];
  return (
    <Stack
      gap={1}
      overflow="auto"
      flexGrow={1}
      border="1px solid"
      borderColor="divider"
      borderRadius="4px"
      width={"100%"}
      p={1}
    >
      {messages.length === 0 ? (
        <Stack minHeight={200} alignItems={"center"} justifyContent={"center"}>
          <Typography typography="s2_1" fontWeight={"fontWeightMedium"}>
            Messages are empty - <i>{data?.endedReason}</i>
          </Typography>
        </Stack>
      ) : (
        messages?.map((message, index) => (
          <Stack key={index} gap={1}>
            <Stack direction="row" gap={2}>
              <Typography typography={"s2"} fontWeight={"fontWeightMedium"}>
                Message {index + 1}
              </Typography>
              <Typography typography={"s2"}>{message.role}</Typography>
            </Stack>
            <Box
              key={index}
              sx={{
                backgroundColor: "background.paper",
                borderRadius: "4px",
                fontSize: "13px",
                whiteSpace: "pre-wrap",
                maxHeight: "72vh",
                overflow: "auto",
                position: "relative",
                minWidth: "100%",
                border: "1px solid",
                borderColor: "divider",
                p: 1,
              }}
            >
              <CustomJsonViewer object={message} />
            </Box>
          </Stack>
        ))
      )}
    </Stack>
  );
};

export default CallLogMessages;

CallLogMessages.propTypes = {
  data: PropTypes.object.isRequired,
};
