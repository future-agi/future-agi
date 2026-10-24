import {
  Box,
  Button,
  IconButton,
  Stack,
  Typography,
  useTheme,
} from "@mui/material";
import PropTypes from "prop-types";
import React from "react";
import { useFieldArray } from "react-hook-form";
import FormTextFieldV2 from "src/components/FormTextField/FormTextFieldV2";
import SvgColor from "src/components/svg-color";

function HeaderRow({ index, control, onRemove }) {
  return (
    <Stack direction={"row"} alignItems={"flex-start"} sx={{ mb: 1 }} gap={1.5}>
      <FormTextFieldV2
        fieldName={`headers.${index}.key`}
        control={control}
        label="Key"
        placeholder="Header key"
        fullWidth
        size="small"
      />
      <FormTextFieldV2
        fieldName={`headers.${index}.value`}
        control={control}
        label="value"
        placeholder="Header value"
        fullWidth
        size="small"
      />
      <IconButton onClick={onRemove}>
        <SvgColor
          src="/assets/icons/ic_close.svg"
          sx={{
            bgcolor: "text.primary",
            height: "24px",
            width: "24px",
          }}
        />
      </IconButton>
    </Stack>
  );
}

HeaderRow.propTypes = {
  index: PropTypes.number.isRequired,
  control: PropTypes.object.isRequired,
  onRemove: PropTypes.func,
};

export default function AddHeadersSection({ control }) {
  const theme = useTheme();
  const { remove, fields, append } = useFieldArray({
    name: "headers",
    control,
  });

  const handleAddHeader = () => {
    append({ key: "", value: "" });
  };

  const handleRemoveHeader = (index) => {
    remove(index);
  };

  return (
    <Box
      sx={{
        padding: 2,
        border: "1px solid",
        borderColor: "divider",
        bgcolor: "background.neutral",
      }}
    >
      <Stack
        direction={"row"}
        justifyContent={"space-between"}
        alignItems={"flex-start"}
        gap={2}
      >
        <Stack direction={"column"} gap={0}>
          <Typography
            typography={"m3"}
            fontWeight={"fontWeightMedium"}
            color={"blakc.800"}
          >
            Add custom headers
          </Typography>
          <Typography
            typography={"s2_1"}
            fontWeight={"fontWeightRegular"}
            color={"blakc.1000"}
          >
            Add a short description of what is it in this version for commit
            history tracking
          </Typography>
        </Stack>
        <Button
          onClick={handleAddHeader}
          variant="outlined"
          color="primary"
          sx={{
            flexShrink: 0,
            borderColor: "primary.main",
            color: "primary.main",
            "&:hover": {
              borderColor: "primary.dark",
            },
          }}
          startIcon={
            <SvgColor
              src="/assets/icons/ic_add.svg"
              width="20px"
              sx={{ bgcolor: "primary.main" }}
            />
          }
        >
          Add headers
        </Button>
      </Stack>
      <Stack
        sx={{
          bgcolor: "background.paper",
          padding: theme.spacing(3, 1.5),
          border: `1px solid ${theme.palette.divider}`,
          borderRadius: "8px",
        }}
        direction={"column"}
        mt={2}
        spacing={2}
      >
        {fields?.map((field, index) => (
          <HeaderRow
            key={field.id}
            index={index}
            control={control}
            onRemove={() => handleRemoveHeader(index)}
          />
        ))}
      </Stack>
    </Box>
  );
}

AddHeadersSection.propTypes = {
  control: PropTypes.object,
};
