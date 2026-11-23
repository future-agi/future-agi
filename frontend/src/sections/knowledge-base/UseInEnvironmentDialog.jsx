import PropTypes from "prop-types";
import { useQuery } from "@tanstack/react-query";
import { useNavigate } from "react-router-dom";
import {
  Button,
  Dialog,
  DialogActions,
  DialogContent,
  DialogTitle,
  List,
  ListItemButton,
  ListItemText,
  Typography,
} from "@mui/material";
import { listHarnessEnvironments } from "src/api/simulate-environments/harnessEnvironments";
import { paths } from "src/routes/paths";

export default function UseInEnvironmentDialog({ open, onClose, selection }) {
  const navigate = useNavigate();
  const { data, isLoading, error } = useQuery({
    queryKey: ["harness-environments", "source-targets"],
    queryFn: () => listHarnessEnvironments({ page: 1, limit: 100 }),
    enabled: open,
  });
  const environments = (data?.results || []).filter(
    (environment) => environment.status === "completed",
  );

  const choose = (environment) => {
    navigate(
      `${paths.dashboard.simulate.environments.detail(environment.id)}?tab=world`,
      { state: { environmentSourceSelection: selection } },
    );
  };

  return (
    <Dialog open={open} onClose={onClose} fullWidth>
      <DialogTitle>Use in Environment</DialogTitle>
      <DialogContent dividers>
        {isLoading && <Typography>Loading Environments…</Typography>}
        {error && (
          <Typography color="error">
            Couldn’t load editable Environments.
          </Typography>
        )}
        {!isLoading && !error && !environments.length && (
          <Typography color="text.secondary">
            No ready Environments are available. Create or finish an Environment
            first.
          </Typography>
        )}
        <List>
          {environments.map((environment) => (
            <ListItemButton
              key={environment.id}
              onClick={() => choose(environment)}
            >
              <ListItemText
                primary={environment.name}
                secondary="Add to the Environment source selection"
              />
            </ListItemButton>
          ))}
        </List>
      </DialogContent>
      <DialogActions>
        <Button onClick={onClose}>Cancel</Button>
      </DialogActions>
    </Dialog>
  );
}

UseInEnvironmentDialog.propTypes = {
  open: PropTypes.bool,
  onClose: PropTypes.func.isRequired,
  selection: PropTypes.shape({
    kb_id: PropTypes.string.isRequired,
    whole_collection: PropTypes.bool,
    file_ids: PropTypes.arrayOf(PropTypes.string),
  }),
};
