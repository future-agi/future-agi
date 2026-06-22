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

// ---------------------------------------------------------------------------
// Helper: wrap a plain URL string into a stable { id, url } object.
// crypto.randomUUID() is available in all modern browsers with no extra deps.
// ---------------------------------------------------------------------------
const toItem = (url) => ({ id: crypto.randomUUID(), url });

// ---------------------------------------------------------------------------
// SortableImageItem
// Receives the whole `item` object ({ id, url }) so the sortable id is the
// stable UUID — never the array index.
// ---------------------------------------------------------------------------
const SortableImageItem = ({ item, onDelete }) => {
  const {
    attributes,
    listeners,
    setNodeRef,
    transform,
    transition,
    isDragging,
  } = useSortable({ id: item.id }); // ← stable UUID, not "image-N"

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
        "&:hover .delete-btn": { opacity: 1 },
        "&:hover .drag-handle": { opacity: 1 },
      }}
    >
      {/* Drag handle */}
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
        alt={`Image`}
        sx={{
          width: "100%",
          height: "100%",
          objectFit: "cover",
          borderRadius: "8px",
          cursor: "grab",
        }}
      />

      {/* Delete button — fires with stable id, not index */}
      <IconButton
        className="delete-btn"
        size="small"
        onClick={(e) => {
          e.stopPropagation();
          onDelete(item.id); // ← delete by id, not by array position
        }}
        sx={{
          position: "absolute",
          top: 4,
          right: 4,
          opacity: 0,
          backgroundColor: "rgba(0,0,0,0.6)",
          transition: "opacity 0.2s",
          zIndex: 2,
          "&:hover": { backgroundColor: "rgba(0,0,0,0.8)" },
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
  onDelete: PropTypes.func.isRequired,
};

// ---------------------------------------------------------------------------
// EditImages
// ---------------------------------------------------------------------------
const EditImages = ({ params, onClose, onCellValueChanged }) => {
  const fileInputRef = useRef(null);

  // State is now { id: string, url: string }[] instead of string[].
  // The persisted value (params.value) is still a plain JSON string[],
  // so we wrap on load and unwrap on save.
  const [imageItems, setImageItems] = useState([]);

  const [error, setError] = useState("");
  const [isLoading, setIsLoading] = useState(false);
  const [isDeleteDialogOpen, setIsDeleteDialogOpen] = useState(false);

  // Tracks which item id is pending deletion (null = "delete all" mode).
  const [deleteId, setDeleteId] = useState(null);

  // DnD sensors — require 5px movement before a drag starts so that
  // click events (delete button) still fire normally.
  const sensors = useSensors(
    useSensor(PointerSensor, {
      activationConstraint: { distance: 5 },
    }),
  );

  // ---------------------------------------------------------------------------
  // Parse initial value — wrap each URL with a stable UUID on mount.
  // ---------------------------------------------------------------------------
  useEffect(() => {
    if (params?.value) {
      try {
        const parsed =
          typeof params.value === "string"
            ? JSON.parse(params.value)
            : params.value;
        const urls = Array.isArray(parsed) ? parsed : [parsed];
        setImageItems(urls.map(toItem));
      } catch {
        setImageItems([toItem(params.value)]);
      }
    }
  }, [params?.value]);

  // ---------------------------------------------------------------------------
  // Drag end — locate positions by stable id (mirrors ColumnConfigureDropDown).
  // ---------------------------------------------------------------------------
  const handleDragEnd = (event) => {
    const { active, over } = event;
    if (!over || active.id === over.id) return;

    setImageItems((items) => {
      const oldIndex = items.findIndex((it) => it.id === active.id);
      const newIndex = items.findIndex((it) => it.id === over.id);
      if (oldIndex === -1 || newIndex === -1) return items;
      return arrayMove(items, oldIndex, newIndex);
    });
  };

  const handleClose = () => onClose();

  const handleButtonClick = () => fileInputRef?.current?.click();

  // ---------------------------------------------------------------------------
  // Save — strip the id wrappers; persisted value stays a plain url string[].
  // ---------------------------------------------------------------------------
  const onSubmit = (e) => {
    e.preventDefault();
    try {
      const plainUrls = imageItems.map((it) => it.url);
      const newValue = plainUrls.length > 0 ? JSON.stringify(plainUrls) : null;
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

  // ---------------------------------------------------------------------------
  // Delete single — filter by stable id, never by array index.
  // ---------------------------------------------------------------------------
  const handleConfirmDelete = () => {
    if (deleteId !== null) {
      setImageItems((prev) => prev.filter((it) => it.id !== deleteId));
    }
    setIsDeleteDialogOpen(false);
    setDeleteId(null);
  };

  const handleDeleteAll = () => {
    setImageItems([]);
    setIsDeleteDialogOpen(false);
    setDeleteId(null);
  };

  // ---------------------------------------------------------------------------
  // File processing — new uploads also get stable UUIDs.
  // ---------------------------------------------------------------------------
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
    const newItems = [];

    validFiles.forEach((file) => {
      const reader = new FileReader();

      reader.onloadend = () => {
        newItems.push(toItem(reader.result)); // ← wrap with UUID immediately
        processed++;
        if (processed === validFiles.length) {
          setImageItems((prev) => [...prev, ...newItems]);
          setIsLoading(false);
        }
      };

      reader.onerror = () => {
        enqueueSnackbar(`Failed to read file: ${file.name}`, {
          variant: "error",
        });
        processed++;
        if (processed === validFiles.length) {
          setImageItems((prev) => [...prev, ...newItems]);
          setIsLoading(false);
        }
      };

      reader.readAsDataURL(file);
    });
  };

  const onDrop = (acceptedFiles) => {
    if (acceptedFiles.length > 0) processFiles(acceptedFiles);
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
    if (files.length > 0) processFiles(files);
  };

  // Opens delete-single dialog — stores id (not index).
  const handleDeleteClick = (id) => {
    setDeleteId(id);
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

  // Derive the label shown in the delete dialog from deleteId.
  const deleteLabel = (() => {
    if (deleteId === null) return "all images";
    const idx = imageItems.findIndex((it) => it.id === deleteId);
    return idx !== -1 ? `Image ${idx + 1}` : "image";
  })();

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
          {/* Loading state */}
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

          {/* Empty state with drag-and-drop zone */}
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
                  style={{ width: "80px", height: "80px", opacity: 0.6 }}
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

          {/* Image grid with drag-to-reorder */}
          <ShowComponent condition={!isLoading && imageItems.length > 0}>
            <DndContext
              sensors={sensors}
              collisionDetection={closestCenter}
              onDragEnd={handleDragEnd}
            >
              {/*
                items={} receives stable UUIDs — dnd-kit can now track
                each image across renders even after a reorder.
              */}
              <SortableContext
                items={imageItems.map((it) => it.id)}
                strategy={rectSortingStrategy}
              >
                <Box sx={containerStyles}>
                  {imageItems.map((item) => (
                    <SortableImageItem
                      key={item.id}       // ← stable key travels with the image
                      item={item}
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
                    setDeleteId(null); // null = "delete all" mode
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
              sx={{ width: 20, height: 20, mr: 1, color: "divider" }}
            />
            Save
          </LoadingButton>
        </DialogActions>
      </Box>

      <DeleteMediaDialog
        open={isDeleteDialogOpen}
        onClose={() => {
          setIsDeleteDialogOpen(false);
          setDeleteId(null);
        }}
        onDelete={deleteId !== null ? handleConfirmDelete : handleDeleteAll}
        isPending={false}
        fileName={deleteLabel}
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