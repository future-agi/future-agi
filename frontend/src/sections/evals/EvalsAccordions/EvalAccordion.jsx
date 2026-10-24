import PropTypes from "prop-types";
import { SegmentedTabs } from "src/components/tabs/tabs";
import React, { useRef, useState } from "react";
import {
  Box,
  Button,
  IconButton,
  Modal,
  Stack,
  Tab,
  Typography,
  useTheme,
} from "@mui/material";
import { ShowComponent } from "src/components/show";
import "react-json-view-lite/dist/index.css";
import Iconify from "src/components/iconify";
import { enqueueSnackbar } from "src/components/snackbar";
import {
  Accordion,
  AccordionDetails,
  AccordionSummary,
} from "src/sections/develop-detail/AccordianElements";
import { Controller } from "react-hook-form";
import logger from "src/utils/logger";

const EvalAccordion = ({
  data,
  setData,
  column,
  showTabs = false,
  controller,
  setValue,
}) => {
  const [selectedImageIdx, setSelectedImageIdx] = useState(null);
  const [imageModal, setImageModal] = useState(false);
  const [selectedImage, setSelectedImage] = useState(null);
  const [isDragging, setIsDragging] = useState(false);
  const theme = useTheme();
  const imageFileRefs = useRef([]);

  const handleTabChange = (event, newValue, index) => {
    const updatedData = [...data];
    updatedData[index].type = newValue;
    if (newValue == "image") {
      setSelectedImage(updatedData[index]);
    } else {
      setSelectedImage(null);
    }

    setData(updatedData);
  };

  const processFile = (file, index, item) => {
    if (file) {
      const reader = new FileReader();
      const updatedData = [...data];

      reader.onloadend = () => {
        setValue(
          item?.map == "input"
            ? `config.config['input'][${index}].value`
            : `config.mapping.${item.name}`,
          reader.result, // This is the new image URL (base64)
          { shouldValidate: true },
        );
        updatedData[index].value = reader.result;
        setData(updatedData);
      };
      reader.onerror = () => logger.error("File read error");
      reader.readAsDataURL(file);
    }
  };

  const handleImageChange = (e, index, item) => {
    const file = e.target.files[0];
    processFile(file, index, item);
  };

  const handleImageDelete = (e, index, item) => {
    const updatedData = [...data];
    updatedData[index].value = "";
    setData(updatedData);
    setValue(
      item?.map == "input"
        ? `config.config['input'][${index}].value`
        : `config.mapping.${item.name}`,
      "",
      { shouldValidate: true },
    );
  };

  const handleImageReplace = (e, index, item) => {
    const file = e.target.files[0];
    processFile(file, index, item);
    setImageModal(false);
  };

  const handleInputChange = (event, index, item) => {
    const updatedData = [...data];
    updatedData[index].value = event.target.value;
    setData(updatedData);
    setValue(
      item?.map == "input"
        ? `config.config['input'][${index}].value`
        : `config.mapping.${item.name}`,
      updatedData[index]?.value,
      { shouldValidate: true },
    );
  };

  // Drag and drop handlers
  const handleDragEnter = (e, _) => {
    e.preventDefault();
    e.stopPropagation();
    setIsDragging(true);
  };

  const handleDragLeave = (e, _) => {
    e.preventDefault();
    e.stopPropagation();
    setIsDragging(false);
  };

  const handleDragOver = (e) => {
    e.preventDefault();
    e.stopPropagation();
  };

  const handleDrop = (e, index, item) => {
    e.preventDefault();
    e.stopPropagation();
    setIsDragging(false);

    if (e.dataTransfer.files && e.dataTransfer.files.length > 0) {
      const file = e.dataTransfer.files[0];
      // Check if file is an image
      if (file.type.match(/image\/.*/)) {
        processFile(file, index, item);
      } else {
        enqueueSnackbar("Please drop an image file", { variant: "error" });
      }
    }
  };

  return (
    <Accordion defaultExpanded disableGutters>
      <AccordionSummary>{column?.headerName}</AccordionSummary>
      <AccordionDetails sx={{ padding: 0 }}>
        <Box
          sx={{
            paddingX: 2,
            paddingBottom: 1,
            overflowY: "auto",
            maxHeight: "250px",
          }}
        >
          <Box
            sx={{
              // border: "1px solid",
              borderColor: "action.selected",
              borderRadius: "8px",
            }}
          >
            {data?.length > 0 &&
              data?.map((item, index) => (
                <Box key={item?.id} sx={{ marginY: 2 }}>
                  <Box
                    sx={{
                      display: "flex",
                      justifyContent: "space-between",
                      alignItems: "center",
                    }}
                  >
                    <Typography
                      sx={{
                        fontSize: "12px",
                        fontWeight: "500",
                        padding: "5px 5px",
                        fontFamily: theme.typography.fontFamily,
                      }}
                    >
                      {item?.name}
                    </Typography>

                    {showTabs && (
                      <SegmentedTabs
                        value={item?.type}
                        onChange={(event, newValue) =>
                          handleTabChange(event, newValue, index)
                        }
                      >
                        <Tab value="text" label="Text" />
                        <Tab value="image" label="Image" />
                      </SegmentedTabs>
                    )}
                  </Box>

                  <ShowComponent condition={item?.type === "text"}>
                    <Box
                      sx={{
                        paddingX: "16px",
                        paddingY: "12px",
                        borderRadius: "8px",
                        backgroundColor: "background.neutral",
                        overflowWrap: "break-word",
                        marginTop: "10px",
                      }}
                    >
                      <Typography variant="body2">
                        <Controller
                          render={({
                            field: { onChange, value, onBlur, ref },
                            formState: { errors },
                          }) => (
                            <>
                              <textarea
                                onChange={(e) => {
                                  onChange(e);
                                  handleInputChange(e, index, item);
                                }}
                                onBlur={onBlur}
                                ref={ref}
                                value={value}
                                defaultValue={item?.value}
                                placeholder="Write your input here..."
                                style={{
                                  width: "100%",
                                  padding: "8px",
                                  borderRadius: "6px",
                                  outline: "none",
                                  borderColor: "transparent",
                                  minHeight: "80px",
                                  background: "transparent",
                                  resize: "none",
                                  fontSize: "13px",
                                  color: theme.palette.text.primary,
                                  fontFamily: theme.typography.fontFamily,
                                  verticalAlign: "top",
                                }}
                              />
                              {errors?.config?.config
                                ? errors?.config?.config?.input?.[index] && (
                                    <Typography variant="caption" color="error">
                                      {errors?.config?.config?.input[index]
                                        ?.message || "This field is required"}
                                    </Typography>
                                  )
                                : errors?.config?.mapping[item?.name] && (
                                    <Typography variant="caption" color="error">
                                      {errors?.config?.mapping[item?.name]
                                        ?.message || "This field is required"}
                                    </Typography>
                                  )}
                            </>
                          )}
                          control={controller}
                          name={
                            item?.map == "input"
                              ? `config.config['input'][${index}]`
                              : `config.mapping.${item.name}`
                          }
                        />
                      </Typography>
                    </Box>
                  </ShowComponent>
                  <ShowComponent condition={item?.type === "image"}>
                    {item?.value?.length > 0 ? (
                      <Box
                        sx={{
                          paddingX: "16px",
                          paddingY: "12px",
                          borderRadius: "8px",
                          overflowWrap: "break-word",
                          backgroundColor: "background.neutral",
                          marginTop: "10px",
                          minHeight: "100px",
                          display: "flex",
                          justifyContent: "center",
                          alignItems: "center",
                          position: "relative",
                          "&:hover .overlay": {
                            opacity: 1,
                            visibility: "visible",
                          },
                        }}
                      >
                        <Box
                          position="relative"
                          overflow="hidden"
                          sx={{
                            "&:hover .overlay": {
                              opacity: 1,
                              visibility: "visible",
                            },
                          }}
                        >
                          <Box
                            className="overlay"
                            position="absolute"
                            top={0}
                            left={0}
                            display="flex"
                            alignItems="center"
                            justifyContent="center"
                            width="100%"
                            height="100%"
                            // borderRadius="15px"
                            gap="5px"
                            // eslint-disable-next-line no-restricted-syntax -- ds: overlay scrim
                            bgcolor="#00000080"
                            sx={{
                              opacity: 0,
                              visibility: "hidden",
                              transition:
                                "opacity 0.3s ease, visibility 0.3s ease",
                            }}
                          >
                            <IconButton sx={{ color: "common.white" }}>
                              <Iconify
                                icon="solar:magnifier-zoom-in-linear"
                                onClick={() => {
                                  setSelectedImage(item?.value);
                                  setImageModal(true);
                                  setSelectedImageIdx(index);
                                }}
                              />
                            </IconButton>
                            <IconButton sx={{ color: "common.white" }}>
                              <Iconify icon="solar:refresh-linear" />
                            </IconButton>
                            <IconButton
                              sx={{ color: "common.white" }}
                              onClick={(e) => handleImageDelete(e, index, item)}
                            >
                              <Iconify icon="solar:trash-bin-trash-linear" />
                            </IconButton>
                          </Box>
                          <img
                            src={item?.value}
                            style={{
                              width: "100%",
                              minHeight: "100%",
                              objectFit: "cover",
                            }}
                          />
                        </Box>
                        <Modal
                          open={imageModal && selectedImageIdx === index}
                          onClose={() => {
                            setImageModal(false);
                            setSelectedImage(null);
                            setSelectedImageIdx(null);
                          }}
                          sx={{ display: "flex", alignItems: "center" }}
                        >
                          <Box
                            sx={{
                              width: "100%",
                              maxWidth: "960px",
                              bgcolor: "background.paper",
                              // eslint-disable-next-line no-restricted-syntax -- ds: overlay shadow
                              boxShadow: 24,
                              borderRadius: "12px",
                              mx: "auto",
                              height: "90vh",
                            }}
                          >
                            <Stack
                              flexDirection="row"
                              alignItems="center"
                              justifyContent="space-between"
                              p="13px 24px"
                            >
                              <Typography>Uploaded Image</Typography>
                              <IconButton onClick={() => setImageModal(false)}>
                                <Iconify icon="solar:close-linear" />
                              </IconButton>
                            </Stack>
                            {selectedImage && (
                              <Box
                                sx={{
                                  width: "100%",
                                  display: "flex",
                                  justifyContent: "center",
                                  alignItems: "center",
                                  height: "68vh",
                                  position: "relative",
                                }}
                              >
                                <img
                                  src={selectedImage}
                                  alt="Selected"
                                  style={{
                                    width: "100%",
                                    height: "100%",
                                    objectFit: "contain",
                                    position: "absolute",
                                    top: 0,
                                    left: 0,
                                  }}
                                />
                              </Box>
                            )}
                            <Stack
                              flexDirection="row"
                              justifyContent="center"
                              // pt="30px"
                              gap="16px"
                              pr="24px"
                              pb="16px"
                            >
                              <Button
                                variant="outlined"
                                sx={{ mt: 2 }}
                                color="primary"
                                component="label"
                              >
                                Replace Image
                                <input
                                  type="file"
                                  accept="image/jpeg,image/jpg,image/png,image/gif,image/webp"
                                  onChange={(event) =>
                                    handleImageReplace(event, index, item)
                                  }
                                  style={{ display: "none" }}
                                />
                              </Button>

                              <Button
                                onClick={(e) => {
                                  handleImageDelete(e, index, item);
                                  setImageModal(false);
                                }}
                                variant="contained"
                                sx={{ mt: 2 }}
                                color="error"
                              >
                                Delete
                              </Button>
                            </Stack>
                          </Box>
                        </Modal>
                      </Box>
                    ) : (
                      <Controller
                        name={
                          item?.map == "input"
                            ? `config.config['input'][${index}]`
                            : `config.mapping.${item.name}`
                        }
                        control={controller}
                        render={({ formState: { errors } }) => (
                          <>
                            <Box
                              sx={{
                                paddingX: "16px",
                                paddingY: "12px",
                                borderRadius: "8px",
                                overflowWrap: "break-word",
                                backgroundColor: isDragging
                                  ? "action.hover"
                                  : "background.neutral",
                                marginTop: "10px",
                                minHeight: "100px",
                                display: "flex",
                                justifyContent: "center",
                                alignItems: "center",
                                cursor: "pointer",
                                border: isDragging
                                  ? "2px dashed"
                                  : "2px dashed transparent",
                                borderColor: isDragging
                                  ? "primary.main"
                                  : "transparent",
                                transition: "all 0.2s ease",
                              }}
                              onClick={() =>
                                imageFileRefs.current[index]?.click()
                              }
                              onDragEnter={(e) => handleDragEnter(e, index)}
                              onDragOver={handleDragOver}
                              onDragLeave={(e) => handleDragLeave(e, index)}
                              onDrop={(e) => handleDrop(e, index, item)}
                            >
                              <Typography variant="body2">
                                <Stack
                                  spacing={3}
                                  alignItems="center"
                                  justifyContent="center"
                                  flexWrap="wrap"
                                >
                                  <Stack
                                    spacing={1}
                                    sx={{ textAlign: "center" }}
                                  >
                                    <Typography
                                      variant=""
                                      sx={{
                                        alignItems: "center",
                                        display: "flex",
                                        justifyContent: "center",
                                        gap: 0.5,
                                      }}
                                    >
                                      <Iconify
                                        icon="solar:upload-minimalistic-linear"
                                        sx={{ cursor: "pointer" }}
                                      />
                                      Drag an image here or
                                      <Box
                                        component="span"
                                        sx={{
                                          color: "primary.main",
                                          textDecoration: "underline",
                                          cursor: "pointer",
                                        }}
                                      >
                                        upload a file
                                      </Box>
                                      <input
                                        type="file"
                                        accept="image/jpeg,image/jpg,image/png,image/gif,image/webp"
                                        style={{ display: "none" }}
                                        ref={(el) =>
                                          (imageFileRefs.current[index] = el)
                                        } // Assign ref for this input element
                                        onChange={(e) =>
                                          handleImageChange(e, index, item)
                                        }
                                      />
                                    </Typography>
                                  </Stack>
                                </Stack>
                                {errors?.config?.config
                                  ? errors?.config?.config?.input?.[index] && (
                                      <Typography
                                        variant="caption"
                                        color="error"
                                      >
                                        {errors?.config?.config?.input[index]
                                          ?.message || "This field is required"}
                                      </Typography>
                                    )
                                  : errors?.config?.mapping[item?.name] && (
                                      <Typography
                                        variant="caption"
                                        color="error"
                                      >
                                        {errors?.config?.mapping[item?.name]
                                          ?.message || "This field is required"}
                                      </Typography>
                                    )}
                              </Typography>
                            </Box>
                          </>
                        )}
                      />
                    )}
                  </ShowComponent>
                </Box>
              ))}
          </Box>
        </Box>
      </AccordionDetails>
    </Accordion>
  );
};

EvalAccordion.propTypes = {
  data: PropTypes.array,
  column: PropTypes.object,
  allowCopy: PropTypes.bool,
  setData: PropTypes.func,
  showTabs: PropTypes.bool,
  controller: PropTypes.any,
  setValue: PropTypes.any,
};

export default EvalAccordion;
