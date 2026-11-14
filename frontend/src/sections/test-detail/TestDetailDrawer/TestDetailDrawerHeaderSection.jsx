import { Box, Button, IconButton, Typography } from "@mui/material";
import React from "react";
import Iconify from "../../../components/iconify";
import PropTypes from "prop-types";
import { formatDuration } from "src/utils/format-time";
import { format } from "date-fns";
import CallStatus from "../../test/CallLogs/CallStatus";
import { useTestDetailSideDrawerStoreShallow } from "../states";
import SvgColor from "src/components/svg-color";
import { handleOnDocsClicked } from "src/utils/Mixpanel";

const TestDetailDrawerHeaderSection = ({ data }) => {
  const { setTestDetailDrawerOpen } = useTestDetailSideDrawerStoreShallow(
    (state) => ({
      setTestDetailDrawerOpen: state.setTestDetailDrawerOpen,
    }),
  );

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
        {/* <Box>
          {data?.type === CallType.INBOUND ? (
            <Iconify
              icon="solar:phone-linear"
              sx={{ width: 24, height: 24, color: "status.info.fg" }}
            />
          ) : (
            <Iconify
              icon="solar:chat-round-line-linear"
              sx={{ width: 24, height: 24, color: "status.success.fg" }}
            />
          )}
        </Box> */}
        <Typography typography="m3" fontWeight="fontWeightSemiBold">
          Call Log Details
        </Typography>
        <Box
          sx={{
            display: "flex",
            alignItems: "center",
            flexWrap: "wrap",
            gap: 0.5, // controls consistent gap between items
          }}
        >
          {data?.customer_name && (
            <>
              <Typography
                typography="s2_1"
                color="text.disabled"
                fontWeight="fontWeightRegular"
              >
                {data?.customer_name}
              </Typography>

              <Separator />
            </>
          )}
          {data?.scenario && (
            <>
              <Typography
                typography="s2_1"
                color="text.disabled"
                fontWeight="fontWeightRegular"
              >
                {data?.scenario}
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
          {data?.duration && (
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
                  {formatDuration(data?.duration)}
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
        <Button
          variant="outlined"
          size="small"
          sx={{
            flexShrink: 0,
          }}
          onClick={() => {
            handleOnDocsClicked("call-log-simulate");
            window.open("https://docs.futureagi.com/docs/simulation", "_blank");
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
          onClick={() => setTestDetailDrawerOpen(null)}
          sx={{
            color: "text.primary",
            p: 0.5, // ✅ reduce padding if needed to align better
          }}
        >
          <Iconify icon="solar:close-linear" />
        </IconButton>
      </Box>
    </Box>
  );
};

TestDetailDrawerHeaderSection.propTypes = {
  data: PropTypes.object.isRequired,
};

export default TestDetailDrawerHeaderSection;
