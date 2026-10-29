import PropTypes from "prop-types";
import { useNavigate } from "react-router-dom";
import {
  Box,
  Button,
  IconButton,
  Stack,
  Tooltip,
  Typography,
} from "@mui/material";
import Iconify from "src/components/iconify";
import { paths } from "src/routes/paths";
import SideDrawer from "../components/SideDrawer";
import {
  EDIT_AREAS,
  areaOfEdit,
  sourceLabelOf,
  undoEditPatch,
  undoableOf,
} from "../_mock/editReview";

const when = (iso) =>
  iso
    ? new Date(iso).toLocaleTimeString("en-US", {
        hour: "numeric",
        minute: "2-digit",
      })
    : "";

/**
 * Review changes (roadmap builder.md Week 1): the unsaved edits to this
 * environment — from the builder chat, from attached files or by hand —
 * grouped by what they change. Each can be opened where it lives or undone
 * on its own; Save makes them the next environment version.
 */
export default function ReviewChangesDrawer({
  open,
  onClose,
  env,
  envState,
  patch,
  label,
  cost,
  canEdit,
  canDiscard,
  onDiscard,
  onSave,
}) {
  const navigate = useNavigate();
  const list = envState?.unsavedEnvChanges || [];
  if (!open) return <SideDrawer open={false} onClose={onClose} />;
  const groups = [
    ...EDIT_AREAS,
    { id: "other", label: "Other", tab: "settings" },
  ]
    .map((a) => ({
      ...a,
      items: list
        .map((c, i) => ({ c, i }))
        .filter(({ c }) => areaOfEdit(c).id === a.id),
    }))
    .filter((g) => g.items.length);

  return (
    <SideDrawer open onClose={onClose} width={640}>
      <Stack sx={{ height: "100%", minHeight: 0 }}>
        <Stack
          direction="row"
          alignItems="flex-start"
          spacing={1}
          sx={{
            px: 2,
            py: 1.5,
            borderBottom: "1px solid",
            borderColor: "divider",
          }}
        >
          <Box flex={1} minWidth={0}>
            <Typography sx={{ typography: "m3", fontWeight: 600 }}>
              Review changes
            </Typography>
            <Typography sx={{ typography: "s3", color: "text.tertiary" }}>
              {`${list.length} unsaved edit${list.length === 1 ? "" : "s"} since environment ${label}. Runs use ${label} until they're saved.`}
            </Typography>
          </Box>
          <IconButton size="small" aria-label="Close" onClick={onClose}>
            <Iconify icon="mingcute:close-line" width={18} />
          </IconButton>
        </Stack>

        <Box sx={{ flex: 1, minHeight: 0, overflow: "auto", px: 2, py: 2 }}>
          {!list.length ? (
            <Typography sx={{ typography: "s2", color: "text.secondary" }}>
              Nothing waiting. Every edit is saved.
            </Typography>
          ) : (
            <Stack spacing={2}>
              {groups.map((g) => (
                <Box key={g.id}>
                  <Typography
                    sx={{ typography: "s2", fontWeight: 600, mb: 0.75 }}
                  >
                    {`${g.label} · ${g.items.length}`}
                  </Typography>
                  <Box
                    sx={{
                      border: "1px solid",
                      borderColor: "divider",
                      borderRadius: "8px",
                    }}
                  >
                    {g.items.map(({ c, i }) => {
                      const u = undoableOf(list, i);
                      const src = sourceLabelOf(c);
                      return (
                        <Stack
                          key={`${c.at}-${i}`}
                          direction="row"
                          alignItems="center"
                          spacing={1}
                          sx={{
                            px: 1.5,
                            minHeight: 44,
                            py: 0.5,
                            "& + &": {
                              borderTop: "1px solid",
                              borderColor: "divider",
                            },
                          }}
                        >
                          <Box flex={1} minWidth={0}>
                            <Typography
                              noWrap
                              sx={{ typography: "s2" }}
                              title={c.detail}
                            >
                              {c.detail || g.label}
                            </Typography>
                            <Typography
                              noWrap
                              sx={{ typography: "s3", color: "text.tertiary" }}
                            >
                              {[src, when(c.at)].filter(Boolean).join(" · ")}
                            </Typography>
                          </Box>
                          <Button
                            size="small"
                            color="inherit"
                            onClick={() => {
                              onClose();
                              navigate(
                                paths.dashboard.simulate.environmentStep(
                                  env.id,
                                  g.tab,
                                ),
                              );
                            }}
                            sx={{ minWidth: 0, color: "text.secondary" }}
                          >
                            Open
                          </Button>
                          {canEdit && (
                            <Tooltip arrow title={u.ok ? "" : u.why}>
                              <span>
                                <Button
                                  size="small"
                                  color="inherit"
                                  disabled={!u.ok}
                                  startIcon={
                                    <Iconify
                                      icon="solar:undo-left-round-linear"
                                      width={14}
                                    />
                                  }
                                  onClick={() =>
                                    patch(undoEditPatch(envState, i))
                                  }
                                  sx={{ minWidth: 0 }}
                                >
                                  Undo
                                </Button>
                              </span>
                            </Tooltip>
                          )}
                        </Stack>
                      );
                    })}
                  </Box>
                </Box>
              ))}
              {cost?.comparability && (
                <Typography sx={{ typography: "s3", color: "text.tertiary" }}>
                  {cost.comparability}
                </Typography>
              )}
            </Stack>
          )}
        </Box>

        {canEdit && list.length > 0 && (
          <Stack
            direction="row"
            alignItems="center"
            spacing={1.5}
            sx={{
              px: 2,
              py: 1.25,
              borderTop: "1px solid",
              borderColor: "divider",
            }}
          >
            <Tooltip
              arrow
              title={
                canDiscard
                  ? ""
                  : `Environment ${label} didn't keep its settings, so these can't be discarded in one step. Undo them one by one, or save them.`
              }
            >
              <span>
                <Button
                  size="small"
                  color="inherit"
                  disabled={!canDiscard}
                  onClick={onDiscard}
                >
                  Discard all
                </Button>
              </span>
            </Tooltip>
            <Typography
              sx={{
                typography: "s3",
                color: "text.tertiary",
                flex: 1,
                minWidth: 0,
              }}
              noWrap
            >
              {cost?.text}
            </Typography>
            <Button
              size="small"
              variant="contained"
              color="primary"
              onClick={onSave}
            >
              {`Save as environment ${cost?.label}`}
            </Button>
          </Stack>
        )}
      </Stack>
    </SideDrawer>
  );
}

ReviewChangesDrawer.propTypes = {
  open: PropTypes.bool,
  onClose: PropTypes.func.isRequired,
  env: PropTypes.object,
  envState: PropTypes.object,
  patch: PropTypes.func,
  label: PropTypes.string,
  cost: PropTypes.object,
  canEdit: PropTypes.bool,
  canDiscard: PropTypes.bool,
  onDiscard: PropTypes.func,
  onSave: PropTypes.func,
};
