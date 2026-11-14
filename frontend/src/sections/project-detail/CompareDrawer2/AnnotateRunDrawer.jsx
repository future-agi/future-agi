import React from "react";
import { Drawer, Box, Typography, Button, IconButton } from "@mui/material";
import { coveredDrawerSx, useDrawerCovered } from "src/hooks/use-drawer-stack";
import AnnotationFieldWrapper from "src/sections/develop-detail/Annotations/CreateEditLabel/AnnotationFieldWrapper";
import PropTypes from "prop-types";
import Iconify from "src/components/iconify";
import { transformLabelObject } from "src/sections/develop-detail/Annotations/CreateEditLabel/common";

const AnnotateRunDrawer = ({
  open,
  onClose,
  annotationLabels,
  control,
  runName,
  observationType,
  observationName,
  onSubmit,
}) => {
  const covered = useDrawerCovered(open);
  return (
    <Drawer
      anchor="right"
      open={open}
      onClose={onClose}
      sx={coveredDrawerSx(covered)}
      BackdropProps={{ invisible: true }}
      PaperProps={{
        sx: {
          width: "40%",
          px: 2,
          display: "flex",
          flexDirection: "column",
          height: "100%",
          overflow: "hidden",
        },
      }}
    >
      <form
        onSubmit={onSubmit}
        style={{
          display: "flex",
          flexDirection: "column",
          flex: 1,
          overflow: "hidden",
        }}
      >
        <Box sx={{ flexShrink: 0 }}>
          <Box
            sx={{
              display: "flex",
              justifyContent: "space-between",
              paddingTop: "20px",
            }}
          >
            <Typography variant="h6">Annotate - {runName}</Typography>
            <IconButton onClick={onClose}>
              <Iconify icon="solar:close-linear" width={20} />
            </IconButton>
          </Box>

          <Typography
            sx={{
              mb: 3,
              fontSize: "16px",
              fontWeight: 400,
            }}
          >
            {observationType} - {observationName}
          </Typography>
        </Box>

        <Box
          sx={{
            overflowY: "auto",
            flex: 1,
            py: 1,
          }}
        >
          {annotationLabels?.map((item, index) => {
            const transformedItem = transformLabelObject(item);
            return (
              <Box key={transformedItem.id} mb={2}>
                <AnnotationFieldWrapper
                  index={index}
                  labelName={transformedItem.name}
                  type={transformedItem.type}
                  settings={transformedItem.settings}
                  control={control}
                  fieldName={transformedItem.id}
                  disableHotkeys={true}
                  defaultOpen={true}
                />
              </Box>
            );
          })}
        </Box>

        <Box
          sx={{
            display: "flex",
            gap: 2,
            py: 2,
            flexShrink: 0,
          }}
        >
          <Button
            aria-label="cancel-annotations"
            onClick={onClose}
            variant="outlined"
            fullWidth
            sx={{ flex: 1 }}
          >
            Cancel
          </Button>
          <Button
            aria-label="apply-annotations"
            type="submit"
            color="primary"
            variant="contained"
            fullWidth
            sx={{ flex: 1 }}
          >
            Apply Annotations
          </Button>
        </Box>
      </form>
    </Drawer>
  );
};

AnnotateRunDrawer.propTypes = {
  open: PropTypes.bool,
  onClose: PropTypes.func,
  annotationLabels: PropTypes.array,
  control: PropTypes.any,
  runName: PropTypes.string,
  onSubmit: PropTypes.func,
  observationType: PropTypes.string,
  observationName: PropTypes.string,
};

export default AnnotateRunDrawer;
