import React, { useState } from "react";
import { MemoryRouter } from "react-router-dom";
import Button from "@mui/material/Button";

import ColumnConfigureDropDown from "./ColumnConfigureDropDown.jsx";
import logger from "src/utils/logger.js";

const meta = {
  component: ColumnConfigureDropDown,
  title: "UI Components/ColumnConfigureDropDown",
};

export default meta;

const Template = (args) => {
  const [open, setOpen] = useState(false);
  const [anchorEl, setAnchorEl] = useState(null);
  const [columns, setColumns] = useState(args.initialColumns);

  const handleClick = (event) => {
    setAnchorEl(event.currentTarget);
    setOpen(true);
  };

  const handleClose = () => {
    setOpen(false);
  };

  const handleVisibilityChange = (visibilityMap) => {
    logger.debug("onColumnVisibilityChange", visibilityMap);
    setColumns((prev) =>
      prev.map((c) => ({ ...c, isVisible: visibilityMap[c.id] })),
    );
  };

  return (
    <MemoryRouter>
      <Button onClick={handleClick}>Open Dropdown</Button>
      <ColumnConfigureDropDown
        {...args}
        open={open}
        onClose={handleClose}
        anchorEl={anchorEl}
        columns={columns}
        setColumns={setColumns}
        onColumnVisibilityChange={handleVisibilityChange}
      />
    </MemoryRouter>
  );
};

export const Default = Template.bind({});
Default.args = {
  initialColumns: [
    { id: "1", name: "Column 1", isVisible: true },
    { id: "2", name: "Column 2", isVisible: false },
    { id: "3", name: "Column 3", isVisible: true },
    { id: "4", name: "Column 4", isVisible: false },
  ],
};

export const AllSelected = Template.bind({});
AllSelected.args = {
  initialColumns: [
    { id: "1", name: "Column 1", isVisible: true },
    { id: "2", name: "Column 2", isVisible: true },
    { id: "3", name: "Column 3", isVisible: true },
  ],
};

export const NoneSelected = Template.bind({});
NoneSelected.args = {
  initialColumns: [
    { id: "1", name: "Column 1", isVisible: false },
    { id: "2", name: "Column 2", isVisible: false },
    { id: "3", name: "Column 3", isVisible: false },
  ],
};
