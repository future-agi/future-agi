import PropTypes from "prop-types";
import { Button } from "@mui/material";
import EmptyState from "src/components/empty-state";
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
    <EmptyState
      icon="solar:inbox-linear"
      title="No annotation queues yet"
      description="A queue sends traces, dataset rows or calls to people for review, with the labels they score them on."
      action={
        canWrite ? (
          <Button
            size="small"
            variant="contained"
            color="primary"
            startIcon={<Iconify icon="solar:add-linear" width={16} />}
            onClick={onCreateClick}
          >
            Create queue
          </Button>
        ) : null
      }
      docsHref="https://docs.futureagi.com/docs/annotations"
    />
  );
}
