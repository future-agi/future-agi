import { Box, Typography } from "@mui/material";
import React from "react";
import HeaderContent from "./HeaderContent";
import CreateDatasetLinks from "./CreateDatasetLinks";
import { useNavigate } from "react-router";
import PropTypes from "prop-types";
import { createFirstDatasetLinks } from "../../constant";

const CreateFirstDataset = ({ setCurrentLabel }) => {
  const navigate = useNavigate();

  const { links, iframeVideoLinks, descriptions } = createFirstDatasetLinks;

  return (
    <Box>
      <Box sx={{ display: "flex", justifyContent: "space-between" }}>
        <HeaderContent
          title="Create your first dataset"
          description="This tour will guide you through the key features and functionalities"
        />
        <Typography
          variant="s1"
          fontWeight={"fontWeightSemiBold"}
          color="text.disabled"
          sx={{ cursor: "pointer" }}
          onClick={() => setCurrentLabel("createFirstEvaluation")}
        >
          Skip
        </Typography>
      </Box>
      <Box
        sx={{
          display: "flex",
          gap: "16px",
          // height: "330px",
          marginTop: "20px",
          backgroundColor: "background.paper",
        }}
      >
        <Box
          sx={{
            width: "500px",
            height: "100%",
            backgroundColor: "background.neutral",
            borderRadius: "8px",
            overflow: "hidden",
          }}
        >
          <div
            style={{
              position: "relative",
              paddingBottom: "56.25%",
              height: 0,
              width: "100%",
            }}
          >
            <iframe
              src={iframeVideoLinks}
              frameBorder="0"
              loading="lazy"
              allowFullScreen
              style={{
                position: "absolute",
                top: 0,
                left: 0,
                width: "100%",
                height: "100%",
                colorScheme: "light",
              }}
            ></iframe>
          </div>
        </Box>
        <CreateDatasetLinks
          links={links}
          heading="Create your first dataset"
          descriptions={descriptions}
          buttonTitle="Create Dataset"
          targetRoute="/dashboard/develop"
          buttonAction={() => navigate("/dashboard/develop")}
        />
      </Box>
    </Box>
  );
};

export default CreateFirstDataset;

CreateFirstDataset.propTypes = {
  setCurrentLabel: PropTypes.func,
};
