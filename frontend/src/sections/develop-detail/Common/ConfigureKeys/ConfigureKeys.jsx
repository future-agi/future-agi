import {
  Box,
  Dialog,
  IconButton,
  InputAdornment,
  LinearProgress,
  TextField,
  Typography,
} from "@mui/material";
import PropTypes from "prop-types";
import React, { useState } from "react";
import Iconify from "src/components/iconify";
import KeyCard from "./KeyCard";
import { useApiKeysStatus } from "src/api/model/api-keys";
import { ShowComponent } from "src/components/show";
import {
  useDeleteApiKey,
  DELETE_MODAL_TYPE,
} from "src/hooks/use-delete-api-key";
import { ConfirmDialog } from "src/components/custom-dialog";
import { LoadingButton } from "@mui/lab";
import SvgColor from "../../../../components/svg-color/svg-color";

const ConfigureKeys = ({ open, onClose }) => {
  const { data, isLoading, isFetching } = useApiKeysStatus({
    enabled: open,
  });

  const [searchQuery, setSearchQuery] = useState("");

  // Function to filter keys based on displayName
  const filteredData = data?.filter((d) =>
    d.display_name.toLowerCase().includes(searchQuery.toLowerCase()),
  );

  const closeModal = () => {
    onClose();
    setSearchQuery("");
  };

  const {
    openDeleteModal,
    setOpenDeleteModal,
    handleDeleteApiKey,
    isDeleting,
  } = useDeleteApiKey();

  return (
    <>
      <Dialog
        open={open}
        onClose={closeModal}
        maxWidth={false}
        PaperProps={{ sx: { maxWidth: 960 } }}
        fullWidth
      >
        <Box sx={{ display: "flex", flexDirection: "column", height: "90vh" }}>
          {/* Sticky Header with Search Bar */}
          <Box
            sx={{
              display: "flex",
              flexDirection: "column",
              gap: 2.2,
              position: "sticky",
              top: 0,
              zIndex: 10,
              backgroundColor: "background.paper",
              paddingX: 3,
              paddingTop: 1.5,
              paddingBottom: 3,
            }}
          >
            {/* Header */}
            <Box
              sx={{
                display: "flex",
                justifyContent: "space-between",
                alignItems: "center",
              }}
            >
              <Typography fontWeight={600} fontSize="16px">
                API Key Configuration
              </Typography>
              <IconButton
                onClick={closeModal}
                size="small"
                sx={{ marginRight: "-15px" }}
              >
                <Iconify icon="solar:close-linear" />
              </IconButton>
            </Box>

            {/* Search Bar */}
            <TextField
              fullWidth
              size="small"
              placeholder="Search"
              value={searchQuery}
              onChange={(e) => setSearchQuery(e.target.value)} // Update state on input
              sx={{ width: "100%" }}
              InputProps={{
                startAdornment: (
                  <InputAdornment position="start">
                    <Iconify
                      icon="solar:magnifier-linear" width={16}
                      sx={{ color: "text.disabled" }}
                    />
                  </InputAdornment>
                ),
              }}
            />
          </Box>

          {isLoading && <LinearProgress />}

          <ShowComponent condition={!isLoading}>
            <Box
              sx={{
                border: "1px solid",
                borderColor: "divider",
                borderRadius: "8px",
                backgroundColor: "action.hover",
                padding: "13px",
                marginX: 3,
                marginBottom: 3,
                display: "grid",
                gridTemplateColumns: "repeat(2, minmax(250px, 1fr))",
                gap: filteredData?.length === 1 ? "50px" : "18px 13px",
                alignContent: "start",
                placeContent: "start",
                overflowY: "auto",
                flexGrow: 1,
                minHeight: "300px",
              }}
            >
              {filteredData?.length > 0 ? (
                filteredData.map((d, index) => (
                  <KeyCard
                    key={index}
                    data={d}
                    onClose={onClose}
                    isFetching={isFetching}
                    onDeleteClick={() =>
                      setOpenDeleteModal({
                        id: d?.id,
                        type: DELETE_MODAL_TYPE.NORMAL,
                      })
                    }
                  />
                ))
              ) : (
                <Typography
                  sx={{ textAlign: "center", gridColumn: "span 2", padding: 2 }}
                >
                  No API keys found.
                </Typography>
              )}
            </Box>
          </ShowComponent>
        </Box>
      </Dialog>
      <ConfirmDialog
        content="Are you sure you want to delete this API key?"
        action={
          <LoadingButton
            loading={isDeleting}
            size="small"
            variant="contained"
            color="error"
            onClick={() => handleDeleteApiKey()}
            sx={{ color: "common.white" }}
            startIcon={
              <SvgColor
                width={2}
                // @ts-ignore
                sx={{ mt: -0.5 }}
                src={"/assets/icons/ic_delete.svg"}
              />
            }
          >
            Delete
          </LoadingButton>
        }
        open={!!openDeleteModal}
        onClose={() => setOpenDeleteModal(null)}
        title="Delete API key"
      />
    </>
  );
};

ConfigureKeys.propTypes = {
  open: PropTypes.bool,
  onClose: PropTypes.func,
};

export default ConfigureKeys;
