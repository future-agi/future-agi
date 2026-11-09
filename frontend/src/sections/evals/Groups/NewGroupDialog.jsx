// L7b G6 groups:create: "Create Groups" on the Groups page opens this
// dialog: a name, a description and the evals in the group (picked from the
// Evaluations list, POST eval-templates/list/), saved with
// POST eval-groups/ {name, description, eval_template_ids}. On success the
// groups list refreshes and the new group's page opens.
import {
  Alert,
  Box,
  Button,
  Checkbox,
  Chip,
  CircularProgress,
  Dialog,
  DialogActions,
  DialogContent,
  DialogTitle,
  List,
  ListItemButton,
  ListItemIcon,
  ListItemText,
  Stack,
  TextField,
  Typography,
} from "@mui/material";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import PropTypes from "prop-types";
import React, { useState } from "react";
import { useNavigate } from "react-router";
import { useDebounce } from "src/hooks/use-debounce";
import axios, { endpoints } from "src/utils/axios";

const PAGE_SIZE = 50;

const errorText = (err) =>
  err?.response?.data?.result ??
  err?.response?.data?.error ??
  err?.result ??
  err?.error ??
  err?.message ??
  "Couldn't create the group.";

export default function NewGroupDialog({ open, onClose }) {
  const navigate = useNavigate();
  const queryClient = useQueryClient();
  const [name, setName] = useState("");
  const [description, setDescription] = useState("");
  const [search, setSearch] = useState("");
  const [picked, setPicked] = useState([]);
  const debounced = useDebounce(search.trim(), 250);

  const { data: templates = [], isLoading } = useQuery({
    queryKey: ["new-group-eval-templates", debounced],
    queryFn: () =>
      axios.post(endpoints.develop.eval.listEvalTemplates, {
        page: 0,
        page_size: PAGE_SIZE,
        search: debounced,
        sort_by: "name",
        sort_order: "asc",
      }),
    select: (res) => res?.data?.result?.items ?? [],
    enabled: open,
  });

  const reset = () => {
    setName("");
    setDescription("");
    setSearch("");
    setPicked([]);
  };

  const create = useMutation({
    mutationFn: (body) => axios.post(endpoints.develop.eval.groupEvals, body),
    meta: { errorHandled: true },
    onSuccess: (res) => {
      const id = res?.data?.result?.id;
      queryClient.invalidateQueries({ queryKey: ["eval-groups"] });
      reset();
      onClose();
      if (id) navigate(`/dashboard/evaluations/groups/${id}`);
    },
  });

  const toggle = (t) =>
    setPicked((prev) =>
      prev.some((p) => p.id === t.id)
        ? prev.filter((p) => p.id !== t.id)
        : [...prev, t],
    );

  const canSave = name.trim().length > 0 && picked.length > 0;

  return (
    <Dialog
      open={open}
      onClose={() => {
        create.reset();
        onClose();
      }}
      maxWidth="sm"
      fullWidth
      aria-labelledby="new-eval-group-title"
    >
      <DialogTitle id="new-eval-group-title">New group</DialogTitle>
      <DialogContent>
        <Stack gap={2} sx={{ pt: 1 }}>
          <TextField
            label="Name"
            size="small"
            required
            value={name}
            onChange={(e) => setName(e.target.value)}
            inputProps={{ "aria-label": "Group name" }}
          />
          <TextField
            label="Description"
            size="small"
            multiline
            minRows={2}
            value={description}
            onChange={(e) => setDescription(e.target.value)}
            inputProps={{ "aria-label": "Group description" }}
          />
          <Box>
            <Typography variant="subtitle2" sx={{ mb: 1 }}>
              Evaluations in this group ({picked.length})
            </Typography>
            {picked.length > 0 && (
              <Stack direction="row" flexWrap="wrap" gap={0.5} sx={{ mb: 1 }}>
                {picked.map((t) => (
                  <Chip
                    key={t.id}
                    size="small"
                    label={t.name}
                    onDelete={() => toggle(t)}
                  />
                ))}
              </Stack>
            )}
            <TextField
              size="small"
              fullWidth
              placeholder="Search evaluations"
              value={search}
              onChange={(e) => setSearch(e.target.value)}
              inputProps={{ "aria-label": "Search evaluations" }}
            />
            <Box
              sx={{
                mt: 1,
                maxHeight: 260,
                overflowY: "auto",
                border: 1,
                borderColor: "divider",
                borderRadius: 1,
              }}
            >
              {isLoading ? (
                <Box sx={{ display: "flex", justifyContent: "center", p: 2 }}>
                  <CircularProgress size={20} />
                </Box>
              ) : templates.length === 0 ? (
                <Typography
                  variant="body2"
                  color="text.secondary"
                  sx={{ p: 2 }}
                >
                  No evaluations match &quot;{debounced}&quot;.
                </Typography>
              ) : (
                <List dense disablePadding>
                  {templates.map((t) => {
                    const checked = picked.some((p) => p.id === t.id);
                    return (
                      <ListItemButton
                        key={t.id}
                        onClick={() => toggle(t)}
                        selected={checked}
                      >
                        <ListItemIcon sx={{ minWidth: 32 }}>
                          <Checkbox
                            edge="start"
                            size="small"
                            checked={checked}
                            tabIndex={-1}
                            disableRipple
                            inputProps={{ "aria-label": t.name }}
                          />
                        </ListItemIcon>
                        <ListItemText
                          primary={t.name}
                          secondary={t.description || null}
                          secondaryTypographyProps={{ noWrap: true }}
                        />
                      </ListItemButton>
                    );
                  })}
                </List>
              )}
            </Box>
          </Box>
          {create.isError && (
            <Alert severity="error">{errorText(create.error)}</Alert>
          )}
        </Stack>
      </DialogContent>
      <DialogActions>
        <Button
          onClick={() => {
            create.reset();
            onClose();
          }}
        >
          Cancel
        </Button>
        <Button
          variant="contained"
          disabled={!canSave || create.isPending}
          onClick={() =>
            create.mutate({
              name: name.trim(),
              description: description.trim(),
              eval_template_ids: picked.map((t) => t.id),
            })
          }
        >
          {create.isPending ? "Creating…" : "Create group"}
        </Button>
      </DialogActions>
    </Dialog>
  );
}

NewGroupDialog.propTypes = {
  open: PropTypes.bool,
  onClose: PropTypes.func.isRequired,
};
