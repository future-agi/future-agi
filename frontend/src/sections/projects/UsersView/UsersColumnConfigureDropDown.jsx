import React from "react";
import useUsersStore from "./Store/usersStore";
import ColumnConfigureDropDown from "src/sections/project-detail/ColumnDropdown/ColumnConfigureDropDown";
import PropTypes from "prop-types";

export const UsersColumnConfigureDropDown = ({ anchorRef }) => {
  const {
    columnPanelOpen,
    setColumnPanelOpen,
    columns,
    setColumns,
    updateColumnVisibility,
  } = useUsersStore();

  return (
    <ColumnConfigureDropDown
      open={columnPanelOpen}
      onClose={() => setColumnPanelOpen(false)}
      anchorEl={anchorRef?.current}
      columns={columns}
      setColumns={setColumns}
      onColumnVisibilityChange={updateColumnVisibility}
      useGrouping={false}
    />
  );
};

UsersColumnConfigureDropDown.propTypes = {
  anchorRef: PropTypes.object,
};
