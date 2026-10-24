import PropTypes from "prop-types";
import { Box, Button, Typography } from "@mui/material";
import Iconify from "src/components/iconify";

AnnotationLabelEmpty.propTypes = {
  onCreateClick: PropTypes.func.isRequired,
};

export default function AnnotationLabelEmpty({ onCreateClick }) {
  return (
    <Box
      sx={{
        display: "flex",
        flexDirection: "column",
        alignItems: "center",
        justifyContent: "center",
        height: "100%",
        py: 10,
        px: 3,
      }}
    >
      <Iconify
        icon="solar:tag-bold-duotone"
        width={64}
        sx={{ color: "text.disabled", mb: 2 }}
      />

      <Typography variant="h6" gutterBottom>
        No labels created yet
      </Typography>

      <Typography
        variant="body2"
        color="text.secondary"
        sx={{ maxWidth: 360, textAlign: "center", mb: 3 }}
      >
        Labels define what annotators will evaluate. Create your first label to
        get started with annotation queues.
      </Typography>

      <Button
        variant="contained"
        color="primary"
        startIcon={<Iconify icon="solar:add-linear" />}
        onClick={onCreateClick}
      >
        Create Label
      </Button>
    </Box>
  );
}
