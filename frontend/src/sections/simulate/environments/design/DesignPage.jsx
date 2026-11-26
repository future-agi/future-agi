import { Box } from "@mui/material";
import { Navigate, useParams } from "react-router-dom";
import DesignForm from "src/pages/dashboard/harness/DesignForm";
import { paths } from "src/routes/paths";

const jobId = /^[\da-f]{8}(?:-[\da-f]{4}){3}-[\da-f]{12}$/i;

export default function DesignPage() {
  const { draftId } = useParams();
  if (jobId.test(draftId)) {
    return (
      <Navigate
        replace
        to={paths.dashboard.simulate.environments.detail(draftId)}
      />
    );
  }
  return (
    <Box sx={{ maxWidth: 760, p: 3 }}>
      <DesignForm scratch />
    </Box>
  );
}
