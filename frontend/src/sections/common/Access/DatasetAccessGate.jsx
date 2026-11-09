// The dataset page for someone who can't view the dataset (W4-5). The page
// renders as before while the access read runs and whatever it answers,
// except one answer: when the server refuses it with 403 view, the reader
// sees who can grant access and can ask for it, instead of a page whose
// every read fails. So a server without the access read (a 404 or a network
// error) never holds the page back.
import { Box, Button, Stack, Typography } from "@mui/material";
import PropTypes from "prop-types";
import React, { useRef } from "react";
import Iconify from "src/components/iconify";
import { useDatasetAccessQuery } from "src/api/develop/dataset-access";
import { RouterLink } from "src/routes/components";
import { useParams } from "src/routes/hooks";
import AccessDeniedNote from "./AccessDeniedNote";
import { deniedTitle, viewDenialOf } from "./accessModel";

export default function DatasetAccessGate({ datasetId, children }) {
  const { dataset } = useParams();
  const id = datasetId ?? dataset;
  const { error, isLoading } = useDatasetAccessQuery(id, { retry: false });
  // A refetch (after "Request view access") clears the error while it
  // runs; keep the page the reader is on instead of blanking it.
  const shown = useRef(null);
  const fresh = viewDenialOf(error);
  if (fresh) shown.current = fresh;
  else if (!isLoading) shown.current = null;
  if (!id) return children;
  const denial = shown.current;
  if (!denial) return children;
  return <DatasetDeniedPage denial={denial} />;
}

DatasetAccessGate.propTypes = {
  datasetId: PropTypes.string,
  children: PropTypes.node,
};

/**
 * The page a refused reader sees (the dataset page, and the experiment page
 * when its dataset is refused; W4 §9 W1 A integration, M6): who can grant
 * access and Request view access.
 */
export function DatasetDeniedPage({ denial }) {
  return (
    <Box
      data-testid="dataset-access-denied"
      sx={{ display: "flex", justifyContent: "center", py: 10, px: 2 }}
    >
      <Stack spacing={2} sx={{ maxWidth: 560, width: "100%" }}>
        <Iconify
          icon="solar:lock-keyhole-bold"
          width={28}
          sx={{ color: "text.disabled" }}
        />
        <Typography variant="h5">{deniedTitle(denial)}</Typography>
        <AccessDeniedNote denial={denial} />
        <Box>
          <Button
            component={RouterLink}
            href="/dashboard/develop"
            size="small"
            startIcon={<Iconify icon="eva:arrow-ios-back-fill" width={14} />}
          >
            Back to datasets
          </Button>
        </Box>
      </Stack>
    </Box>
  );
}

DatasetDeniedPage.propTypes = {
  denial: PropTypes.object.isRequired,
};
