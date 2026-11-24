import PropTypes from "prop-types";
import { useMemo, useState } from "react";
import {
  Box,
  Button,
  Divider,
  Popover,
  Stack,
  TextField,
  Typography,
} from "@mui/material";
import { enqueueSnackbar } from "notistack";
import Iconify from "src/components/iconify";
import { TagChip } from "src/components/tag-picker";
import {
  useChangeScenarioTags,
  useEnvironmentTags,
} from "src/api/simulate-environments/scenarioTags";
import { SCENARIO_TAGS_COPY as C } from "./scenarios.constants";

export default function BulkTagMenu({
  envId,
  anchorEl,
  onClose,
  count,
  resolveKeys,
  onDone,
}) {
  const open = Boolean(anchorEl);
  const [search, setSearch] = useState("");
  const [newTag, setNewTag] = useState("");
  const known = useEnvironmentTags(envId, { enabled: open });
  const change = useChangeScenarioTags(envId);

  const shown = useMemo(() => {
    const q = search.trim().toLowerCase();
    return (known.data || []).filter(
      (tag) => !q || tag.toLowerCase().includes(q),
    );
  }, [known.data, search]);

  const apply = async ({ add = [], remove = [] }) => {
    let scenarioKeys;
    try {
      scenarioKeys = (await resolveKeys()).filter(Boolean);
    } catch {
      enqueueSnackbar(C.failed, { variant: "error" });
      return;
    }
    if (!scenarioKeys.length) return;
    change.mutate(
      { scenarioKeys, add, remove },
      {
        onSuccess: () => {
          enqueueSnackbar(
            add.length
              ? C.added(add[0], scenarioKeys.length)
              : C.removed(remove[0], scenarioKeys.length),
            { variant: "success" },
          );
          setSearch("");
          setNewTag("");
          onDone?.();
        },
        onError: () => enqueueSnackbar(C.failed, { variant: "error" }),
      },
    );
  };

  const addNew = () => {
    const tag = newTag.trim();
    if (!tag) return;
    setNewTag("");
    apply({ add: [tag] });
  };

  const close = () => {
    setSearch("");
    setNewTag("");
    onClose();
  };

  return (
    <Popover
      open={open}
      anchorEl={anchorEl}
      onClose={close}
      anchorOrigin={{ vertical: "bottom", horizontal: "right" }}
      transformOrigin={{ vertical: "top", horizontal: "right" }}
      slotProps={{ paper: { sx: { width: 280, borderRadius: "8px" } } }}
    >
      <Box sx={{ p: 1.5, pb: 1 }}>
        <Typography sx={{ typography: "s1", fontWeight: 600 }}>
          {C.bulkTitle(count)}
        </Typography>
        <TextField
          size="small"
          fullWidth
          placeholder={C.search}
          value={search}
          onChange={(event) => setSearch(event.target.value)}
          autoFocus
          sx={{ mt: 1 }}
          InputProps={{ sx: { typography: "s1", height: 32 } }}
        />
      </Box>
      <Box sx={{ maxHeight: 220, overflow: "auto", px: 1 }}>
        {shown.map((tag) => (
          <Stack
            key={tag}
            direction="row"
            alignItems="center"
            spacing={1}
            sx={{ px: 0.5, py: 0.5 }}
          >
            <Box sx={{ flex: 1, minWidth: 0 }}>
              <TagChip tag={tag} />
            </Box>
            <Button
              size="small"
              disabled={change.isPending}
              onClick={() => apply({ add: [tag] })}
              sx={{ minWidth: 0, color: "text.secondary" }}
            >
              {C.add}
            </Button>
            <Button
              size="small"
              disabled={change.isPending}
              onClick={() => apply({ remove: [tag] })}
              sx={{ minWidth: 0, color: "text.secondary" }}
            >
              {C.remove}
            </Button>
          </Stack>
        ))}
        {!known.isLoading && shown.length === 0 && (
          <Typography
            sx={{
              typography: "s1",
              color: "text.disabled",
              textAlign: "center",
              py: 1,
            }}
          >
            {known.isError ? C.unavailable : C.none}
          </Typography>
        )}
      </Box>
      <Divider sx={{ mt: 0.5 }} />
      <Box sx={{ p: 1 }}>
        <TextField
          size="small"
          fullWidth
          placeholder={C.newTag}
          value={newTag}
          onChange={(event) => setNewTag(event.target.value)}
          onKeyDown={(event) => {
            if (event.key === "Enter") {
              event.preventDefault();
              addNew();
            }
          }}
          InputProps={{
            startAdornment: (
              <Iconify
                icon="solar:add-linear"
                width={14}
                sx={{ color: "primary.main", mr: 0.5 }}
              />
            ),
            sx: { typography: "s1", height: 32 },
          }}
        />
      </Box>
    </Popover>
  );
}

BulkTagMenu.propTypes = {
  envId: PropTypes.string.isRequired,
  anchorEl: PropTypes.object,
  onClose: PropTypes.func.isRequired,
  count: PropTypes.number.isRequired,
  resolveKeys: PropTypes.func.isRequired,
  onDone: PropTypes.func,
};
