import PropTypes from "prop-types";
import {
  Alert,
  Button,
  Card,
  Stack,
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableRow,
  Typography,
} from "@mui/material";
import { useWebhookActivity } from "src/api/webhooks";
import { activityText, fmtDateTime } from "./copy";

// The endpoint page's Activity card (DESIGN.md section 11.2): this endpoint's
// audit entries, newest first. Those who may see the endpoint's URL get the
// details the API returns for them; everyone else what happened, when and by
// whom (the API decides, GET /webhooks/endpoints/{id}/activity/).
export default function ActivityCard({ endpoint }) {
  const {
    data,
    isLoading,
    isError,
    hasNextPage,
    fetchNextPage,
    isFetchingNextPage,
  } = useWebhookActivity(endpoint.id);
  const rows = data?.rows || [];

  return (
    <Card
      variant="outlined"
      sx={{ p: 2.5 }}
      component="section"
      aria-label="Activity"
    >
      <Typography
        sx={{ typography: "s1", fontWeight: "fontWeightSemiBold", mb: 1.5 }}
      >
        Activity
      </Typography>
      {isError && (
        <Alert severity="error">The activity could not be loaded.</Alert>
      )}
      {!isError && !isLoading && rows.length === 0 && (
        <Typography variant="body2" color="text.secondary">
          Nothing has happened to this webhook yet.
        </Typography>
      )}
      {rows.length > 0 && (
        <Table size="small" aria-label="Activity entries">
          <TableHead>
            <TableRow>
              <TableCell>When</TableCell>
              <TableCell>What</TableCell>
              <TableCell>Who</TableCell>
            </TableRow>
          </TableHead>
          <TableBody>
            {rows.map((entry) => (
              <TableRow key={entry.id}>
                <TableCell sx={{ whiteSpace: "nowrap" }}>
                  {fmtDateTime(entry.at)}
                </TableCell>
                <TableCell>{activityText(entry)}</TableCell>
                <TableCell>
                  {entry.actor ? entry.actor.name : "Future AGI"}
                </TableCell>
              </TableRow>
            ))}
          </TableBody>
        </Table>
      )}
      {hasNextPage && (
        <Stack direction="row" justifyContent="center" sx={{ mt: 1 }}>
          <Button
            size="small"
            onClick={() => fetchNextPage()}
            disabled={isFetchingNextPage}
          >
            Show older
          </Button>
        </Stack>
      )}
    </Card>
  );
}

ActivityCard.propTypes = {
  endpoint: PropTypes.object.isRequired,
};
