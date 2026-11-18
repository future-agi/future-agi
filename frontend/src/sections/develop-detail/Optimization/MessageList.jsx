import React from "react";
import PropTypes from "prop-types";
import { Box, Button, Typography } from "@mui/material";
import { useFieldArray } from "react-hook-form";
import { PromptSection } from "src/components/prompt-section";
import Iconify from "src/components/iconify";
import { getRandomId } from "src/utils/utils";

const MessageList = ({ control, allColumns }) => {
  const { fields, append, remove } = useFieldArray({
    control,
    name: "messages",
  });

  return (
    <Box sx={{ display: "flex", flexDirection: "column", gap: "20px" }}>
      {fields.map((field, index) => (
        <PromptSection
          key={field.id}
          allColumns={allColumns}
          control={control}
          prefixControlString={`messages.${index}`}
          onRemove={index > 0 ? () => remove(index) : undefined}
          roleSelectDisabled={index === 0}
        />
      ))}
      <Box
        sx={{
          display: "flex",
          justifyContent: "space-between",
          alignItems: "center",
        }}
      >
        <Button
          size="small"
          color="primary"
          startIcon={<Iconify icon="solar:add-linear" />}
          onClick={() => {
            append({ id: getRandomId(), role: "user", content: "" });
          }}
        >
          Add Message
        </Button>
        <Typography color="text.secondary" variant="subtitle2" fontWeight={400}>
          use
          <Typography component="span" color="primary">
            {" {{ "}
          </Typography>
          to access variables
        </Typography>
      </Box>
    </Box>
  );
};

MessageList.propTypes = {
  control: PropTypes.any,
  allColumns: PropTypes.array,
};

export default MessageList;
