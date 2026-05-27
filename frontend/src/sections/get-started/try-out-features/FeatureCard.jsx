import {
  Box,
  Button,
  Divider,
  Link,
  Typography,
  useTheme,
} from "@mui/material";
import PropTypes from "prop-types";
import React from "react";
import { useNavigate } from "react-router";
import { useAuthContext } from "src/auth/hooks";
import Image from "src/components/image";
import {
  buildCurrentFlowContext,
  CurrentFlowEvents,
  getRouteFamily,
  trackCurrentFlow,
} from "src/utils/analytics/currentFlow";

const FeatureCard = ({
  title = "",
  description,
  imageUrl = "",
  imageUrlDark = "",
  buttonTitle,
  learnMoreLink,
  actionLink,
}) => {
  const navigate = useNavigate();
  const { user } = useAuthContext();
  const theme = useTheme();
  const isDark = theme.palette.mode === "dark";

  const handleFeatureClick = () => {
    const context = buildCurrentFlowContext({ user });
    trackCurrentFlow(CurrentFlowEvents.currentFlowTryFeatureClicked, {
      ...context,
      event_source: "get_started_feature_card",
      feature_title: title,
      button_text: buttonTitle,
      target_route: actionLink,
      target_route_family: getRouteFamily(actionLink),
    });
    trackCurrentFlow(
      CurrentFlowEvents.currentFlowFirstValueCandidate,
      {
        ...context,
        event_source: "get_started_feature_card",
        candidate_type: "try_feature_card",
        feature_title: title,
        target_route: actionLink,
        target_route_family: getRouteFamily(actionLink),
      },
      {
        onceKeyParts: [
          "firstValueCandidate",
          user?.default_workspace_id,
          user?.id,
        ],
      },
    );
    navigate(actionLink);
  };

  return (
    <Box
      bgcolor={"background.paper"}
      border={`1px solid`}
      sx={{ borderColor: "divider" }}
      borderRadius={"8px"}
      width={"33%"}
      display={"flex"}
      flexDirection={"column"}
      gap={"16px"}
      overflow={"hidden"}
    >
      <Image
        alt={title}
        src={isDark && imageUrlDark ? imageUrlDark : imageUrl}
        sx={{ width: "100%" }}
      />
      <Box
        px={"16px"}
        pb={"16px"}
        sx={{
          display: "flex",
          flexDirection: "column",
          justifyContent: "space-between",
          gap: "12px",
          flex: 1,
        }}
      >
        <Box
          sx={{
            display: "flex",
            flexDirection: "column",
            // justifyContent: "space-between",
            gap: "2px",
          }}
        >
          <Typography
            variant="s2"
            fontWeight={"fontWeightSemiBold"}
            color="text.secondary"
          >
            {title}
          </Typography>
          <Typography
            variant="s1"
            fontWeight={"fontWeightRegular"}
            color="text.disabled"
          >
            {description}
          </Typography>
        </Box>
        <Box>
          <Divider />
          <Box
            display={"flex"}
            justifyContent={"space-between"}
            sx={{ alignItems: "center", pt: "16px" }}
          >
            <Link
              underline="none"
              href={learnMoreLink}
              target="_blank"
              variant="s1"
              fontWeight={"fontWeightSemiBold"}
              color="primary.main"
              sx={{
                border: "1px solid",
                borderColor: "primary.main",
                borderRadius: "8px",
                height: "38px",
                padding: "8px 24px",
                "&:hover": {
                  color: "primary.dark",
                },
              }}
            >
              Learn more
            </Link>
            <Button
              color="primary"
              variant="contained"
              onClick={handleFeatureClick}
              sx={{ height: "38px", padding: "8px 24px" }}
            >
              <Typography
                variant="s1"
                fontWeight={"fontWeightSemiBold"}
                color="background.paper"
              >
                {buttonTitle}
              </Typography>
            </Button>
          </Box>
        </Box>
      </Box>
    </Box>
  );
};

export default FeatureCard;

FeatureCard.propTypes = {
  title: PropTypes.string.isRequired,
  description: PropTypes.string.isRequired,
  buttonTitle: PropTypes.string.isRequired,
  navigateTo: PropTypes.string,
  imageUrl: PropTypes.string,
  imageUrlDark: PropTypes.string,
  learnMoreLink: PropTypes.string,
  actionLink: PropTypes.string,
};
