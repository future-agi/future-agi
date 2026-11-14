import React from "react";
import { Box, Button, Typography } from "@mui/material";
import Iconify from "src/components/iconify";
import PropTypes from "prop-types";
import { planData } from "./constant";
import { HubspotMeetingModalWrapper } from "./HubspotMeetingModalWrapper";

const PlanBreakdown = ({ redirectGrowth, setMeetModalOpen, meetModalOpen }) => {
  return (
    <Box display="flex" flexDirection={"column"} gap={6}>
      <Box
        display="flex"
        gap={1.5}
        sx={{
          backgroundColor: "background.paper",
          marginRight: "-40px",
          marginLeft: "-40px",
          position: "sticky",
          top: -15,
          padding: 2,
        }}
      >
        <Box
          sx={{
            width: "calc(25% - 6px)",
            display: "flex",
            justifyContent: "center",
            flexDirection: "column",
            gap: 1,
          }}
        >
          <Typography
            typography="l3"
            color="text.primary"
            fontWeight={"fontWeightSemiBold"}
          >
            Plan Breakdown
          </Typography>
          <Typography
            typography="s1"
            color="text.primary"
            fontWeight={"fontWeightRegular"}
          >
            Compare plans and select the one that best fits your needs
          </Typography>
        </Box>
        <Box
          sx={{
            mt: 2,
            width: "calc(25% - 6px)",
            display: "flex",
            justifyContent: "center",
            flexDirection: "column",
            alignItems: "center",
            gap: 1,
          }}
        >
          <Typography
            typography="l3"
            color="text.primary"
            fontWeight={"fontWeightSemiBold"}
          >
            Starter Plan
          </Typography>
        </Box>
        <Box
          sx={{
            mt: 2,
            width: "calc(25% - 6px)",
            display: "flex",
            justifyContent: "center",
            flexDirection: "column",
            alignItems: "center",
            gap: 2,
          }}
        >
          <Typography
            typography="l3"
            color="text.primary"
            fontWeight={"fontWeightSemiBold"}
          >
            Growth Plan
          </Typography>
          <Button
            variant="contained"
            color="primary"
            size="small"
            onClick={redirectGrowth}
          >
            Start Growth Plan
          </Button>
        </Box>
        <Box
          sx={{
            mt: 2,
            width: "calc(25% - 6px)",
            display: "flex",
            justifyContent: "center",
            flexDirection: "column",
            alignItems: "center",
            gap: 2,
          }}
        >
          <Typography
            typography="l3"
            color="text.primary"
            fontWeight={"fontWeightSemiBold"}
          >
            Enterprise Plan
          </Typography>
          <Button
            variant="contained"
            color="primary"
            size="small"
            onClick={() => {
              setMeetModalOpen(true);
            }}
          >
            Contact Sales
          </Button>
        </Box>
      </Box>
      {planData.map((item, index) => {
        return (
          <Box key={index}>
            <Box>
              <Box
                sx={{
                  width: "calc(25% - 6px)",
                  display: "flex",
                  justifyContent: "center",
                  flexDirection: "column",
                  gap: 1,
                  marginBottom: "4px",
                }}
              >
                <Typography
                  typography="l3"
                  color="text.primary"
                  fontWeight={"fontWeightSemiBold"}
                >
                  {item.section}
                </Typography>
              </Box>
            </Box>
            {item.features.map((temp, ind) => {
              return (
                <Box key={`${index}-${ind}`} display="flex" gap={1.5}>
                  <RenderValue value={temp.name} heading />
                  <RenderValue value={temp.free} />
                  <RenderValue value={temp.growth} />
                  <RenderValue value={temp.enterprise} />
                </Box>
              );
            })}
          </Box>
        );
      })}
      <HubspotMeetingModalWrapper
        open={meetModalOpen}
        onClose={() => {
          setMeetModalOpen(false);
        }}
      />
    </Box>
  );
};

export default PlanBreakdown;

PlanBreakdown.propTypes = {
  redirectGrowth: PropTypes.func,
  meetModalOpen: PropTypes.bool,
  setMeetModalOpen: PropTypes.func,
};

const RenderValue = ({ value, heading }) => {
  return (
    <Box
      sx={{
        width: "calc(25% - 6px)",
        display: "flex",
        justifyContent: "center",
        alignItems: heading ? "flex-start" : "center",
        gap: 1,
      }}
    >
      <Box
        sx={{
          padding: 1,
          paddingLeft: heading ? 0 : 1,
          borderBottom: "1px solid",
          borderColor: "divider",
          width: heading ? "100%" : "90%",
          display: "flex",
        }}
      >
        {!heading && <span>&nbsp;&nbsp;</span>}
        {typeof value === "boolean" ? (
          <Iconify
            // @ts-ignore
            icon={value ? "solar:check-linear" : "solar:close-linear"}
            sx={{
              width: 20,
              height: 20,
              color: value ? "status.success.fg" : "status.danger.fg",
            }}
          />
        ) : (
          <Typography
            typography="s1"
            color="text.primary"
            fontWeight={heading ? "fontWeightMedium" : "fontWeightRegular"}
          >
            {value}
          </Typography>
        )}
      </Box>
    </Box>
  );
};

RenderValue.propTypes = {
  value: PropTypes.oneOfType([PropTypes.string, PropTypes.bool]),
  heading: PropTypes.bool,
};
