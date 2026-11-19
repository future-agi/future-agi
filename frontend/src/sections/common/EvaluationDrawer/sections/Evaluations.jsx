import {
  Box,
  Button,
  InputAdornment,
  Typography,
  useTheme,
} from "@mui/material";
import PropTypes from "prop-types";
import React, { useEffect, useState } from "react";
import { Controller } from "react-hook-form";
import SvgColor from "src/components/svg-color/svg-color";
import { useDebounce } from "src/hooks/use-debounce";
import FormSearchField from "src/components/FormSearchField/FormSearchField";
import Iconify from "../../../../components/iconify";
import { FormSearchSelectFieldControl } from "src/components/FromSearchSelectField";
import { EvalTypes, useCases } from "../validation";
import { useEvalStore } from "../../../evals/store/useEvalStore";
import { useEvaluationContext } from "../context/EvaluationContext";
import { ShowComponent } from "src/components/show";

export default function Evaluations({ control, setValue, isEvalsView }) {
  const theme = useTheme();
  const [searchTerm, setSearchTerm] = useState("");
  const searchQuery = useDebounce(searchTerm.trim(), 300);
  const { setCreateGroupMode, createGroupMode } = useEvalStore();
  const { module } = useEvaluationContext();
  useEffect(() => {
    setValue("searchTerm", searchQuery);
  }, [searchQuery, setValue]);

  const buttonStyles = (isSelected) => ({
    color: isSelected ? "primary.main" : "text.primary",
    backgroundColor: isSelected ? "action.hover" : "transparent",
    border: "1px solid",
    fontSize: "12px",
    fontWeight: 500,
    px: 1,
    borderColor: isSelected ? "primary.lighter" : "divider",
    borderRadius: "4px",
    "&:hover": {
      backgroundColor: isSelected
        ? "action.selected"
        : theme.palette.action.hover,
    },
  });

  const iconColor = (isSelected) =>
    isSelected ? "primary.main" : "text.primary";
  return (
    <>
      <Typography fontWeight="500" fontSize={12}>
        Use Cases
      </Typography>

      {/* Use Cases */}
      <Controller
        name="selectedUseCases"
        control={control}
        render={({ field }) => (
          <Box display="flex" flexWrap="wrap" gap={theme.spacing(1)}>
            {useCases.map((button, index) => {
              const isSelected = field.value.includes(button.value);
              return (
                <Button
                  key={index}
                  size="small"
                  onClick={() => {
                    const updated = isSelected
                      ? field.value.filter((v) => v !== button.value)
                      : [...field.value, button.value];
                    field.onChange(updated);
                  }}
                  startIcon={
                    <SvgColor
                      width={16}
                      src={`/assets/icons/evals_use_case/${button.icon}.svg`}
                      sx={{
                        color: iconColor(isSelected),
                      }}
                    />
                  }
                  sx={buttonStyles(isSelected)}
                >
                  {button.title}
                </Button>
              );
            })}
          </Box>
        )}
      />

      {/* Inputs */}
      <Box
        display="flex"
        gap={theme.spacing(2)}
        mt={theme.spacing(0.5)}
        width="100%"
      >
        {/* Search */}

        <FormSearchField
          size="small"
          autoFocus
          fullWidth
          placeholder="Search"
          variant="outlined"
          searchQuery={searchTerm}
          onChange={(e) => setSearchTerm(e.target.value)}
          sx={{
            "& .MuiInputBase-input": {
              paddingY: `${theme.spacing(1.05)}`,
              paddingRight: `${theme.spacing(0.5)}`,
            },
          }}
          InputProps={{
            startAdornment: (
              <InputAdornment position="start">
                <SvgColor
                  src={`/assets/icons/custom/search.svg`}
                  sx={{ width: "20px", height: "20px", color: "text.disabled" }}
                />
              </InputAdornment>
            ),
            endAdornment: searchTerm && (
              <InputAdornment position="end">
                <Iconify
                  icon="solar:close-linear"
                  onClick={() => setSearchTerm("")}
                  sx={{ color: "text.disabled", cursor: "pointer" }}
                />
              </InputAdornment>
            ),
          }}
        />

        <FormSearchSelectFieldControl
          fullWidth
          label="Eval Categories"
          size="small"
          control={control}
          fieldName="selectedEvalCategory"
          options={[
            { label: "View All", value: "" }, // This ensures "" is passed when 'View All' is selected
            { label: "Future AGI Built", value: "futureagi_built" },
            { label: "User Built", value: "user_built" },
          ]}
        />
        {/* Eval Tags Dropdown */}
        <FormSearchSelectFieldControl
          fullWidth
          label="Eval Type"
          size="small"
          control={control}
          fieldName="selectedEvalTags"
          options={EvalTypes}
          checkbox
          multiple
        />
        <ShowComponent
          condition={
            isEvalsView ||
            module === "task" ||
            module === "workbench" ||
            module === "dataset" ||
            module === "run-experiment" ||
            module === "run-optimization" ||
            module === "experiment"
          }
        >
          <Button
            disabled={createGroupMode}
            onClick={() => {
              setCreateGroupMode(true);
            }}
            sx={{ minWidth: "162px" }}
            color="primary"
            variant="outlined"
            startIcon={
              <SvgColor
                src={"/assets/icons/ic_add.svg"}
                sx={{ color: "inherit" }}
              />
            }
          >
            Create Group
          </Button>
        </ShowComponent>
      </Box>
    </>
  );
}

Evaluations.propTypes = {
  control: PropTypes.object,
  setValue: PropTypes.func,
  isEvalsView: PropTypes.bool,
};
