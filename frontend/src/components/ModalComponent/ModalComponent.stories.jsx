import React, { useState } from "react";
import { Button, Box, Typography } from "@mui/material";
import { ModalComponent } from "./ModalComponent";

const meta = {
  component: ModalComponent,
  title: "UI Components/ModalComponent",
  argTypes: {
    open: {
      control: "boolean",
      description: "Controls the visibility of the modal",
    },
    onClose: {
      action: "closed",
      description: "Function called when the modal is closed",
    },
    children: {
      control: "text",
      description: "Content inside the modal",
    },
  },
};

export default meta;

const Template = (args) => {
  const [open, setOpen] = useState(false);

  const handleOpen = () => {
    setOpen(true);
  };

  const handleClose = () => {
    setOpen(false);
    if (args.onClose) {
      args.onClose();
    }
  };

  return (
    <Box>
      <Button variant="contained" onClick={handleOpen}>
        Open Modal
      </Button>
      <ModalComponent {...args} open={open} onClose={handleClose}>
        {args.children || (
          <Box sx={{ p: 4, textAlign: "center" }}>
            <Typography variant="h6">Default Modal Content</Typography>
            <Typography variant="body2">
              This is a default modal content
            </Typography>
          </Box>
        )}
      </ModalComponent>
    </Box>
  );
};

export const Default = {
  render: (args) => <Template {...args} />,
};

export const WithCustomContent = {
  render: (args) => <Template {...args} />,
  args: {
    children: (
      <Box sx={{ p: 4, textAlign: "center" }}>
        <Typography variant="h6">Custom Modal Title</Typography>
        <Typography variant="body2">
          This is a custom modal content with more details.
        </Typography>
      </Box>
    ),
  },
};

export const LongContent = {
  render: (args) => <Template {...args} />,
  args: {
    children: (
      <Box sx={{ p: 4, textAlign: "center" }}>
        <Typography variant="h6">Long Content Modal</Typography>
        {[...Array(10)].map((_, i) => (
          <Typography key={i} variant="body2">
            This is a long content paragraph {i + 1}
          </Typography>
        ))}
      </Box>
    ),
  },
};
