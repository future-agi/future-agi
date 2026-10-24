import React, { useRef, useState } from "react";
import { Avatar, Box, Popover, Typography } from "@mui/material";
import PropTypes from "prop-types";
import { stringAvatar } from "src/utils/utils";
import SearchModal from "./SearchModal";

const AnnotationAssignedRender = ({ value, data }) => {
  const [openModal, setOpenModal] = useState(false);
  const anchorEl = useRef(null);

  const handleCloseModal = () => {
    setOpenModal(false);
  };
  if (!value) return "";

  return (
    <>
      <Box
        display="flex"
        alignItems="center"
        gap={1}
        sx={{ position: "relative" }}
        onClick={() => {
          setOpenModal(true);
        }}
        ref={anchorEl}
      >
        <Box display="flex" alignItems="center" gap={1}>
          <Avatar {...stringAvatar(value?.name)} sx={style.avatarStyle} />

          <Typography color="text.primary" fontSize={12}>
            {value?.name}
          </Typography>
        </Box>

        {value?.otherCount > 0 && (
          <Typography color="text.secondary" fontSize={12} fontWeight={400}>
            +{value?.otherCount} others
          </Typography>
        )}
      </Box>
      {/* Popover for People Assigned */}
      <Popover
        anchorEl={anchorEl.current}
        anchorOrigin={{ horizontal: "left", vertical: "bottom" }}
        open={openModal}
        onClose={handleCloseModal}
        MenuListProps={{
          "aria-labelledby": "basic-button",
        }}
        sx={{
          "& .MuiPopover-paper": {
            padding: 0,
          },
        }}
      >
        <Box sx={{ minHeight: 285, minWidth: 200 }}>
          <SearchModal peopleAssigned={data?.assignedUsers} />
        </Box>
      </Popover>
    </>
  );
};

AnnotationAssignedRender.propTypes = {
  value: PropTypes.shape({
    name: PropTypes.string.isRequired,
    otherCount: PropTypes.number.isRequired,
  }).isRequired,
  onClick: PropTypes.func, // Make optional
  data: PropTypes.object,
};

AnnotationAssignedRender.defaultProps = {
  onClick: () => {}, // Default no-op function
  data: {},
};

export default AnnotationAssignedRender;

const style = {
  avatarStyle: {
    width: "25px",
    height: "25px",
    objectFit: "cover",
    fontSize: "12px",
    backgroundColor: "action.hover",
    color: "status.discovery.fg",
  },
};
