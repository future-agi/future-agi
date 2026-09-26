import PropTypes from "prop-types";
import { Box, Button, Stack, Typography } from "@mui/material";
import Iconify from "src/components/iconify";
import { useAuthContext } from "src/auth/hooks";
import { PERMISSIONS, RolePermission } from "src/utils/rolePermissionMapping";

AnnotationQueueEmpty.propTypes = {
  onCreateClick: PropTypes.func.isRequired,
};

export default function AnnotationQueueEmpty({ onCreateClick }) {
  const { role } = useAuthContext();
  const canWrite = RolePermission.DATASETS[PERMISSIONS.CREATE][role];
  return (
    <Stack
      alignItems="center"
      justifyContent="center"
      height={"100%"}
      sx={{ py: 10, textAlign: "center" }}
    >
      <Box
        sx={{
          width: 64,
          height: 64,
          borderRadius: "50%",
          bgcolor: "action.selected",
          display: "flex",
          alignItems: "center",
          justifyContent: "center",
          mb: 2,
        }}
      >
        <Iconify
          icon="solar:inbox-line-bold"
          width={32}
          sx={{ color: "primary.main" }}
        />
      </Box>
      <Typography variant="h6" gutterBottom>
        No Active annotation queues yet
      </Typography>
      <Typography variant="body2" color="text.secondary" sx={{ mb: 3 }}>
        Create your first annotation queue to start collecting human feedback.
      </Typography>
      {canWrite && (
        <Button
          variant="outlined"
          color="primary"
          startIcon={<Iconify icon="mingcute:add-line" />}
          onClick={onCreateClick}
        >
          Create Queue
        </Button>
      )}
    </Stack>
  );
}
