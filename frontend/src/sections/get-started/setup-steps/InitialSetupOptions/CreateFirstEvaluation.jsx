import ExternalContentFrame from "src/components/external-content/ExternalContentFrame";
import { Box, Button } from "@mui/material";
import React from "react";
import HeaderContent from "./HeaderContent";
import CreateDatasetLinks from "./CreateDatasetLinks";
import { useNavigate } from "react-router";
import PropTypes from "prop-types";
import { createFirstEvalsLinks } from "../../constant";
import ModelRoutes from "../../ModelRoutes";

const CreateFirstEvaluation = ({ setCurrentLabel }) => {
  const navigate = useNavigate();

  const { links, iframeVideoLinks, descriptions } = createFirstEvalsLinks;

  return (
    <Box>
      <Box sx={{ display: "flex", justifyContent: "space-between" }}>
        <HeaderContent
          title="Create your first evaluation"
          description="Create an evaluation to score your model outputs automatically instead of checking each one by hand."
        />
        <Button
          variant="text"
          size="small"
          sx={{ color: "text.secondary" }}
          onClick={() => setCurrentLabel("RunFirstExperiment")}
        >
          Skip
        </Button>
      </Box>
      <ModelRoutes
        sentence="Evaluations can call a model you pick, and your organization has no provider key of its own."
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
              paddingBottom: "calc(53.0625% + 41px)",
              height: 0,
              width: "100%",
            }}
          >
            <ExternalContentFrame
              src={iframeVideoLinks}
              title="Evaluation: Future AGI"
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
        </Box>
        <CreateDatasetLinks
          links={links}
          heading="What you can do"
          descriptions={descriptions}
          buttonTitle="Go to Evals"
          buttonAction={() => navigate("/dashboard/evaluations")}
        />
      </Box>
    </Box>
  );
};

export default CreateFirstEvaluation;

CreateFirstEvaluation.propTypes = {
  setCurrentLabel: PropTypes.func,
};
