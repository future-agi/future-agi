import { Box, Button, IconButton, Typography } from "@mui/material";
import PropTypes from "prop-types";
import React from "react";
import Iconify from "../iconify";
import { format } from "date-fns";
import CallStatus from "src/sections/test/CallLogs/CallStatus";
import { formatDuration } from "src/utils/format-time";
import { useCallLogsSideDrawerStore } from "./store";
import SvgColor from "../svg-color";
import { handleOnDocsClicked } from "src/utils/Mixpanel";

const CallLogsDetailDrawerHeaderSection = ({ data, onAnnotate }) => {
  const { setCallLogsSideDrawerData } = useCallLogsSideDrawerStore();

  const Separator = () => (
    <Typography
      typography="s2_1"
      color="text.disabled"
      fontWeight="fontWeightRegular"
      sx={{ mx: 0.5 }}
    >
      |
    </Typography>
  );
  return (
    <Box
      sx={{
        display: "flex",
        flexDirection: "row",
        justifyContent: "space-between",
        borderBottom: "1px solid",
        borderColor: "divider",
        paddingBottom: 2,
        paddingTop: 2,
      }}
    >
      <Box display={"flex"} flexDirection={"column"} gap={1} paddingLeft={2}>
        {/* <Stack bgcolor="green.50" p={1} borderRadius={1}>
            {data?.type === CallType.INBOUND ? (
              <Iconify
                icon="solar:phone-linear"
                sx={{ width: 24, height: 24, color: "blue.600" }}
              />
            ) : (
              <Iconify
                icon="solar:phone-linear"
                sx={{ width: 24, height: 24, color: "green.500" }}
              />
            )}
          </Stack> */}
        <Typography typography="m3" fontWeight="fontWeightSemiBold">
          Call Log Details
        </Typography>
        <Box
          sx={{
            display: "flex",
            alignItems: "center",
            flexWrap: "wrap",
            gap: 0.5,
          }}
        >
          {data?.phone_number && (
            <>
              <Typography
                typography="s2_1"
                color="text.disabled"
                fontWeight="fontWeightRegular"
              >
                {data?.phone_number}
              </Typography>

              <Separator />
            </>
          )}
          {data?.timestamp && (
            <>
              <Typography
                typography="s2_1"
                color="text.disabled"
                fontWeight="fontWeightRegular"
              >
                {data?.timestamp &&
                  format(new Date(data?.timestamp), "yyyy-MM-dd HH:mm:ss")}
              </Typography>
              <Separator />
            </>
          )}
          {data?.duration_seconds && (
            <>
              <Box sx={{ display: "flex", alignItems: "center", gap: 0.5 }}>
                <Iconify
                  icon="solar:clock-circle-linear"
                  width="14px"
                  height="14px"
                  color="text.disabled"
                />
                <Typography
                  typography="s2_1"
                  color="text.disabled"
                  fontWeight="fontWeightRegular"
                >
                  {formatDuration(data?.duration_seconds)}
                </Typography>
              </Box>

              <Separator />
            </>
          )}
          <CallStatus value={data?.status ?? ""} />
        </Box>
      </Box>

      <Box
        sx={{
          display: "flex",
          flexDirection: "row",
          alignItems: "center",
          alignSelf: "flex-start",
          gap: 1,
          paddingRight: 2,
        }}
      >
        {onAnnotate && (
          <Button
            variant="soft"
            size="small"
            onClick={onAnnotate}
            startIcon={<Iconify icon="solar:pen-linear" width={16} />}
            sx={{
              color: "text.secondary",
              backgroundColor: "action.hover",
            }}
          >
            Annotate
          </Button>
        )}
        <Button
          variant="outlined"
          size="small"
          sx={{
            flexShrink: 0,
          }}
          onClick={() => {
            handleOnDocsClicked("call-log-observe");
            window.open(
              "https://docs.futureagi.com/docs/api/run-tests/createruntest",
              "_blank",
            );
          }}
        >
          <SvgColor
            src="/assets/icons/agent/docs.svg"
            sx={{ height: 16, width: 16, mr: 1 }}
          />
          <Typography typography="s2" fontWeight="fontWeightMedium">
            View Docs
          </Typography>
        </Button>

        <IconButton
          onClick={() => setCallLogsSideDrawerData(null)}
          sx={{
            color: "text.primary",
          }}
        >
          <Iconify icon="solar:close-linear" />
        </IconButton>
      </Box>
    </Box>
  );
};

CallLogsDetailDrawerHeaderSection.propTypes = {
  data: PropTypes.object.isRequired,
  onAnnotate: PropTypes.func,
};

export default CallLogsDetailDrawerHeaderSection;
