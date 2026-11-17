import React, { useEffect, useState } from "react";
import PropTypes from "prop-types";
import {
  Alert,
  Autocomplete,
  Box,
  Button,
  Checkbox,
  FormControlLabel,
  Dialog,
  DialogActions,
  DialogContent,
  DialogTitle,
  IconButton,
  Stack,
  TextField,
  Typography,
} from "@mui/material";
import { LoadingButton } from "@mui/lab";
import axios, { endpoints } from "src/utils/axios";
import Iconify from "src/components/iconify";
import CustomTooltip from "src/components/tooltip";
import { notify } from "src/sections/common/ux/toast";
import { KeyCap } from "src/sections/common/RowPanel/ShortcutSheet";
import VersionTagChip from "src/sections/common/Datasets/VersionTagChip";
import { VersionNumber } from "src/sections/common/Datasets/VersionChip";
import {
  apiErrorMessage,
  useDatasetVersions,
  useUpdateDatasetVersion,
} from "src/api/develop/dataset-versions";
import { sectionLabelSx } from "./ChangeParts";
import {
  ENVIRONMENT_LABELS,
  ENVIRONMENT_NOTE_REQUIRED,
  allTags,
  isEnvironmentTag,
  tagError,
  tagMoveText,
} from "./versionText";

/**
 * Tag version… (PRODUCT-SPEC §3.5): set or move a tag onto a version
 * (N5 PATCH `{tag}`), or remove one of its tags. A tag names one version;
 * experiments keep the version id they ran on, so moving a tag never
 * changes what a run used.
 * R3 (RV#37): the dataset's other tags show as chips (`golden-v3 · on v4`;
 * a click fills the field and the helper says it moves); the `x` says
 * what it removes; Enter saves; removing offers Undo.
 */
const TagVersionDialog = ({ open, datasetId, version, onClose }) => {
  const { data: versions = [] } = useDatasetVersions(datasetId);
  const update = useUpdateDatasetVersion(datasetId);
  const [tag, setTag] = useState("");
  const [removing, setRemoving] = useState(null);
  const [error, setError] = useState(null);
  const [listOpen, setListOpen] = useState(false);
  const [note, setNote] = useState("");
  const [makeEnv, setMakeEnv] = useState(false);

  useEffect(() => {
    if (open) {
      setTag("");
      setNote("");
      setMakeEnv(false);
      setRemoving(null);
      setError(null);
    }
  }, [open, version?.id]);

  // The dialog may stay open across an N1 refetch; read the fresh tags.
  const current = versions.find((v) => v.id === version?.id) ??
    version ?? { tags: [] };
  const tags = current.tags ?? [];
  const value = tag.trim();
  const problem = tagError(value);
  const help = problem ?? tagMoveText(value, versions, current.number);
  const holderOf = (t) =>
    versions.find((v) => (v.tags ?? []).includes(t))?.number;
  const others = allTags(versions).filter((t) => !tags.includes(t));
  // W3-9: an environment moves with a note. A brand-new tag can become one
  // (`Make it an environment`).
  const knownTag = allTags(versions).includes(value);
  const canMakeEnv =
    Boolean(value) &&
    !tagError(value) &&
    !knownTag &&
    !isEnvironmentTag(value, versions);
  const env =
    Boolean(value) &&
    (isEnvironmentTag(value, versions) || (canMakeEnv && makeEnv));
  const envHolder = env ? holderOf(value) : null;
  const noteMissing = env && !note.trim();
  const canSaveTag = (t) =>
    Boolean(t) &&
    !tagError(t) &&
    !tags.includes(t) &&
    !((isEnvironmentTag(t, versions) || (t === value && env)) && !note.trim());
  const canSave = canSaveTag(value);

  const save = async (raw = value) => {
    const next = String(raw ?? "")
      .trim()
      .toLowerCase();
    if (!canSaveTag(next) || update.isPending) return;
    setError(null);
    try {
      const asEnv = isEnvironmentTag(next, versions) || (next === value && env);
      if (asEnv && !isEnvironmentTag(next, versions)) {
        // Register the new environment first (the server then asks for
        // the note on every move).
        const known = [
          ...new Set(
            versions.flatMap((v) =>
              (v.labels ?? [])
                .filter((l) => l.kind === "environment")
                .map((l) => l.name),
            ),
          ),
        ].filter((n) => !ENVIRONMENT_LABELS.includes(n));
        await axios.put(endpoints.develop.updateDataset(datasetId), {
          dataset_config: { environment_labels: [...known, next] },
        });
      }
      const res = await update.mutateAsync({
        versionId: current.id,
        tag: next,
        ...(asEnv ? { note: note.trim() } : {}),
      });
      const from = versions.find((v) => v.id === res?.tag_moved_from);
      notify(
        from
          ? `${next} moved from v${from.number} to v${current.number}`
          : `Tagged v${current.number} ${next}`,
        { variant: "success", key: "version-tag" },
      );
      onClose();
    } catch (e) {
      setError(apiErrorMessage(e, "Couldn't save the tag."));
    }
  };

  const remove = async (name) => {
    setError(null);
    try {
      await update.mutateAsync({ versionId: current.id, tag: null });
      // N5 `tag: null` clears every tag on the version; put the others back.
      const keep = tags.filter((t) => t !== name);
      for (const t of keep) {
        // eslint-disable-next-line no-await-in-loop
        await update.mutateAsync({ versionId: current.id, tag: t });
      }
      setRemoving(null);
      const versionId = current.id;
      notify(`Removed ${name} from v${current.number}`, {
        variant: "success",
        key: "version-tag",
        undo: {
          onUndo: () =>
            update.mutateAsync({ versionId, tag: name }).catch((e) =>
              notify(apiErrorMessage(e, "Couldn't put the tag back."), {
                variant: "error",
              }),
            ),
        },
      });
    } catch (e) {
      setError(apiErrorMessage(e, "Couldn't remove the tag."));
    }
  };

  return (
    <Dialog
      open={open}
      onClose={update.isPending ? undefined : onClose}
      maxWidth={false}
      PaperProps={{ sx: { width: 480, maxWidth: "calc(100vw - 32px)" } }}
      onKeyDown={(e) => {
        if ((e.metaKey || e.ctrlKey) && e.key === "Enter") {
          e.preventDefault();
          save();
        }
      }}
    >
      <DialogTitle sx={{ p: 2, pb: 1 }}>
        <Stack direction="row" alignItems="center" gap={1}>
          <Typography typography="m2" fontWeight="fontWeightBold">
            Tag version
          </Typography>
          <VersionNumber number={current.number} />
          <Box sx={{ flex: 1 }} />
          <IconButton onClick={onClose} aria-label="Close">
            <Iconify icon="mdi:close" />
          </IconButton>
        </Stack>
        <Typography
          typography="s2"
          color="text.secondary"
          sx={{ mt: 0.25 }}
          noWrap
        >
          {current.description}
        </Typography>
      </DialogTitle>
      <DialogContent sx={{ px: 2, pb: 1 }}>
        {error && (
          <Alert severity="error" sx={{ mb: 1.5 }}>
            {error}
          </Alert>
        )}
        <Typography sx={{ ...sectionLabelSx, mb: 0.75 }}>
          Tags on v{current.number}
        </Typography>
        {tags.length ? (
          <Stack gap={0.75} sx={{ mb: 2 }}>
            {tags.map((t) => (
              <Stack key={t} direction="row" alignItems="center" gap={1}>
                <VersionTagChip name={t} size="medium" />
                <Box sx={{ flex: 1 }} />
                {removing === t ? (
                  <>
                    <Typography typography="s2" color="text.secondary">
                      Remove {t}?
                    </Typography>
                    <Button
                      size="small"
                      color="inherit"
                      onClick={() => setRemoving(null)}
                    >
                      Keep
                    </Button>
                    <LoadingButton
                      size="small"
                      color="error"
                      variant="outlined"
                      loading={update.isPending}
                      onClick={() => remove(t)}
                    >
                      Remove
                    </LoadingButton>
                  </>
                ) : (
                  <CustomTooltip
                    show
                    size="small"
                    placement="top"
                    arrow
                    title={`Remove ${t} from v${current.number}`}
                  >
                    <IconButton
                      size="small"
                      aria-label={`Remove ${t} from v${current.number}`}
                      onClick={() => setRemoving(t)}
                      sx={{ color: "text.secondary" }}
                    >
                      <Iconify icon="mdi:close" width={16} />
                    </IconButton>
                  </CustomTooltip>
                )}
              </Stack>
            ))}
          </Stack>
        ) : (
          <Typography typography="s2" color="text.secondary" sx={{ mb: 2 }}>
            No tags yet.
          </Typography>
        )}
        <Typography sx={{ ...sectionLabelSx, mb: 0.75 }}>
          Add or move a tag
        </Typography>
        <Autocomplete
          freeSolo
          size="small"
          options={others}
          inputValue={tag}
          open={listOpen}
          onOpen={() => setListOpen(true)}
          onClose={() => setListOpen(false)}
          onInputChange={(_, v) => setTag((v ?? "").toLowerCase())}
          // Enter with the list open: a typed tag saves at once; picking a
          // suggestion fills the field (Enter again moves it).
          onChange={(_, v, reason) => {
            if (reason === "createOption" && v) save(v);
            else if (reason === "selectOption" && v) setTag(String(v));
          }}
          renderInput={(params) => (
            <TextField
              {...params}
              autoFocus
              onKeyDown={(e) => {
                // Enter saves once the suggestion list is closed.
                if (
                  e.key === "Enter" &&
                  !listOpen &&
                  !e.metaKey &&
                  !e.ctrlKey
                ) {
                  e.preventDefault();
                  save();
                }
              }}
              placeholder="e.g. golden-v4"
              error={Boolean(problem)}
              helperText={
                help ??
                "Pick an existing tag to move it here, or type a new one."
              }
              inputProps={{ ...params.inputProps, "aria-label": "Tag" }}
            />
          )}
        />
        {canMakeEnv && (
          <FormControlLabel
            sx={{ mt: 0.75, ml: 0 }}
            control={
              <Checkbox
                size="small"
                checked={makeEnv}
                onChange={(e) => setMakeEnv(e.target.checked)}
                inputProps={{ "data-testid": "make-environment" }}
              />
            }
            label={
              <Typography typography="s2">
                Make it an environment (on one version at a time; moves need a
                note)
              </Typography>
            }
          />
        )}
        {env && (
          <Stack
            gap={0.75}
            sx={{
              mt: 1.25,
              p: 1.25,
              borderRadius: 1,
              bgcolor: "background.neutral",
            }}
            data-testid="environment-move"
          >
            <Stack direction="row" alignItems="center" gap={0.75}>
              <Box
                sx={{
                  width: 8,
                  height: 8,
                  borderRadius: "50%",
                  bgcolor:
                    value === "production" ? "success.main" : "info.main",
                }}
              />
              <Typography typography="s2" fontWeight={600}>
                {envHolder
                  ? `Move ${value} from v${envHolder} to v${current.number}?`
                  : `Put ${value} on v${current.number}?`}
              </Typography>
              <Typography typography="s3" color="text.secondary">
                Environment
              </Typography>
            </Stack>
            <Typography typography="s3" color="text.secondary">
              An environment is on one version at a time. Moving it needs a
              note.
            </Typography>
            <TextField
              size="small"
              required
              label="Note"
              placeholder="Ship v6 after smoke passed"
              value={note}
              onChange={(e) => setNote(e.target.value)}
              inputProps={{ "data-testid": "environment-note", maxLength: 200 }}
            />
          </Stack>
        )}
        {others.length > 0 && (
          <Stack
            direction="row"
            alignItems="center"
            gap={0.75}
            data-testid="tag-suggestions"
            sx={{ mt: 1.25, flexWrap: "wrap" }}
          >
            <Typography typography="s2" color="text.secondary">
              On other versions
            </Typography>
            {others.map((t) => (
              <CustomTooltip
                key={t}
                show
                size="small"
                placement="top"
                arrow
                title={`Move ${t} from v${holderOf(t)} to v${current.number}`}
              >
                <Box
                  component="button"
                  type="button"
                  onClick={() => setTag(t)}
                  aria-pressed={value === t}
                  sx={{
                    all: "unset",
                    cursor: "pointer",
                    display: "inline-flex",
                    alignItems: "center",
                    gap: 0.5,
                    borderRadius: 0.5,
                    outline: value === t ? "2px solid" : "none",
                    outlineColor: "primary.main",
                    "&:focus-visible": {
                      outline: "2px solid",
                      outlineColor: "primary.main",
                    },
                  }}
                >
                  <VersionTagChip name={t} />
                  <Typography typography="s3" color="text.disabled">
                    on v{holderOf(t)}
                  </Typography>
                </Box>
              </CustomTooltip>
            ))}
          </Stack>
        )}
      </DialogContent>
      <DialogActions sx={{ px: 2, pb: 2, pt: 1 }}>
        <Button
          size="small"
          variant="outlined"
          color="inherit"
          onClick={onClose}
          sx={{ px: 3 }}
        >
          Cancel
        </Button>
        <CustomTooltip
          show={noteMissing}
          size="small"
          placement="top"
          title={ENVIRONMENT_NOTE_REQUIRED}
        >
          <Box component="span" sx={{ display: "inline-flex" }}>
            <LoadingButton
              size="small"
              variant="contained"
              loading={update.isPending && !removing}
              disabled={!canSave}
              onClick={() => save()}
              data-testid="tag-save"
              endIcon={
                <Box
                  component="span"
                  sx={{ display: "inline-flex", opacity: 0.8 }}
                >
                  <KeyCap>↵</KeyCap>
                </Box>
              }
              sx={{
                px: 2,
                "& .MuiButton-endIcon kbd": { height: 18, minWidth: 18 },
              }}
            >
              {env
                ? envHolder
                  ? `Move ${value}`
                  : `Put ${value} here`
                : tagMoveText(value, versions, current.number)?.includes(
                      "moves",
                    )
                  ? "Move tag"
                  : "Save tag"}
            </LoadingButton>
          </Box>
        </CustomTooltip>
      </DialogActions>
    </Dialog>
  );
};

TagVersionDialog.propTypes = {
  open: PropTypes.bool,
  datasetId: PropTypes.string,
  version: PropTypes.object,
  onClose: PropTypes.func,
};

export default TagVersionDialog;
