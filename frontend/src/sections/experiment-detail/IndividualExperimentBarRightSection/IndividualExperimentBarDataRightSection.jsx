import { Button } from "@mui/material";
import React, { useState } from "react";
import ConfigureAddAsNewDatasetModal from "src/pages/dashboard/models/ConfigureAddAsNewDatasetModal";
import { Events, trackEvent } from "src/utils/Mixpanel";
import { useAuthContext } from "src/auth/hooks";
import { PERMISSIONS, RolePermission } from "src/utils/rolePermissionMapping";

const IndividualExperimentBarDataRightSection = () => {
  const [modalOpen, setModalOpen] = useState(false);
  const { role } = useAuthContext();

  return (
    <>
      <Button
        color="primary"
        variant="contained"
        size="small"
        onClick={() => {
          setModalOpen(true);
          trackEvent(Events.addAsNewDatasetClicked);
        }}
        disabled={!RolePermission.DATASETS[PERMISSIONS.CREATE][role]}
      >
        Add as new dataset
      </Button>
      <ConfigureAddAsNewDatasetModal
        open={modalOpen}
        onClose={() => setModalOpen(false)}
      />
    </>
  );
};

export default IndividualExperimentBarDataRightSection;
