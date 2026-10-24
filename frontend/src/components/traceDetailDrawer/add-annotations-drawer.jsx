import React, { useState } from "react";
import {
  Drawer,
  Button,
  Box,
  List,
  ListItem,
  Typography,
  IconButton,
} from "@mui/material";
import { coveredDrawerSx, useDrawerCovered } from "src/hooks/use-drawer-stack";
import PropTypes from "prop-types";
import Iconify from "../iconify";
import CreateEditLabel from "src/sections/develop-detail/Annotations/CreateEditLabel/CreateEditLabel";
import AnnotationFieldWrapper from "src/sections/develop-detail/Annotations/CreateEditLabel/AnnotationFieldWrapper";
import { useQueryClient } from "@tanstack/react-query";
import { useDeleteAnnotationLabel } from "./useDeleteAnnotationLabel";
import { transformLabelObject } from "src/sections/develop-detail/Annotations/CreateEditLabel/common";
import ConfirmAnnotationDelete from "./ConfirmAnnotationDelete";
import { Events, PropertyName, trackEvent } from "src/utils/Mixpanel";
import { useAnnotationLabels } from "src/api/annotation/annotation";
import { useScrollEnd } from "src/hooks/use-scroll-end";
import AnnotationFieldSkeleton from "src/sections/develop-detail/Annotations/CreateEditLabel/AnnotationFieldSkeleton";
import { ShowComponent } from "../show";
import SvgColor from "../svg-color";

const AddAnnotationsDrawerChild = ({
  onClose,
  onAnnotateClick,
  projectId,
  onAnnotationChanges,
}) => {
  const [openCreateEditLabel, setOpenCreateEditLabel] = useState(false);
  const queryClient = useQueryClient();
  const [editLabel, setEditLabel] = useState(null);
  const [deleteLabelId, setDeleteLabelId] = useState(null);

  const { labels, fetchNextPage, isPending, isFetchingNextPage } =
    useAnnotationLabels(projectId);

  const scrollContainer = useScrollEnd(() => {
    if (isPending || isFetchingNextPage) return;
    fetchNextPage();
  }, [fetchNextPage, isFetchingNextPage, isPending]);

  const { mutate: deleteAnnotationLabel, isPending: isDeletingLabel } =
    useDeleteAnnotationLabel({
      onSuccess: () => {
        setDeleteLabelId(null);
        onAnnotationChanges?.();
      },
    });

  const handleAddNew = () => {
    setOpenCreateEditLabel(true);
  };

  return (
    <>
      <CreateEditLabel
        open={openCreateEditLabel || !!editLabel}
        onClose={() => {
          setOpenCreateEditLabel(false);
          setEditLabel(null);
        }}
        projectId={projectId}
        onSuccess={(_, variables) => {
          queryClient.invalidateQueries({
            queryKey: ["project-annotations-labels", projectId],
          });
          queryClient.invalidateQueries({
            queryKey: ["project-annotations-labels-paginated", projectId],
          });
          trackEvent(Events.projectAnnotation[variables.type], {
            [PropertyName.click]: variables,
          });
          onAnnotationChanges?.();
        }}
        editData={editLabel ? transformLabelObject(editLabel) : null}
      />
      <Box
        sx={{
          display: "flex",
          justifyContent: "space-between",
          padding: 2,
        }}
      >
        <Typography variant="m3" color="text.primary" fontWeight={600}>
          Label Configuration
        </Typography>
        <Box>
          <IconButton onClick={onClose}>
            <Iconify icon="solar:close-linear" width={20} />
          </IconButton>
        </Box>
      </Box>
      <Box
        sx={{
          paddingX: 2,
          display: "flex",
          flexDirection: "column",
          overflowY: "hidden",
        }}
      >
        <Box
          sx={{
            border: "1px solid",
            borderColor: "divider",
            padding: 2,
            borderRadius: "8px",
            flex: 1,
            overflowY: "hidden",
            display: "flex",
            flexDirection: "column",
            gap: 1,
          }}
        >
          <Box sx={{ display: "flex", flexDirection: "column", gap: 1 }}>
            <Typography variant="s1" color="text.primary" fontWeight={500}>
              Labels
            </Typography>
            <Box sx={{ display: "flex", gap: 0.5, alignItems: "center" }}>
              <Typography variant="s2" color="text.secondary">
                These are the annotation labels that an annotator will be able
                to assign to the responses
              </Typography>
            </Box>
          </Box>
          {/* Label List */}
          <List ref={scrollContainer} sx={{ overflow: "auto", flex: 1 }}>
            {labels?.map((item, index) => {
              const transformedItem = transformLabelObject(item);
              return (
                <ListItem
                  key={transformedItem.id}
                  sx={{
                    display: "flex",
                    justifyContent: "space-between",
                    alignItems: "center",
                    padding: "10px 0",
                    gap: 1,
                  }}
                >
                  <AnnotationFieldWrapper
                    index={index}
                    labelName={transformedItem.name}
                    type={transformedItem.type}
                    settings={transformedItem.settings}
                    disableHotkeys={true}
                    collapsable={false}
                  />

                  {/* Edit and Delete Buttons */}
                  <IconButton size="small" onClick={() => setEditLabel(item)}>
                    <SvgColor
                      src="/assets/icons/ic_edit.svg"
                      sx={{ width: 20, height: 20 }}
                    />
                  </IconButton>
                  <IconButton
                    size="small"
                    onClick={() => setDeleteLabelId(item.id)}
                  >
                    <SvgColor
                      src="/assets/icons/ic_delete.svg"
                      sx={{ width: 20, height: 20 }}
                    />
                  </IconButton>
                </ListItem>
              );
            })}
            <ShowComponent condition={isFetchingNextPage || isPending}>
              <AnnotationFieldSkeleton />
              <AnnotationFieldSkeleton />
              <AnnotationFieldSkeleton />
            </ShowComponent>
          </List>

          {/* Add New Label Button */}
          <Button
            variant="outlined" // Use 'outlined' variant for border only
            color="primary"
            onClick={handleAddNew}
            fullWidth
          >
            + Create New Label
          </Button>
        </Box>
      </Box>
      <Box sx={{ flex: 1 }} />
      <Box
        sx={{
          display: "flex",
          justifyContent: "space-between",
          padding: 2,
        }}
      >
        <Button
          variant="outlined"
          color="secondary"
          onClick={onClose} // Close drawer on cancel
          sx={{ width: "48%" }}
        >
          Cancel
        </Button>
        <Button
          variant="contained"
          color="primary"
          sx={{ width: "48%" }}
          onClick={onAnnotateClick}
        >
          Annotate
        </Button>
      </Box>
      <ConfirmAnnotationDelete
        open={Boolean(deleteLabelId)}
        onClose={() => setDeleteLabelId(null)}
        onConfirm={() => deleteAnnotationLabel(deleteLabelId)}
        loading={isDeletingLabel}
      />
    </>
  );
};

AddAnnotationsDrawerChild.propTypes = {
  onClose: PropTypes.func,
  onAnnotateClick: PropTypes.func,
  projectId: PropTypes.string,
  onAnnotationChanges: PropTypes.func,
};

const AddAnnotationsDrawer = ({
  open,
  onClose,
  onAnnotateClick,
  projectId,
  onAnnotationChanges,
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
          width: "45%", // Adjust the width of the drawer
          paddingTop: 0,
        },
      }}
    >
      <AddAnnotationsDrawerChild
        onClose={onClose}
        onAnnotateClick={onAnnotateClick}
        projectId={projectId}
        onAnnotationChanges={onAnnotationChanges}
      />
    </Drawer>
  );
};

// Prop validation
AddAnnotationsDrawer.propTypes = {
  open: PropTypes.bool,
  onClose: PropTypes.func,
  onAnnotateClick: PropTypes.func,
  projectId: PropTypes.string,
  onAnnotationChanges: PropTypes.func,
};

export default AddAnnotationsDrawer;
