import ExternalContentFrame from "src/components/external-content/ExternalContentFrame";
import { Box, Button } from "@mui/material";
import React from "react";
import HeaderContent from "./HeaderContent";
import CreateDatasetLinks from "./CreateDatasetLinks";
import { useNavigate } from "react-router";
import PropTypes from "prop-types";
import { createFirstDatasetLinks } from "../../constant";
import ModelRoutes from "../../ModelRoutes";

const CreateFirstDataset = ({ setCurrentLabel }) => {
  const navigate = useNavigate();

  const { links, iframeVideoLinks, descriptions } = createFirstDatasetLinks;

  return (
    <Box>
      <Box sx={{ display: "flex", justifyContent: "space-between" }}>
        <HeaderContent
          title="Create your first dataset"
          description="Create a dataset to hold the examples you test your prompts and models against."
        />
        <Button
          variant="text"
          size="small"
          sx={{ color: "text.secondary" }}
          onClick={() => setCurrentLabel("createFirstEvaluation")}
        >
          Skip
        </Button>
      </Box>
      <ModelRoutes
        sentence="Run prompt columns and synthetic data call a model, and your organization has no provider key of its own."
        onAddProviderKey={() => setCurrentLabel("addKeys")}
      />
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
            <ExternalContentFrame
              title="Create your first dataset"
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
            ></ExternalContentFrame>
          </div>
        </Box>
        <CreateDatasetLinks
          links={links}
          heading="What you can do"
          descriptions={descriptions}
          buttonTitle="Go to Datasets"
          buttonAction={() => navigate("/dashboard/develop")}
          videoPaddingBottom="45.000%"
        />
      </Box>
    </Box>
  );
};

export default CreateFirstDataset;

CreateFirstDataset.propTypes = {
  setCurrentLabel: PropTypes.func,
};
