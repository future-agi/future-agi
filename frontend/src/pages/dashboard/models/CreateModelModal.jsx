import React from "react";
import PropType from "prop-types";
import { useQueryClient } from "@tanstack/react-query";
import AddCustomModal from "../settings/AddCustomModal";

const CreateModelModal = ({ open, onClose }) => {
  const queryClient = useQueryClient();

  // Mount a fresh form each time so credentials are cleared on close.
  if (!open) return null;

  return (
    <AddCustomModal
      open={open}
      onClose={onClose}
      onRefresh={() => queryClient.invalidateQueries({ queryKey: ["models"] })}
    />
  );
};

CreateModelModal.propTypes = {
  open: PropType.bool,
  onClose: PropType.func,
};

export default CreateModelModal;
