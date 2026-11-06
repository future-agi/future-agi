import {
  Box,
  Button,
  FormHelperText,
  IconButton,
  InputAdornment,
  List,
  ListItem,
  Popover,
  TextField,
  Typography,
  useTheme,
  ButtonBase,
} from "@mui/material";
import React, { useMemo, useRef, useState } from "react";
import { trackEvent, Events } from "src/utils/Mixpanel";
import PropTypes from "prop-types";
import { FormMultiSelectField } from "src/components/FormSelectField";
import Iconify from "src/components/iconify";
import {
  Accordion,
  AccordionDetails,
  AccordionSummary,
} from "../../AccordianElements";
import { useController } from "react-hook-form";
import { typography } from "src/theme/typography";
import { FormSearchSelectFieldControl } from "src/components/FromSearchSelectField";
import FormTextFieldV2 from "src/components/FormTextField/FormTextFieldV2";

export function StaticAccordion({ control, formHandle, columnData, isWatch }) {
  const processedFields = isWatch("staticFields").map((field) => {
    return {
      ...field,
      type: field.type || "plain_text",
      view: field.view || "default_collapsed",
    };
  });

  const error = formHandle?.errors?.staticFields?.root?.message;

  return (
    <Accordion defaultExpanded>
      <AccordionSummary>Static Fields</AccordionSummary>
      <AccordionDetails sx={{ padding: 0 }}>
        <Box key={0} sx={{ padding: 1 }}>
          <Box display={"flex"} justifyItems={"center"} alignItems={"center"}>
            <IconButton size="small" color="text.secondary">
              <Iconify
                width="13px"
                icon="solar:danger-circle-linear"
                height="13px"
                color="text.secondary"
              />
            </IconButton>
            <Typography fontSize={12} color="text.secondary">
              Choose your input columns
            </Typography>
          </Box>
          {processedFields.map((field, idx) => (
            <Box
              key={field?.id}
              display="flex"
              gap={1}
              alignItems="flex-start"
              sx={{ marginBottom: 2, marginTop: 2 }}
            >
              {/* Column Dropdown */}
              <FormSearchSelectFieldControl
                fullWidth
                label="Column"
                size="small"
                control={control}
                fieldName={`${formHandle.staticItems}.${idx}.column`}
                options={columnData?.[0]?.staticFieldColumn}
                value={field?.column}
                onChange={() => {
                  formHandle.setValue(
                    `${formHandle.staticItems}.${idx}.type`,
                    "plain_text",
                  );
                  formHandle.setValue(
                    `${formHandle.staticItems}.${idx}.view`,
                    "default_collapsed",
                  );
                }}
              />
              {/* Type Dropdown */}
              <FormSearchSelectFieldControl
                fullWidth
                label="Type"
                size="small"
                control={control}
                fieldName={`${formHandle.staticItems}.${idx}.type`}
                options={[
                  { value: "plain_text", label: "Plain Text" },
                  { value: "markdown", label: "Markdown" },
                ]}
              />

              {/* View Dropdown */}
              <FormSearchSelectFieldControl
                fullWidth
                label="View"
                size="small"
                control={control}
                fieldName={`${formHandle.staticItems}.${idx}.view`}
                options={[
                  { value: "default_collapsed", label: "Default Collapsed" },
                  { value: "default_open", label: "Default Open" },
                ]}
              />

              {/* Remove Button */}
              {idx > 0 && (
                <IconButton onClick={() => formHandle.staticRemove(idx)}>
                  <Iconify
                    icon="solar:trash-bin-trash-bold"
                    color="text.secondary"
                  />
                </IconButton>
              )}
              {formHandle.staticFields?.length > 1 && idx === 0 && (
                <Box sx={{ width: "120px" }} />
              )}
            </Box>
          ))}

          {/* Add New Field Button */}
          <Button
            size="small"
            color="primary"
            startIcon={<Iconify icon="solar:add-linear" />}
            onClick={() => {
              trackEvent(Events.annStaticFieldAdd);
              formHandle.staticAppend({
                column: "",
                type: "plain_text", // Default to plain_text when added
                view: "default_collapsed", // Default to collapsed when added
              });
            }}
          >
            Add Field
          </Button>
          {error && (
            <FormHelperText
              error={Boolean(error)}
              sx={{ paddingLeft: "10px", marginTop: "-5px" }}
            >
              {error}
            </FormHelperText>
          )}
        </Box>
      </AccordionDetails>
    </Accordion>
  );
}

StaticAccordion.propTypes = {
  formHandle: PropTypes.any,
  control: PropTypes.any,
  columnData: PropTypes.any,
  isWatch: PropTypes.any,
};

export function ResponseAccordion({
  control,
  formHandle,
  columnData,
  isWatch,
}) {
  const processedFields = isWatch("responseFields").map((field) => {
    return {
      ...field,
      type: field.type || "plain_text",
      edit: field.edit || "editable",
      view: field.view || "default_collapsed",
    };
  });

  const error = formHandle?.errors?.responseFields?.root?.message;

  return (
    <Accordion defaultExpanded>
      <AccordionSummary>Response Fields</AccordionSummary>
      <AccordionDetails sx={{ padding: 0 }}>
        <Box sx={{ padding: 1 }}>
          <Box display={"flex"} justifyItems={"center"} alignItems={"center"}>
            <IconButton size="small" color="text.secondary">
              <Iconify
                width="13px"
                icon="solar:danger-circle-linear"
                height="13px"
                color="text.secondary"
              />
            </IconButton>
            <Typography fontSize={12} color="text.secondary">
              Choose your output columns
            </Typography>
          </Box>
          {processedFields.map((field, idx) => (
            <Box
              key={field?.id}
              display="flex"
              gap={1}
              alignItems="flex-start"
              sx={{ marginBottom: 2, marginTop: 2 }}
            >
              {/* Column Dropdown */}
              <FormSearchSelectFieldControl
                fullWidth
                label="Column"
                size="small"
                control={control}
                fieldName={`${formHandle.responseItems}.${idx}.column`}
                options={columnData?.[0]?.staticFieldColumn}
                value={field?.column}
                onChange={() => {
                  formHandle.setValue(
                    `${formHandle.responseItems}.${idx}.type`,
                    "plain_text",
                  );
                  formHandle.setValue(
                    `${formHandle.responseItems}.${idx}.edit`,
                    "editable",
                  );
                  formHandle.setValue(
                    `${formHandle.responseItems}.${idx}.view`,
                    "default_collapsed",
                  );
                }}
              />

              {/* Type Dropdown */}
              <FormSearchSelectFieldControl
                fullWidth
                label="Type"
                size="small"
                control={control}
                fieldName={`${formHandle.responseItems}.${idx}.type`}
                options={[
                  { value: "plain_text", label: "Plain Text" },
                  { value: "markdown", label: "Markdown" },
                ]}
              />

              {/* Edit Dropdown */}
              <FormSearchSelectFieldControl
                fullWidth
                label="Edit"
                size="small"
                control={control}
                fieldName={`${formHandle.responseItems}.${idx}.edit`}
                options={[
                  { value: "not_editable", label: "Non Editable" },
                  { value: "editable", label: "Editable" },
                ]}
              />

              {/* Remove Button */}
              {idx > 0 && (
                <IconButton onClick={() => formHandle.responseRemove(idx)}>
                  <Iconify
                    icon="solar:trash-bin-trash-bold"
                    color="text.secondary"
                  />
                </IconButton>
              )}
              {formHandle?.responseFields?.length > 1 && idx === 0 && (
                <Box sx={{ width: "120px" }} />
              )}
            </Box>
          ))}

          {/* Add New Field Button */}
          <Button
            size="small"
            color="primary"
            startIcon={<Iconify icon="solar:add-linear" />}
            onClick={() => {
              trackEvent(Events.annResponseFieldAdd);
              formHandle.responseAppend({
                column: "",
                type: "plain_text",
                edit: "editable",
                view: "default_collapsed",
              });
            }}
          >
            Add Field
          </Button>
          {error && (
            <FormHelperText
              error={Boolean(error)}
              sx={{ paddingLeft: "10px", marginTop: "-5px" }}
            >
              {error}
            </FormHelperText>
          )}
        </Box>
      </AccordionDetails>
    </Accordion>
  );
}

ResponseAccordion.propTypes = {
  formHandle: PropTypes.any,
  control: PropTypes.any,
  columnData: PropTypes.any,
  isWatch: PropTypes.any,
};

export function LabelsAccordion({
  control,
  formHandle,
  columnData,
  onAddNewLabel,
  errors,
  isWatch,
  isFetchingNextPage,
  fetchNextPage,
}) {
  const labelFields = isWatch("labelFields");
  const theme = useTheme();
  const [, setPopoverOpen] = useState(false);
  const labelFieldOptions = useMemo(() => {
    return (
      columnData?.[1]?.labelFieldColumn?.map(({ value, label, disabled }) => ({
        value,
        label,
        disabled,
      })) || []
    );
  }, [columnData]);

  const handleClosePopover = () => {
    setPopoverOpen(false);
  };

  const handleCreateLabel = (idx) => {
    onAddNewLabel("New Label", idx);
    handleClosePopover();
  };

  const CustomPopover = ({
    open,
    anchorEl,
    onClose = () => {},
    options = [],
    onSelect = () => {},
  }) => {
    const [searchText, setSearchText] = useState("");

    const filteredOptions = options.filter((option) =>
      option.label.toLowerCase().includes(searchText.toLowerCase()),
    );

    return (
      <Popover
        open={open}
        anchorEl={anchorEl ?? null}
        onClose={onClose}
        anchorOrigin={{
          vertical: "bottom",
          horizontal: "left",
        }}
        transformOrigin={{
          vertical: "top",
          horizontal: "left",
        }}
        PaperProps={{
          sx: {
            width: 320,
            maxHeight: 300,
            display: "flex",
            flexDirection: "column",
          },
        }}
      >
        {/* Search Input */}
        <TextField
          placeholder="Search Label"
          size="small"
          value={searchText}
          onChange={(e) => setSearchText(e.target.value)}
          InputProps={{
            startAdornment: (
              <InputAdornment position="start">
                <Iconify
                  icon="solar:magnifier-linear" width={16}
                  sx={{ color: "divider" }}
                />
              </InputAdornment>
            ),
          }}
          fullWidth
          autoFocus
        />

        <Box
          sx={{
            flex: 1,
            overflowY: "auto",
            maxHeight: 150,
          }}
        >
          <List>
            {filteredOptions.length > 0 ? (
              filteredOptions.map((option) => (
                <ListItem
                  button
                  key={option.value}
                  onClick={() => onSelect(option.label)}
                >
                  <Typography
                    sx={{
                      fontSize: "13px",
                    }}
                  >
                    {option.label}
                  </Typography>
                </ListItem>
              ))
            ) : (
              <Typography
                sx={{
                  textAlign: "center",
                  color: "text.secondary",
                  padding: "10px",
                }}
              >
                No results found
              </Typography>
            )}
          </List>
        </Box>

        <ButtonBase
          component="div"
          sx={{
            position: "sticky",
            bottom: 0,
            left: 0,
            width: "100%",
            color: theme.palette.primary.main,
            fontSize: "13px",
            fontWeight: typography.fontWeightSemiBold,
            lineHeight: 1.6,
            display: "flex",
            justifyContent: "flex-start",
            textAlign: "left",
            alignItems: "center",
            borderTop: 1,
            borderTopStyle: "solid",
            borderTopColor: "divider",
            padding: "6px 8px",
            borderRadius: 0,
            marginTop: "10px",
          }}
          onClick={handleCreateLabel}
        >
          <Iconify icon="solar:add-linear" color="primary.main" />
          <Typography
            sx={{
              color: theme.palette.primary.main,
              marginLeft: "5px",
              whiteSpace: "nowrap",
              fontWeight: 600,
            }}
          >
            Create New Label
          </Typography>
        </ButtonBase>
      </Popover>
    );
  };

  CustomPopover.propTypes = {
    open: PropTypes.bool,
    anchorEl: PropTypes.object,
    onClose: PropTypes.func,
    options: PropTypes.arrayOf(
      PropTypes.shape({
        label: PropTypes.string,
        value: PropTypes.string,
      }),
    ),
    onSelect: PropTypes.func,
  };

  CustomPopover.defaultProps = {
    onClose: () => {},
    onSelect: () => {},
  };

  const anchorRefs = useRef([]);

  return (
    <Accordion defaultExpanded>
      <AccordionSummary>Labels</AccordionSummary>
      <AccordionDetails sx={{ padding: 0 }}>
        <Box sx={{ padding: 1 }}>
          <Box
            sx={{ marginBottom: "18px" }}
            display={"flex"}
            justifyItems={"center"}
            alignItems={"center"}
          >
            <IconButton size="small" color="text.secondary">
              <Iconify
                width="13px"
                icon="solar:danger-circle-linear"
                height="13px"
                color="text.secondary"
              />
            </IconButton>
            <Typography fontSize={12} color="text.secondary">
              These are the scores or annotations labels that annotator will be
              able to assign to the responses
            </Typography>
          </Box>
          <Box sx={{ pt: 0 }}>
            {labelFields.map((field, idx) => {
              return (
                <Box
                  key={field?.id}
                  display="flex"
                  gap={1}
                  alignItems="flex-start"
                  sx={{ marginBottom: 2, mt: 1 }}
                >
                  <Box
                    ref={(el) => (anchorRefs.current[idx] = el)}
                    sx={{ position: "relative", flex: 1 }}
                  >
                    <FormSearchSelectFieldControl
                      fullWidth
                      label="Label Name"
                      size="small"
                      control={control}
                      fieldName={`labelFields.${idx}.labelName`}
                      options={labelFieldOptions}
                      value={field?.labelName?.name ?? field?.labelName}
                      createLabel="Create New Label"
                      handleCreateLabel={() => handleCreateLabel(idx)}
                      isFetchingNextPage={isFetchingNextPage}
                      onScrollEnd={fetchNextPage}
                      onChange={(e) => {
                        const selectedLabel = e?.target?.value || "";
                        const selectedValue =
                          labelFieldOptions.find(
                            (option) => option.label === selectedLabel,
                          )?.value || selectedLabel;

                        formHandle.setValue(
                          `labelFields.${idx}.labelName`,
                          selectedValue,
                        );
                      }}
                    />
                  </Box>
                  <Box sx={{ flex: 1 }}>
                    <FormSearchSelectFieldControl
                      fullWidth
                      label="Assign"
                      size="small"
                      control={control}
                      fieldName={`labelFields.${idx}.assign`}
                      options={[
                        { value: "optional", label: "Optional" },
                        { value: "required", label: "Required" },
                      ]}
                      error={!!errors?.labelFields?.[idx]?.assign}
                      helperText={errors?.labelFields?.[idx]?.assign?.message}
                    />
                  </Box>
                  {idx > 0 && (
                    <IconButton onClick={() => formHandle.labelRemove(idx)}>
                      <Iconify
                        icon="solar:trash-bin-trash-bold"
                        color="text.secondary"
                      />
                    </IconButton>
                  )}
                  {labelFields?.length > 1 && idx === 0 && (
                    <Box sx={{ width: "36px" }} />
                  )}
                </Box>
              );
            })}
            <Button
              size="small"
              color="primary"
              startIcon={<Iconify icon="solar:add-linear" />}
              onClick={() =>
                formHandle.labelAppend({ labelName: "", assign: "" })
              }
            >
              Add Label
            </Button>
          </Box>
        </Box>
      </AccordionDetails>
    </Accordion>
  );
}

LabelsAccordion.propTypes = {
  formHandle: PropTypes.any,
  control: PropTypes.any,
  columnData: PropTypes.any,
  onAddNewLabel: PropTypes.any,
  errors: PropTypes.object,
  isWatch: PropTypes.any,
  fetchNextPage: PropTypes.func,
  isFetchingNextPage: PropTypes.bool,
};

export function AnnotatorsAccordion({ formHandle, control, columnData }) {
  const { field: responseField } = useController({
    control,
    name: "responses",
  });

  return (
    <Accordion defaultExpanded>
      <AccordionSummary>Annotators</AccordionSummary>
      <AccordionDetails sx={{ padding: 0 }}>
        <Box sx={{ padding: 1 }}>
          <Box display={"flex"} justifyItems={"center"} alignItems={"center"}>
            <IconButton size="small" color="text.secondary">
              <Iconify
                icon="solar:danger-circle-linear"
                width="13px"
                height="13px"
                color="text.secondary"
              />
            </IconButton>
            <Typography fontSize={12} color="text.secondary">
              Add annotators to label the dataset
            </Typography>
          </Box>
        </Box>
        <Box sx={{ p: 1 }}>
          {formHandle.annotatorFields.map((field, idx) => (
            <Box
              key={field.id}
              display="flex"
              gap={1}
              alignItems="flex-start"
              sx={{ marginBottom: 1, mt: 1 }}
            >
              <Box
                sx={{
                  flex: "0 0 60%",
                  maxWidth: "60%",
                  position: "relative",
                  "& .MuiFormLabel-root": {
                    backgroundColor: "background.paper",
                    paddingRight: "5px",
                  },
                }}
              >
                <FormMultiSelectField
                  control={control}
                  fieldName={`${formHandle.annotatorItems}.${idx}.addAnnotator`}
                  label="Add Annotator"
                  size="small"
                  options={columnData[3]?.mappedAnnoUserDataForm}
                  fullWidth
                  onChange={(e) => {
                    responseField?.onChange?.(e.length.toString());
                  }}
                />
              </Box>

              <Box sx={{ flex: 1 }}>
                <FormTextFieldV2
                  label="No. of responses"
                  variant="outlined"
                  placeholder="Enter number of responses"
                  fieldName="responses"
                  control={control}
                  size="small"
                  fullWidth
                  fieldType="number"
                />
              </Box>
            </Box>
          ))}
        </Box>
        <Box sx={{ padding: 1, marginBottom: 1 }}>
          <Box display={"flex"} justifyItems={"center"} alignItems={"center"}>
            <IconButton size="small" color="text.secondary">
              <Iconify
                icon="solar:danger-circle-linear"
                width="13px"
                height="13px"
                color="text.secondary"
              />
            </IconButton>

            <Typography fontSize={12} color="text.secondary">
              The number of response should be less than or equal to added
              annotators
            </Typography>
          </Box>
        </Box>
      </AccordionDetails>
    </Accordion>
  );
}

AnnotatorsAccordion.propTypes = {
  formHandle: PropTypes.any,
  control: PropTypes.any,
  columnData: PropTypes.any,
  isWatch: PropTypes.any,
};
