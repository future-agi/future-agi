import { Box, Button, Typography } from "@mui/material";
import PropTypes from "prop-types";
import React, { useState } from "react";
import GetStartedDemoVideo from "../../GetStartedDemoVideo";

const CreateDatasetLinks = ({
  links = [],
  heading,
  descriptions,
  buttonTitle,
  buttonAction = () => {},
}) => {
  const [showDemoModal, setShowDemoModal] = useState(null);

  const handleClose = () => {
    setShowDemoModal(null);
  };

  return (
    <Box
      sx={{
        flex: 1,
        overflowY: "auto",
        display: "flex",
        gap: "16px",
        flexDirection: "column",
        justifyContent: "space-between",
      }}
    >
      <Box
        sx={{
          display: "flex",
          gap: "2px",
          flexDirection: "column",
          "& > ul > li::marker": {
            fontSize: "12px",
            color: "text.disabled",
          },
        }}
      >
        <Typography
          variant="s2"
          fontWeight={"fontWeightMedium"}
          color="text.primary"
        >
          {heading}
        </Typography>
        <ul style={{ paddingLeft: "26px", margin: "0" }}>
          {descriptions?.map((item, index) => (
            <li key={index} style={{ margin: 0, padding: 0, lineHeight: 0 }}>
              <Typography
                variant="s2"
                fontWeight={"fontWeightMedium"}
                color="text.secondary"
              >
                {item}
              </Typography>
            </li>
          ))}
        </ul>
      </Box>
      <Box>
        <Box
          sx={{
            marginY: "16px",
            display: "flex",
            gap: "8px",
            flexDirection: "column",
          }}
        >
          {links.map((item, index) => {
            return (
              <Typography
                variant="s2"
                fontWeight={"fontWeightMedium"}
                color="primary.main"
                key={index}
                onClick={() => setShowDemoModal(item)}
                sx={{ textDecoration: "underline", cursor: "pointer" }}
              >
                {item.title}
              </Typography>
            );
          })}
        </Box>
        <Button
          color="primary"
          variant="contained"
          sx={{ maxWidth: "max-content", height: "38px", padding: "8px 24px" }}
          onClick={buttonAction}
        >
          {buttonTitle}
        </Button>
      </Box>
      <GetStartedDemoVideo
        showDemoModal={Boolean(showDemoModal)}
        handleClose={handleClose}
        headingTitle={showDemoModal?.title}
        showAction={false}
        videoComponent={
          <div
            style={{
              position: "relative",
              paddingBottom: `calc(${heading.includes("dataset") ? "45.000%" : "47.400%"} + 41px)`,
              height: 0,
              width: "100%",
            }}
          >
            <iframe
              src={showDemoModal?.link.replace("share", "embed")}
              title={showDemoModal?.title}
              frameBorder="0"
              loading="lazy"
              allow="clipboard-write"
              allowFullScreen
              style={{
                position: "absolute",
                top: 0,
                left: 0,
                width: "100%",
                height: "100%",
                colorScheme: "light",
              }}
            />
          </div>
        }
      />
    </Box>
  );
};

export default CreateDatasetLinks;

CreateDatasetLinks.propTypes = {
  links: PropTypes.array,
  heading: PropTypes.string,
  descriptions: PropTypes.array,
  buttonTitle: PropTypes.string,
  buttonAction: PropTypes.func,
};
