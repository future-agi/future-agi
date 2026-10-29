import PropTypes from "prop-types";
import { useState } from "react";
import {
  Box,
  Button,
  ButtonBase,
  Popover,
  Stack,
  TextField,
  Typography,
} from "@mui/material";
import Iconify from "src/components/iconify";
import { ConfirmDialog } from "src/components/custom-dialog";
import CopyCommandButton from "../../components/CopyCommandButton";
import { groupRunPlanOf } from "../../_mock/scenarioOrg";

/* Light tabs, not boxes: only the open group is shaded. */
const pill = (on) => ({
  px: 1,
  height: 24,
  borderRadius: "6px",
  bgcolor: on ? "action.selected" : "transparent",
  typography: "s3",
  fontWeight: 400,
  color: on ? "text.primary" : "text.secondary",
  gap: 0.5,
  flexShrink: 0,
  "&:hover": { bgcolor: "action.hover", color: "text.primary" },
});

/**
 * Groups as playlists (roadmap scenarios.md "Pack or group"): every group
 * the environment has, one click to show only its scenarios, and Run this
 * group — which resolves the scenarios that can run now and lists the ones
 * sitting out, with why, instead of dropping them. A scenario in two groups
 * is still one scenario.
 */
export default function GroupsBar({
  groups,
  rows,
  stateOf,
  total,
  active,
  onPick,
  onCreate,
  onRun,
  selectedCount = 0,
  readOnly = false,
  repeats = 3,
  envId,
}) {
  const [newAnchor, setNewAnchor] = useState(null);
  const [name, setName] = useState("");
  const [confirm, setConfirm] = useState(null);
  const current = groups.find((g) => g.name === active) || null;
  const plan = confirm ? groupRunPlanOf(confirm, rows, stateOf) : null;

  return (
    <Stack
      direction="row"
      alignItems="center"
      spacing={0.75}
      sx={{ mb: 1.25, flexWrap: "wrap", rowGap: 0.75 }}
    >
      <Typography sx={{ typography: "s3", color: "text.tertiary", mr: 0.25 }}>
        Groups
      </Typography>
      <ButtonBase onClick={() => onPick(null)} sx={pill(!current)}>
        All scenarios
        <Box component="span" sx={{ color: "text.tertiary" }}>
          {total}
        </Box>
      </ButtonBase>
      {groups.map((g) => (
        <ButtonBase
          key={g.id}
          onClick={() => onPick(current?.id === g.id ? null : g.name)}
          sx={pill(current?.id === g.id)}
        >
          <Iconify
            icon="solar:folder-2-linear"
            width={14}
            sx={{ color: "text.secondary" }}
          />
          {g.name}
          <Box component="span" sx={{ color: "text.tertiary" }}>
            {g.ids.length}
          </Box>
        </ButtonBase>
      ))}
      {!readOnly && (
        <ButtonBase
          onClick={(e) => setNewAnchor(e.currentTarget)}
          sx={{
            ...pill(false),
            borderStyle: "dashed",
            color: "text.secondary",
          }}
        >
          <Iconify icon="solar:add-circle-linear" width={12} />
          New group
        </ButtonBase>
      )}
      <Box flex={1} />
      {current && (
        <Button
          size="small"
          variant="outlined"
          disabled={readOnly || !current.ids.length}
          onClick={() => setConfirm(current)}
          startIcon={<Iconify icon="solar:play-bold" width={14} />}
          sx={{ color: "text.primary", borderColor: "divider", flexShrink: 0 }}
        >
          {`Run ${current.name}`}
        </Button>
      )}

      <Popover
        open={!!newAnchor}
        anchorEl={newAnchor}
        onClose={() => setNewAnchor(null)}
        anchorOrigin={{ vertical: "bottom", horizontal: "left" }}
        slotProps={{ paper: { sx: { p: 1.5, width: 300, mt: 0.5 } } }}
      >
        <Typography sx={{ typography: "s2", fontWeight: 600, mb: 0.75 }}>
          New group
        </Typography>
        <TextField
          size="small"
          autoFocus
          fullWidth
          placeholder="Refund release gate"
          value={name}
          onChange={(e) => setName(e.target.value)}
        />
        <Typography sx={{ typography: "s3", color: "text.tertiary", mt: 0.75 }}>
          {selectedCount
            ? `Starts with the ${selectedCount} selected scenario${selectedCount === 1 ? "" : "s"}.`
            : "Starts empty — select scenarios and choose Organise → Add to group."}
        </Typography>
        <Stack
          direction="row"
          justifyContent="flex-end"
          spacing={1}
          sx={{ mt: 1.25 }}
        >
          <Button
            size="small"
            onClick={() => setNewAnchor(null)}
            sx={{ color: "text.secondary" }}
          >
            Cancel
          </Button>
          <Button
            size="small"
            variant="contained"
            color="primary"
            disabled={!name.trim()}
            onClick={() => {
              onCreate(name.trim());
              setName("");
              setNewAnchor(null);
            }}
          >
            Create group
          </Button>
        </Stack>
      </Popover>

      {plan && (
        <ConfirmDialog
          open
          onClose={() => setConfirm(null)}
          PaperProps={{ sx: { maxWidth: 560 } }}
          title={`Run ${confirm.name}?`}
          content={
            <Stack spacing={1.25}>
              <Typography sx={{ typography: "s2", color: "text.secondary" }}>
                {`${plan.run.length} of ${confirm.ids.length} scenario${confirm.ids.length === 1 ? "" : "s"} run, ${repeats}× each. The run keeps exactly these — a scenario added to the group later joins the next run, not this one.`}
              </Typography>
              {plan.left.length > 0 && (
                <Box>
                  <Typography
                    sx={{ typography: "s3", fontWeight: 600, mb: 0.5 }}
                  >{`Sitting out · ${plan.left.length}`}</Typography>
                  <Box
                    sx={{
                      border: "1px solid",
                      borderColor: "divider",
                      borderRadius: 1,
                    }}
                  >
                    {plan.left.map((l) => (
                      <Stack
                        key={l.id}
                        direction="row"
                        spacing={1.5}
                        sx={{
                          px: 1.25,
                          py: 0.75,
                          "& + &": {
                            borderTop: "1px solid",
                            borderColor: "divider",
                          },
                        }}
                      >
                        <Typography
                          noWrap
                          sx={{ typography: "s3", flex: 1, minWidth: 0 }}
                        >
                          {l.title}
                        </Typography>
                        <Typography
                          sx={{
                            typography: "s3",
                            color: "text.tertiary",
                            flexShrink: 0,
                            maxWidth: 260,
                          }}
                        >
                          {l.why}
                        </Typography>
                      </Stack>
                    ))}
                  </Box>
                </Box>
              )}
              <Box>
                <CopyCommandButton
                  command={`agent-learn run --env ${envId || "<env>"} --group "${confirm.name}" --attempts ${repeats}`}
                />
              </Box>
            </Stack>
          }
          action={
            <Button
              size="small"
              variant="contained"
              color="primary"
              disabled={!plan.run.length}
              onClick={() => {
                const ids = plan.run;
                setConfirm(null);
                onRun(ids);
              }}
            >
              {`Run ${plan.run.length}`}
            </Button>
          }
        />
      )}
    </Stack>
  );
}

GroupsBar.propTypes = {
  groups: PropTypes.array.isRequired,
  rows: PropTypes.array.isRequired,
  stateOf: PropTypes.func,
  total: PropTypes.number,
  active: PropTypes.string,
  onPick: PropTypes.func.isRequired,
  onCreate: PropTypes.func.isRequired,
  onRun: PropTypes.func.isRequired,
  selectedCount: PropTypes.number,
  readOnly: PropTypes.bool,
  repeats: PropTypes.number,
  envId: PropTypes.string,
};
