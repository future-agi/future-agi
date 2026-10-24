import {
  Box,
  ButtonBase,
  CircularProgress,
  TableCell,
  Typography,
} from "@mui/material";
import React from "react";
import Iconify from "src/components/iconify";
import PropTypes from "prop-types";

const JobAction = ({
  status,
  deleted,
  onDeleteClick,
  onStatusChange,
  statusLoading,
}) => {
  if (deleted) {
    return <></>;
  }

  return (
    <TableCell>
      <Box
        sx={{
          display: "flex",
          gap: "17px",
          width: "100%",
          justifyContent: "center",
        }}
      >
        <StatusAction
          status={status}
          onStatusChange={onStatusChange}
          loading={statusLoading}
          visibility={Boolean(onStatusChange)}
        />
        <DeleteAction onClick={() => onDeleteClick()} />
      </Box>
    </TableCell>
  );
};

const DeleteAction = ({ onClick }) => {
  return (
    <ButtonBase
      className="row-hover-action"
      sx={{ gap: 0.5, flexDirection: "column", borderRadius: 1 }}
      onClick={(e) => {
        e.stopPropagation();
        onClick();
      }}
    >
      <Iconify icon="solar:trash-bin-trash-bold" color="error.main" />
      <Typography fontSize={12} fontWeight={400} color="text.primary">
        Delete
      </Typography>
    </ButtonBase>
  );
};

DeleteAction.propTypes = {
  onClick: PropTypes.func,
};

const StatusAction = ({ status, onStatusChange, loading, visibility }) => {
  const renderIcon = () => {
    if (loading) {
      return <CircularProgress size={20} />;
    }
    return status ? (
      <Iconify color="secondary.light" icon="solar:pause-linear" />
    ) : (
      <Iconify color="secondary.light" icon="solar:play-linear" />
    );
  };

  return (
    <ButtonBase
      className="row-hover-action"
      sx={{
        gap: 0.5,
        flexDirection: "column",
        borderRadius: 1,
        visibility: visibility ? undefined : "hidden",
      }}
      onClick={(e) => {
        e.stopPropagation();
        onStatusChange(!status);
      }}
    >
      {renderIcon()}

      <Typography fontSize={12} fontWeight={400} color="text.primary">
        {status ? "Pause" : "Resume"}
      </Typography>
    </ButtonBase>
  );
};

StatusAction.propTypes = {
  status: PropTypes.bool,
  onStatusChange: PropTypes.func,
  loading: PropTypes.bool,
  visibility: PropTypes.bool,
};

JobAction.propTypes = {
  status: PropTypes.bool,
  deleted: PropTypes.bool,
  onDeleteClick: PropTypes.func,
  onStatusChange: PropTypes.func,
  statusLoading: PropTypes.bool,
};

export default JobAction;
