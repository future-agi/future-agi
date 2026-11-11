import EmptyContent from "src/components/empty-content";
import React from "react";
import { useTheme } from "@mui/material";
import { LoadingButton } from "@mui/lab";
import PropTypes from "prop-types";
import Iconify from "src/components/iconify";
import { useAuthContext } from "src/auth/hooks";
import { PERMISSIONS, RolePermission } from "src/utils/rolePermissionMapping";

const EmptyCard = ({
  tab,
  setCurrentTab,
  action,
  datasetId,
  icon = "",
  title = "",
  description = "",
}) => {
  const theme = useTheme();
  const handleAddEvaluation = () => {
    setCurrentTab(tab == "annotation" ? "annotations" : "data");
    action();
  };
  const { role } = useAuthContext();

  // Swap to dark variant if available
  const resolvedIcon =
    theme.palette.mode === "dark" && icon
      ? icon.replace(/\.svg$/, "-dark.svg")
      : icon;

  return (
    <EmptyContent
      img={resolvedIcon || undefined}
      title={title}
      description={description}
      sx={{ width: "100%" }}
      action={
        !datasetId ? (
          <LoadingButton
            size="small"
            variant="contained"
            color="primary"
            type="submit"
            onClick={handleAddEvaluation}
            disabled={!RolePermission.DATASETS[PERMISSIONS.UPDATE][role]}
            startIcon={
              <Iconify icon="solar:add-linear" width="16px" height="16px" />
            }
          >
            Add {tab}
          </LoadingButton>
        ) : undefined
      }
    />
  );
};

EmptyCard.propTypes = {
  datasetId: PropTypes.any,
  tab: PropTypes.string,
  setCurrentTab: PropTypes.func.isRequired,
  action: PropTypes.func,
  icon: PropTypes.string,
  title: PropTypes.string,
  description: PropTypes.string,
};
export default EmptyCard;
