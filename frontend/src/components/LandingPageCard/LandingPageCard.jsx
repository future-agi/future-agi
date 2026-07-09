import React from "react";
import { Box, Button, Typography, useTheme } from "@mui/material";
import PropTypes from "prop-types";
import Image from "../image";
import { ShowComponent } from "../show";
import { LoadingButton } from "@mui/lab";

const LandingPageCard = ({
  title,
  description,
  image,
  loading = false,
  showAction = false,
  availableFeature = false,
  disabled = false,
}) => {
  const theme = useTheme();

  return (
    <Box
      sx={{
        minWidth: "250px",
        borderRadius: "12px",
        textAlign: "left",
        border: "1px solid",
        borderColor: "divider",
        overflow: "hidden",
        height: "100%",
        justifyContent: "space-between",
        display: "flex",
        flexDirection: "column",
      }}
    >
      <Box>
        <Box sx={{ marginBottom: "16px", overflow: "hidden" }}>
          <Image
            alt={title}
            src={image}
            sx={{
              width: "100%",
              height: "100%",
              filter:
                theme.palette.mode === "dark"
                  ? "brightness(0.9) invert(1) hue-rotate(180deg)"
                  : "none",
            }}
          />
        </Box>
        <Box
          sx={{
            padding: "12px",
            paddingTop: "0px",
            display: "flex",
            flexDirection: "column",
            gap: "4px",
          }}
        >
          <Typography
            variant="m3"
            fontWeight={"fontWeightMedium"}
            color="text.primary"
          >
            {title}
          </Typography>
          <Typography
            variant="s1"
            fontWeight={"fontWeightRegular"}
            color="text.secondary"
          >
            {description}
          </Typography>
        </Box>
      </Box>
      <ShowComponent condition={showAction}>
        <Box
          sx={{
            display: "flex",
            justifyContent: "space-between",
            padding: "12px",
            paddingTop: "0px",
            gap: "8px",
          }}
        >
          <ShowComponent condition={availableFeature}>
            <LoadingButton
              variant="contained"
              color="primary"
              loading={loading}
              disabled={disabled}
            >
              Get started
            </LoadingButton>
          </ShowComponent>
          <ShowComponent condition={!availableFeature}>
            <Button
              variant="contained"
              color="primary"
              disabled
              sx={{ "&.Mui-disabled": { color: "text.primary" } }}
              type="buttom"
            >
              Coming soon
            </Button>
          </ShowComponent>
        </Box>
      </ShowComponent>
    </Box>
  );
};

LandingPageCard.propTypes = {
  title: PropTypes.string,
  description: PropTypes.string,
  image: PropTypes.string,
  loading: PropTypes.bool,
  showAction: PropTypes.bool,
  availableFeature: PropTypes.bool,
  disabled: PropTypes.bool,
};

export default LandingPageCard;
