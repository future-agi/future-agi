import { Box, Button, Card, Typography } from "@mui/material";
import React, { useState } from "react";
import CustomTooltip from "src/components/tooltip";
import PropTypes from "prop-types";
import ConfigureDefaultDataset from "./ConfigureDefaultDataset";

const ModelBaselineCard = ({ modelDetails }) => {
  const { isDatasetAdded, baselineModelEnvironment, baselineModelVersion } =
    modelDetails;

  const [isConfigureModalOpen, setIsConfigureModalOpen] = useState(false);

  const isDefaultModelConfigured =
    Boolean(baselineModelEnvironment) && Boolean(baselineModelVersion);

  const renderConfigText = () => {
    if (!isDatasetAdded) return "Add dataset to configure default dataset.";

    if (!isDefaultModelConfigured)
      return "Your model’s default dataset is not configured.";

    return (
      <>
        Your model’s default dataset is{" "}
        <Typography variant="body2" sx={{ fontWeight: 700 }} component="span">
          {baselineModelEnvironment} {baselineModelVersion}
        </Typography>
      </>
    );
  };

  return (
    <Card>
      <Box
        sx={{
          padding: 2.5,
          display: "flex",
          justifyContent: "space-between",
          borderBottom: 1,
          borderColor: "divider",
          alignItems: "center",
        }}
      >
        <Box sx={{ display: "flex", alignItems: "center", gap: 0.5 }}>
          <Typography variant="subtitle1" sx={{ fontWeight: 700 }}>
            Dataset Config
          </Typography>
          {/* <CustomTooltip title="Informative text" placement="top" arrow>
            <Iconify icon="eva:info-fill" sx={{ color: "divider" }} />
          </CustomTooltip> */}
        </Box>
        <CustomTooltip
          show={!isDatasetAdded}
          title="Add dataset to configure default dataset"
          placement="top"
          arrow
        >
          <Box>
            <Button
              color="primary"
              variant="contained"
              disabled={!isDatasetAdded}
              onClick={() => {
                setIsConfigureModalOpen(true);
                // trackEvent(Events.configConfigureDefaultDatasetStart);
              }}
            >
              Configure Default Dataset
            </Button>
          </Box>
        </CustomTooltip>
      </Box>
      <Typography variant="body2" sx={{ p: 2.5, pt: 2 }}>
        {renderConfigText()}
      </Typography>
      <ConfigureDefaultDataset
        open={isConfigureModalOpen}
        onClose={() => setIsConfigureModalOpen(false)}
        modelDetails={modelDetails}
      />
    </Card>
  );
};

ModelBaselineCard.propTypes = {
  modelDetails: PropTypes.object,
};

export default ModelBaselineCard;
