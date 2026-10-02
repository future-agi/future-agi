import React from "react";
import Button from "@mui/material/Button";
import ConfirmDialog from "./confirm-dialog";

export default {
  title: "Components/ConfirmDialog",
  component: ConfirmDialog,
  parameters: {
    layout: "centered",
  },
  argTypes: {
    open: { control: "boolean" },
    title: { control: "text" },
    content: { control: "text" },
    onClose: { action: "closed" },
  },
};

// Basic confirm dialog
export const Basic = {
  args: {
    open: true,
    title: "Delete Item",
    content: "Are you sure you want to delete this item?",
    action: (
      <Button
        size="small"
        variant="contained"
        color="error"
        sx={{ paddingX: "24px" }}
      >
        Delete
      </Button>
    ),
  },
};

// Warning dialog
export const Warning = {
  args: {
    open: true,
    title: "Warning",
    content: "This action cannot be undone. Please confirm to proceed.",
    action: (
      <Button
        size="small"
        variant="contained"
        color="warning"
        sx={{ paddingX: "24px" }}
      >
        Proceed
      </Button>
    ),
  },
};

// Success dialog
export const Success = {
  args: {
    open: true,
    title: "Confirm Changes",
    content: "Your changes will be saved. Do you want to continue?",
    action: (
      <Button
        size="small"
        variant="contained"
        color="success"
        sx={{ paddingX: "24px" }}
      >
        Save Changes
      </Button>
    ),
  },
};
