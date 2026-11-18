import { Box, Button, Divider, Typography } from "@mui/material";
import React from "react";
import Iconify from "src/components/iconify";
import SvgColor from "src/components/svg-color";
import PropTypes from "prop-types";
import CurrentPlanButon from "./CurrentPlanButon";
import { HubspotMeetingModalWrapper } from "./HubspotMeetingModalWrapper";

const offers = [
  "Unlimited everything",
  "Dedicated support engineer",
  "Advanced security & compliance (SOC2, HIPAA)",
  "Fined grained RBAC",
  "On premise and Custom deployments",
  "Advanced reporting & analytics",
  "SLA guarantees (private slack channel)",
];

const EnterPrisePricingCard = ({
  currentPlan,
  _redirect,
  meetModalOpen,
  setMeetModalOpen,
}) => {
  return (
    <Box
      flex={1}
      padding={"8px"}
      bgcolor="background.neutral"
      borderRadius={"12px"}
    >
      <Box
        flex={1}
        padding={"30px"}
        display="flex"
        position={"relative"}
        gap={2}
        flexDirection={"column"}
        sx={{
          backgroundColor: "primary.main",
        }}
        borderRadius={"8px"}
      >
        {currentPlan && (
          <Box sx={{ position: "absolute", right: -1, top: -1 }}>
            <CurrentPlanButon planType="custom" />
          </Box>
        )}

        <Box>
          <Iconify
            // @ts-ignore
            icon="solar:buildings-2-linear"
            sx={{
              width: 24,
              height: 24,
              color: "primary.contrastText",
            }}
          />
          <Box display="flex" gap={1} flexDirection={"column"}>
            <Box display="flex" gap={1}>
              <Typography
                color="primary.contrastText"
                typography="m2"
                fontWeight={"fontWeightSemiBold"}
              >
                Enterprise Plan
              </Typography>
            </Box>
            <Typography
              color="primary.contrastText"
              typography="s1"
              fontWeight={"fontWeightRegular"}
            >
              White-glove service with enterprise-grade security and unlimited
              potential
            </Typography>
          </Box>
        </Box>
        <Divider orientation="horizontal" />
        <Box display="flex" flexDirection={"column"} gap={1}>
          {offers.map((item, index) => {
            return (
              <Box key={index} display="flex" gap={1}>
                <Box
                  sx={{
                    backgroundColor: "success.lighter",
                    borderRadius: "50%",
                    height: "22px",
                    width: "22px",
                  }}
                >
                  <SvgColor
                    // @ts-ignore
                    src="/icons/settings/pricing-list-enterprise.svg"
                    sx={{ width: "20px", color: "status.success.fg" }}
                  />
                </Box>
                <Typography
                  color="primary.contrastText"
                  typography="s1"
                  fontWeight={"fontWeightRegular"}
                >
                  {item}
                </Typography>
              </Box>
            );
          })}
        </Box>
        <Box
          display="flex"
          flexDirection={"column"}
          gap={1.5}
          padding={"10px 24px"}
          mt={9}
        >
          <Typography
            color="primary.contrastText"
            typography="l2"
            fontWeight={"fontWeightSemiBold"}
          >
            Custom Pricing
          </Typography>
          <Button
            variant="contained"
            sx={{
              backgroundColor: "background.paper",
              color: "text.primary",
              "&:hover": {
                backgroundColor: "background.default",
                color: "text.primary",
              },
            }}
            onClick={() => setMeetModalOpen(true)}
          >
            Let{"'"}s talk business →
          </Button>
        </Box>
      </Box>
      <HubspotMeetingModalWrapper
        open={meetModalOpen}
        onClose={() => setMeetModalOpen(false)}
      />
    </Box>
  );
};

export default EnterPrisePricingCard;

EnterPrisePricingCard.propTypes = {
  _redirect: PropTypes.func,
  currentPlan: PropTypes.bool,
  meetModalOpen: PropTypes.bool,
  setMeetModalOpen: PropTypes.func,
};
