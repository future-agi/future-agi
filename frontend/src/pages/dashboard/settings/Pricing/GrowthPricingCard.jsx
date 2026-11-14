import { Box, Button, Divider, Typography } from "@mui/material";
import React from "react";
import Iconify from "src/components/iconify";
import SvgColor from "src/components/svg-color";
import PropTypes from "prop-types";
import CurrentPlanButon from "./CurrentPlanButon";

const offers = [
  "Unlimited team members",
  "Unlimited traces",
  "Advanced analytics",
  "Priority support",
  "10 GB storage with 180 days data retention",
  "Advanced workflows (evals in CI/CD, AGI x, etc.)",
];

const GrowthPricingCard = ({ currentPlan, redirect }) => {
  return (
    <Box flex={1}>
      <Box
        flex={1}
        padding={"30px"}
        display="flex"
        gap={2}
        position={"relative"}
        borderRadius={"8px"}
        flexDirection={"column"}
        border="1px solid"
        borderColor="divider"
        sx={{ backgroundColor: "background.paper" }}
      >
        <Box>
          <Iconify
            // @ts-ignore
            icon="solar:graph-up-linear"
            sx={{
              width: 24,
              height: 24,
              color: "text.primary",
            }}
          />
          <Box display="flex" gap={1} flexDirection={"column"}>
            <Box display="flex" gap={1} alignItems={"center"}>
              <Typography
                color="text.primary"
                typography="m2"
                fontWeight={"fontWeightSemiBold"}
              >
                Growth Plan
              </Typography>
              <Typography
                sx={{
                  borderRadius: "4px",
                  backgroundColor: "status.success.bg",
                  fontSize: "12px",
                  padding: 0.4,
                  paddingX: 1.5,
                  alignItems: "center",
                  fontWeight: 600,
                  color: "status.success.fg",
                }}
              >
                Most Popular Choice
              </Typography>
            </Box>

            {currentPlan && (
              <Box sx={{ top: 0, position: "absolute", right: 0 }}>
                {" "}
                <CurrentPlanButon />
              </Box>
            )}
            <Typography
              color="text.primary"
              typography="s1"
              fontWeight={"fontWeightRegular"}
            >
              Accelerate your growth with tools that actually work
            </Typography>
          </Box>
        </Box>
        <Divider orientation="horizontal" />
        <Box display="flex" flexDirection={"column"} gap={1}>
          {offers.map((item, index) => {
            return (
              <Box key={index} display="flex" gap={1}>
                <SvgColor
                  // @ts-ignore
                  src="/icons/settings/pricing-list.svg"
                  sx={{ width: "20px", color: "status.success.fg" }}
                />
                <Typography
                  color="text.primary"
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
          mt={4}
        >
          <Typography
            color="text.primary"
            typography="l2"
            fontWeight={"fontWeightSemiBold"}
          >
            Pay as you scale
          </Typography>
          <Button
            variant="contained"
            sx={{
              backgroundColor: "action.hover",
              color: "primary.main",
              "&:hover": {
                backgroundColor: "primary.lighter",
                color: "primary.main",
              },
            }}
            onClick={redirect}
          >
            Fuel your growth →
          </Button>
        </Box>
      </Box>
    </Box>
  );
};

export default GrowthPricingCard;

GrowthPricingCard.propTypes = {
  redirect: PropTypes.func,
  currentPlan: PropTypes.bool,
};
