import { Box, Typography } from "@mui/material";
import React from "react";
import HeaderContent from "./HeaderContent";
import CreateDatasetLinks from "./CreateDatasetLinks";
import { useNavigate } from "react-router";
import PropTypes from "prop-types";
import { createFirstEvalsLinks } from "../../constant";

const CreateFirstEvaluation = ({ setCurrentLabel }) => {
  const navigate = useNavigate();

  const { links, iframeVideoLinks, descriptions } = createFirstEvalsLinks;

  return (
    <Box>
      <Box sx={{ display: "flex", justifyContent: "space-between" }}>
        <HeaderContent
          title="Create your first evaluation"
          description="This tour will guide you through the key features and functionalities"
        />
        <Typography
          variant="s1"
          fontWeight={"fontWeightSemiBold"}
          color="text.disabled"
          sx={{ cursor: "pointer" }}
          onClick={() => setCurrentLabel("RunFirstExperiment")}
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
              paddingBottom: "calc(53.0625% + 41px)",
              height: 0,
              width: "100%",
            }}
          >
            <iframe
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
          heading="Set Up Your First Evaluation"
          descriptions={descriptions}
          buttonTitle="Create evaluation"
          targetRoute="/dashboard/evaluations"
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
