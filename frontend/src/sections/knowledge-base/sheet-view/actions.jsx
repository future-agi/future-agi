import SelectionBar from "src/components/selection-bar";
import { Box, Button, IconButton, Stack } from "@mui/material";
import PropTypes from "prop-types";
import { useQueryClient } from "@tanstack/react-query";
import React from "react";
import { useNavigate } from "react-router";
import FormSearchField from "src/components/FormSearchField/FormSearchField";
import Iconify from "src/components/iconify";
import { PERMISSIONS, RolePermission } from "src/utils/rolePermissionMapping";
import { useAuthContext } from "src/auth/hooks";

export default function Actions({
  sx,
  selectedFiles,
  setSelectedFiles,
  setSelectedAll,
  gridRef,
  setOpenDeleteDialog,
  setSearchQuery,
  searchQuery,
  status,
  setOpenAddRow,
  knowledgeId,
  setOpenEditDialog,
  selectedAll,
  totalRows,
  excludingIds,
}) {
  const { role } = useAuthContext();
  const navigate = useNavigate();
  const queryClient = useQueryClient();
  const handleActionClick = (action) => {
    switch (action) {
      case "Cancel":
        if (gridRef.current) {
          gridRef.current.api.deselectAll();
          setSelectedFiles([]);
          setSelectedAll(false);
        }
        break;
      case "Delete":
        setOpenDeleteDialog(true);
        break;

      case "create-synthetic-data":
        queryClient.invalidateQueries({
          queryKey: ["knowledge-base"],
        });
        navigate("/dashboard/develop/create-synthetic-dataset", {
          state: {
            // openSyntheticDrawer: true,
            knowledgeId,
          },
        });
        break;
      default:
        break;
    }
  };

  const count = selectedAll
    ? totalRows - excludingIds?.length
    : selectedFiles.length;

  return (
    <Stack
      sx={{
        ...sx,
      }}
      direction={"row"}
      justifyContent={"space-between"}
    >
      <FormSearchField
        size="small"
        placeholder="Search"
        sx={{ minWidth: "360px" }}
        searchQuery={searchQuery}
        onChange={(e) => setSearchQuery(e.target.value)}
      />

      {selectedAll || selectedFiles?.length > 0 ? (
        <SelectionBar
          count={count}
          onCancel={() => handleActionClick("Cancel")}
          sx={{ mr: "5px" }}
          actions={[
            {
              label: "Delete",
              icon: "solar:trash-bin-trash-linear",
              onClick: () => {
                if (RolePermission.KNOWLEDGE_BASE[PERMISSIONS.DELETE][role]) {
                  handleActionClick("Delete");
                }
              },
            },
          ]}
        />
      ) : (
        <Stack direction={"row"} alignItems={"center"} gap={"16px"}>
          {/* <CustomTooltip show={true} title={"Edit Name"} arrow> */}
          <IconButton
            onClick={() => {
              if (RolePermission.KNOWLEDGE_BASE[PERMISSIONS.UPDATE][role]) {
                setOpenEditDialog(true);
              }
            }}
            sx={{
              padding: 0,
              margin: 0,
              width: "16px",
            }}
          >
            <Box component={"img"} src="/assets/icons/ic_pen.svg" />
          </IconButton>
          {/* </CustomTooltip> */}
          <Button
            disabled={status?.status !== "Completed"}
            onClick={() => {
              if (RolePermission.KNOWLEDGE_BASE[PERMISSIONS.CREATE][role]) {
                handleActionClick("create-synthetic-data");
              }
            }}
            sx={{
              typography: "s1",
            }}
            variant="contained"
            color="primary"
          >
            Create Synthetic data
          </Button>
          <Button
            type="button"
            sx={{
              typography: "s1",
            }}
            startIcon={<Iconify icon="solar:add-linear" />}
            variant="outlined"
            color="primary"
            onClick={() => {
              if (RolePermission.KNOWLEDGE_BASE[PERMISSIONS.UPDATE][role]) {
                setOpenAddRow();
              }
            }}
          >
            Add docs
          </Button>
        </Stack>
      )}
    </Stack>
  );
}

Actions.propTypes = {
  sx: PropTypes.object,
  selectedFiles: PropTypes.array,
  setSelectedFiles: PropTypes.array,
  gridRef: PropTypes.object.isRequired,
  setSelectedAll: PropTypes.func,
  setOpenDeleteDialog: PropTypes.func,
  setSearchQuery: PropTypes.func,
  searchQuery: PropTypes.func,
  status: PropTypes.object,
  setOpenAddRow: PropTypes.func,
  knowledgeId: PropTypes.string,
  setOpenEditDialog: PropTypes.func,
  totalRows: PropTypes.number,
  selectedAll: PropTypes.bool,
  excludingIds: PropTypes.array,
};
