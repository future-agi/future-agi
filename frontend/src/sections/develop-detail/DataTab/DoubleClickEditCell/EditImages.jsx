import {
  Box,
  CircularProgress,
  DialogActions,
  Typography,
  IconButton,
} from "@mui/material";
import PropTypes from "prop-types";
import React, { useEffect, useRef, useState } from "react";
import ErrorMessage from "./ErrorMessage";
import { LoadingButton } from "@mui/lab";
import SvgColor from "src/components/svg-color";
import { ShowComponent } from "src/components/show";
import { enqueueSnackbar } from "notistack";
import DeleteMediaDialog from "./ConfirmDelete";
import { useDropzone } from "react-dropzone";
import GridIcon from "src/components/gridIcon/GridIcon";
import {
  DndContext,
  closestCenter,
  PointerSensor,
  useSensor,
  useSensors,
} from "@dnd-kit/core";
import {
  SortableContext,
  useSortable,
  rectSortingStrategy,
  arrayMove,
} from "@dnd-kit/sortable";
import { CSS } from "@dnd-kit/utilities";

const createImageItem = (url) => ({
  id: crypto.randomUUID(),
  url,
});

const normalizeImageItems = (value) => {
  if (!value) {
    return [];
  }

  const values = Array.isArray(value) ? value : [value];
  return values.map((url) => createImageItem(url));
};

// Sortable Image Item Component
const SortableImageItem = ({ item, index, onDelete }) => {
  const {
    attributes,
    listeners,
    setNodeRef,
    transform,
    transition,
    isDragging,
  } = useSortable({ id: item.id });

  const style = {
    transform: CSS.Transform.toString(transform),
    transition,
    opacity: isDragging ? 0.5 : 1,
    zIndex: isDragging ? 1000 : 1,
  };

  return (
    <Box
      ref={setNodeRef}
      style={style}
      sx={{
        position: "relative",
        aspectRatio: "1",
        "&:hover .delete-btn": {
          opacity: 1,
        },
        "&:hover .drag-handle": {
          opacity: 1,
        },
      }}
    >
      <Box
        {...attributes}
        {...listeners}
        className="drag-handle"
        sx={{
          position: "absolute",
          top: 4,
          left: 4,
          opacity: 0,
          backgroundColor: "rgba(0,0,0,0.6)",
          borderRadius: "4px",
          padding: "2px",
          cursor: "grab",
          transition: "opacity 0.2s",
          zIndex: 2,
          display: "flex",
          alignItems: "center",
          justifyContent: "center",
        }}
      >
        <SvgColor
          src="/assets/icons/components/ic_drag.svg"
          sx={{ width: 16, height: 16, color: "common.white" }}
        />
      </Box>
      <GridIcon
        src={item.url}
        alt={`Image ${index + 1}`}
        sx={{
          width: "100%",
          height: "100%",
          objectFit: "cover",
          borderRadius: "8px",
          cursor: "grab",
        }}
      />
      <IconButton
        className="delete-btn"
        size="small"
        onClick={(e) => {
          e.stopPropagation();
          onDelete(item.id);
        }}
        sx={{
          position: "absolute",
          top: 4,
          right: 4,
          opacity: 0,
          backgroundColor: "rgba(0,0,0,0.6)",
          transition: "opacity 0.2s",
          zIndex: 2,
          "&:hover": {
            backgroundColor: "rgba(0,0,0,0.8)",
          },
        }}
      >
        <SvgColor
          src="/assets/icons/components/ic_delete.svg"
          sx={{ width: 16, height: 16, color: "common.white" }}
        />
      </IconButton>
    </Box>
  );
};

SortableImageItem.propTypes = {
  item: PropTypes.shape({
    id: PropTypes.string.isRequired,
    url: PropTypes.string.isRequired,
  }).isRequired,
  index: PropTypes.number.isRequired,
  onDelete: PropTypes.func.isRequired,
};

const EditImages = ({ params, onClose, onCellValueChanged }) => {
  const fileInputRef = useRef(null);
  const [imageItems, setImageItems] = useState([]);
  const [error, setError] = useState("");
  const [isLoading, setIsLoading] = useState(false);
  const [isDeleteDialogOpen, setIsDeleteDialogOpen] = useState(false);
  const [deleteImageId, setDeleteImageId] = useState(null);

  // DnD sensors
  const sensors = useSensors(
    useSensor(PointerSensor, {
      activationConstraint: {
        distance: 5,
      },
    }),
  );

  // Parse initial value
  useEffect(() => {
    if (params?.value) {
      try {
        const parsed =
          typeof params.value === "string"
            ? JSON.parse(params.value)
            : params.value;
        setImageItems(normalizeImageItems(parsed));
      } catch {
        setImageItems(normalizeImageItems(params.value));
      }
    } else {
      setImageItems([]);
    }
  }, [params?.value]);

  const handleDragEnd = (event) => {
    const { active, over } = event;

    if (!over || active.id === over.id) {
      return;
    }

    setImageItems((items) => {
      const oldIndex = items.findIndex((item) => item.id === active.id);
      const newIndex = items.findIndex((item) => item.id === over.id);

      if (oldIndex === -1 || newIndex === -1) {
        return items;
      }

      return arrayMove(items, oldIndex, newIndex);
    });
  };

  const handleClose = () => {
    onClose();
  };

  const handleButtonClick = () => {
    fileInputRef?.current?.click();
  };

  const onSubmit = (e) => {
    e.preventDefault();
    try {
      const newValue =
        imageItems.length > 0
          ? JSON.stringify(imageItems.map((item) => item.url))
          : null;
      onCellValueChanged({
        ...params,
        newValue,
        onSuccess: () => {
          enqueueSnackbar("Images have been updated", { variant: "success" });
        },
      });
      handleClose();
    } catch (err) {
      setError(
        err?.errors?.[0]?.message ||
          "An error occurred while saving the images",
      );
    }
  };

  const handleConfirmDelete = () => {
    if (deleteImageId !== null) {
      setImageItems((prev) => prev.filter((item) => item.id !== deleteImageId));
    }
    setIsDeleteDialogOpen(false);
    setDeleteImageId(null);
  };

  const handleDeleteAll = () => {
    setImageItems([]);
    setIsDeleteDialogOpen(false);
    setDeleteImageId(null);
  };

  const processFiles = (files) => {
    setIsLoading(true);
    const validFiles = files.filter((file) => {
      const maxSize = 5 * 1024 * 1024;
      if (file.size > maxSize) {
        enqueueSnackbar(`${file.name} exceeds 5MB limit`, { variant: "error" });
        return false;
      }
      return true;
    });

    if (validFiles.length === 0) {
      setIsLoading(false);
      return;
    }

    let processed = 0;
    const newImageItems = [];

    validFiles.forEach((file) => {
      const reader = new FileReader();
      reader.onloadend = () => {
        newImageItems.push(createImageItem(reader.result));
        processed++;
        if (processed === validFiles.length) {
          setImageItems((prev) => [...prev, ...newImageItems]);
          setIsLoading(false);
        }
      };
      reader.onerror = () => {
        enqueueSnackbar(`Failed to read file: ${file.name}`, {
          variant: "error",
        });
        processed++;
        if (processed === validFiles.length) {
          setImageItems((prev) => [...prev, ...newImageItems]);
          setIsLoading(false);
        }
      };
      reader.readAsDataURL(file);
    });
  };

  const onDrop = (acceptedFiles) => {
    if (acceptedFiles.length > 0) {
      processFiles(acceptedFiles);
    }
  };

  const { getRootProps, getInputProps, isDragActive } = useDropzone({
    onDrop,
    onDropRejected: () => {
      enqueueSnackbar(
        "Unsupported format. Supported formats: JPEG, PNG, WebP, BMP, TIFF",
        { variant: "error" },
      );
    },
    accept: {
      "image/jpeg": [],
      "image/png": [],
      "image/webp": [],
      "image/bmp": [],
      "image/tiff": [],
    },
    multiple: true,
    noClick: false,
    noKeyboard: true,
  });

  const handleImageChange = (event) => {
    const files = Array.from(event?.target?.files || []);
    if (files.length > 0) {
      processFiles(files);
    }
  };

  const handleDeleteClick = (imageId) => {
    setDeleteImageId(imageId);
    setIsDeleteDialogOpen(true);
  };

  const containerStyles = {
    display: "grid",
    gridTemplateColumns: "repeat(auto-fill, minmax(120px, 1fr))",
    gap: "12px",
    padding: "16px",
    minHeight: "200px",
    maxHeight: "400px",
    overflowY: "auto",
    backgroundColor: isDragActive ? "action.hover" : "transparent",
    border: "2px dashed",
    borderColor: isDragActive ? "primary.main" : "divider",
    borderRadius: "8px",
    transition: "all 0.2s ease",
  };

  const sortableItems = imageItems.map((item) => item.id);

  const deleteImageIndex =
    deleteImageId !== null
      ? imageItems.findIndex((item) => item.id === deleteImageId)
      : -1;

  return (
    <>
      <Box
        sx={{
          padding: "16px",
          paddingTop: "0px",
          display: "flex",
          flexDirection: "column",
          gap: "10px",
        }}
        component="form"
        onSubmit={onSubmit}
      >
        <Box
          sx={{
            border: "1px solid",
            borderColor: "divider",
            borderRadius: "8px",
            padding: "12px",
            backgroundColor: "background.paper",
          }}
        >
          <ShowComponent condition={isLoading}>
            <Box
              sx={{
                ...containerStyles,
                display: "flex",
                justifyContent: "center",
                alignItems: "center",
              }}
            >
              <CircularProgress />
            </Box>
          </ShowComponent>

          <ShowComponent condition={!isLoading && imageItems.length === 0}>
            <Box {...getRootProps()} sx={containerStyles}>
              <input {...getInputProps()} />
              <Box
                sx={{
                  gridColumn: "1 / -1",
                  display: "flex",
                  flexDirection: "column",
                  justifyContent: "center",
                  alignItems: "center",
                  minHeight: "150px",
                  gap: 2,
                }}
              >
                <img
                  src="/assets/placeholder.svg"
                  alt="No images placeholder"
                  style={{
                    width: "80px",
                    height: "80px",
                    opacity: 0.6,
                  }}
                />
                <Box sx={{ textAlign: "center" }}>
                  <Typography
                    variant="body1"
                    fontWeight={600}
                    color="text.secondary"
                    sx={{ mb: 0.5 }}
                  >
                    No images added
                  </Typography>
                  <Typography variant="body2" color="text.secondary">
                    Click or drag images here to upload
                  </Typography>
                </Box>
              </Box>
            </Box>
          </ShowComponent>

          <ShowComponent condition={!isLoading && imageItems.length > 0}>
            <DndContext
              sensors={sensors}
              collisionDetection={closestCenter}
              onDragEnd={handleDragEnd}
            >
              <SortableContext
                items={sortableItems}
                strategy={rectSortingStrategy}
              >
                <Box sx={containerStyles}>
                  {imageItems.map((item, index) => (
                    <SortableImageItem
                      key={item.id}
                      item={item}
                      index={index}
                      onDelete={handleDeleteClick}
                    />
                  ))}
                </Box>
              </SortableContext>
            </DndContext>
          </ShowComponent>

          {error && (
            <ErrorMessage isError={Boolean(error)} errorMessage={error} />
          )}

          <Box
            sx={{
              display: "flex",
              justifyContent: "space-between",
              flexWrap: "wrap",
              gap: 2,
              mt: 1,
            }}
          >
            <Box>
              <Typography variant="body2" color="text.secondary">
                {imageItems.length} image{imageItems.length !== 1 ? "s" : ""}
              </Typography>
              {imageItems.length > 1 && (
                <Typography variant="caption" color="text.secondary">
                  Drag to reorder
                </Typography>
              )}
            </Box>

            <Box sx={{ display: "flex", gap: 1 }}>
              <LoadingButton
                size="small"
                startIcon={
                  <SvgColor
                    src="/assets/icons/components/ic_replace.svg"
                    sx={{ width: 24, height: 24, color: "text.primary" }}
                  />
                }
                onClick={handleButtonClick}
                sx={{
                  color: "text.primary",
                  fontSize: "14px",
                  fontWeight: 400,
                }}
              >
                Add More
                <input
                  type="file"
                  accept="image/jpeg,image/png,image/webp,image/bmp,image/tiff"
                  multiple
                  ref={fileInputRef}
                  style={{ display: "none" }}
                  onChange={handleImageChange}
                />
              </LoadingButton>
              {imageItems.length > 0 && (
                <LoadingButton
                  size="small"
                  startIcon={
                    <SvgColor
                      src="/assets/icons/components/ic_delete.svg"
                      sx={{ width: 24, height: 24, color: "text.primary" }}
                    />
                  }
                  onClick={() => {
                    setDeleteImageId(null);
                    setIsDeleteDialogOpen(true);
                  }}
                  sx={{
                    color: "text.primary",
                    fontSize: "14px",
                    fontWeight: 400,
                  }}
                >
                  Delete All
                </LoadingButton>
              )}
            </Box>
          </Box>
        </Box>

        <DialogActions sx={{ justifyContent: "flex-end", padding: 0 }}>
          <LoadingButton
            variant="contained"
            type="submit"
            size="medium"
            sx={{
              backgroundColor: "primary.main",
              "&:hover": { backgroundColor: "primary.main" },
              borderRadius: "10px",
              fontSize: "14px",
              fontWeight: 600,
              width: "205px",
            }}
          >
            <SvgColor
              src="/assets/icons/components/ic_save.svg"
              sx={{
                width: 20,
                height: 20,
                mr: 1,
                color: "divider",
              }}
            />
            Save
          </LoadingButton>
        </DialogActions>
      </Box>

      <DeleteMediaDialog
        open={isDeleteDialogOpen}
        onClose={() => {
          setIsDeleteDialogOpen(false);
          setDeleteImageId(null);
        }}
        onDelete={deleteImageId !== null ? handleConfirmDelete : handleDeleteAll}
        isPending={false}
        fileName={
          deleteImageId !== null && deleteImageIndex !== -1
            ? `Image ${deleteImageIndex + 1}`
            : "all images"
        }
        fileType="image"
      />
    </>
  );
};

export default EditImages;

EditImages.propTypes = {
  params: PropTypes.object,
  onClose: PropTypes.func,
  onCellValueChanged: PropTypes.func,
};
