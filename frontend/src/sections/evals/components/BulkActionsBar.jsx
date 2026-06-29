import { Box, Button, Divider, Typography } from "@mui/material";
import PropTypes from "prop-types";
import Iconify from "src/components/iconify";

const BulkActionsBar = ({
  selectedCount,
  onDelete,
  onCancel,
  canEditEvals = true,
}) => (
  <Box
    sx={{
      display: "flex",
      alignItems: "center",
      gap: 2,
      px: 2,
      py: 0.5,
      borderRadius: "8px",
      border: "1px solid",
      borderColor: "divider",
    }}
  >
    <Typography
      variant="body2"
      fontWeight={600}
      color="primary.main"
      sx={{ pr: 1 }}
    >
      {selectedCount} Selected
    </Typography>

    <Divider orientation="vertical" flexItem />

    <Button
      size="small"
      color="error"
      startIcon={<Iconify icon="solar:trash-bin-trash-bold" width={16} />}
      onClick={onDelete}
      disabled={!canEditEvals}
      sx={{ fontSize: "13px", textTransform: "none" }}
    >
      Delete
    </Button>

    <Typography
      variant="body2"
      color="text.secondary"
      fontWeight={600}
      sx={{ cursor: "pointer" }}
      onClick={onCancel}
    >
      Cancel
    </Typography>
  </Box>
);

BulkActionsBar.propTypes = {
  selectedCount: PropTypes.number.isRequired,
  onDelete: PropTypes.func.isRequired,
  onCancel: PropTypes.func.isRequired,
  canEditEvals: PropTypes.bool,
};

export default BulkActionsBar;
