import PropTypes from "prop-types";
import { useState } from "react";
import { Box, Button, Stack, TextField, Typography } from "@mui/material";
import { enqueueSnackbar } from "notistack";
import ContentSkeleton from "src/components/content-skeleton";
import { ConfirmDialog } from "src/components/custom-dialog";
import {
  useDecideChanges,
  useDismissFailedChange,
  useDraftReview,
} from "src/api/simulate-environments/draftReview";
import { useEnvironmentVersions } from "src/api/simulate-environments/versions";
import SideDrawer from "../../components/SideDrawer";
import MockBadge from "../../components/MockBadge";
import { REVIEW_COPY as COPY } from "../workspace.constants";
import ChangeRow from "./ChangeRow";
import FailedChange from "./FailedChange";
import RecheckCard from "./RecheckCard";
import {
  comparabilityLine,
  groupChanges,
  keptChanges,
  recheckedKeys,
  saveLine,
} from "./reviewView";

const NAME_LIMIT = 80;
const cardSx = {
  border: "1px solid",
  borderColor: "divider",
  borderRadius: "8px",
};

function GroupHeading({ children }) {
  return (
    <Typography
      component="h3"
      sx={{ typography: "m3", fontWeight: "fontWeightMedium", mb: 0.75 }}
    >
      {children}
    </Typography>
  );
}
GroupHeading.propTypes = { children: PropTypes.node };

/**
 * Every unsaved change, grouped by what it changes: where it came from, its
 * before and after, and its Undo. Keeping a change accepts it; Undo rejects it.
 * Changes that couldn't apply say why. Save makes the rest a named version.
 */
export default function ReviewChangesDrawer({
  open,
  env,
  canEdit = true,
  onClose,
  onSaved,
  onOpenTab,
}) {
  const { data: review, isLoading } = useDraftReview(env?.id, {
    enabled: open,
  });
  const { data: versions } = useEnvironmentVersions(env?.id, {
    enabled: open && Boolean(env?.versioning?.enabled),
  });
  const decide = useDecideChanges(env?.id);
  const dismiss = useDismissFailedChange(env?.id);
  const [name, setName] = useState("");
  const [discarding, setDiscarding] = useState(false);

  const changes = review?.changes || [];
  const failed = review?.failed || [];
  const kept = keptChanges(changes);
  const names = recheckedKeys(changes);
  const line = saveLine(changes, versions);
  const savedLabel = versions?.defaults?.[line.piece]?.label || "";
  const compared = comparabilityLine(
    names.length,
    review?.sample_scenario_total ?? names.length,
  );

  const goTab = (tab) => {
    onClose?.();
    onOpenTab?.(tab);
  };
  const save = () => {
    if (review?.is_sample) {
      enqueueSnackbar(COPY.sampleSaved, { variant: "info" });
    }
    onSaved?.({ name: name.trim() });
  };
  const discard = () => {
    setDiscarding(false);
    if (review?.is_sample) {
      enqueueSnackbar(COPY.sampleDiscarded, { variant: "info" });
    }
    onClose?.();
  };

  const body = () => {
    if (isLoading) return <ContentSkeleton variant="rows" />;
    if (!kept.length && !failed.length) {
      return (
        <Typography sx={{ typography: "s1", color: "text.secondary" }}>
          {COPY.empty}
        </Typography>
      );
    }
    return (
      <Stack spacing={2}>
        {failed.length > 0 && (
          <Box>
            <GroupHeading>
              {COPY.groupTitle(COPY.failed, failed.length)}
            </GroupHeading>
            <Box sx={cardSx}>
              {failed.map((one) => (
                <FailedChange
                  key={one.id}
                  failed={one}
                  canEdit={canEdit}
                  onFix={() => goTab("contract")}
                  onDismiss={() => dismiss(one.id)}
                />
              ))}
            </Box>
          </Box>
        )}
        {groupChanges(kept).map((group) => (
          <Box key={group.id} component="section" aria-label={group.label}>
            <GroupHeading>
              {COPY.groupTitle(group.label, group.changes.length)}
            </GroupHeading>
            <Box sx={cardSx}>
              {group.changes.map((change) => (
                <ChangeRow
                  key={change.id}
                  change={change}
                  changes={changes}
                  canEdit={canEdit}
                  onUndo={() =>
                    decide.mutate([{ id: change.id, decision: "rejected" }])
                  }
                  onOpen={(link) =>
                    link.kind === "chat" ? onClose?.() : goTab(group.tab)
                  }
                />
              ))}
            </Box>
          </Box>
        ))}
        {names.length > 0 && <RecheckCard names={names} />}
        {kept.length > 0 && (
          <Typography sx={{ typography: "s3", color: "text.tertiary" }}>
            {COPY.comparability(compared)}
          </Typography>
        )}
      </Stack>
    );
  };

  return (
    <SideDrawer open={open} onClose={onClose} width={640}>
      <Stack
        role="dialog"
        aria-label={COPY.title}
        sx={{ height: "100%", minHeight: 0 }}
      >
        <Box
          sx={{
            px: 2,
            py: 1.5,
            pr: 6,
            borderBottom: "1px solid",
            borderColor: "divider",
            flexShrink: 0,
          }}
        >
          <Stack direction="row" alignItems="center" spacing={1}>
            <Typography
              component="h2"
              sx={{ typography: "m1", fontWeight: "fontWeightSemiBold" }}
            >
              {COPY.title}
            </Typography>
            {review?.is_sample && <MockBadge />}
          </Stack>
          <Typography sx={{ typography: "s3", color: "text.tertiary" }}>
            {COPY.subtitle(kept.length, savedLabel)}
          </Typography>
        </Box>

        <Box sx={{ flex: 1, minHeight: 0, overflow: "auto", px: 2, py: 2 }}>
          {body()}
        </Box>

        {canEdit && kept.length > 0 && (
          <Stack
            spacing={1}
            sx={{
              px: 2,
              py: 1.25,
              borderTop: "1px solid",
              borderColor: "divider",
              flexShrink: 0,
            }}
          >
            <TextField
              size="small"
              fullWidth
              label={COPY.nameLabel}
              placeholder={COPY.namePlaceholder}
              value={name}
              onChange={(event) => setName(event.target.value)}
              inputProps={{ maxLength: NAME_LIMIT }}
            />
            <Stack direction="row" alignItems="center" spacing={1.5}>
              <Button
                size="small"
                color="inherit"
                onClick={() => setDiscarding(true)}
              >
                {COPY.discardAll}
              </Button>
              <Typography
                noWrap
                sx={{
                  typography: "s3",
                  color: "text.tertiary",
                  flex: 1,
                  minWidth: 0,
                }}
              >
                {COPY.saveLine(line)}
              </Typography>
              <Button
                size="small"
                variant="contained"
                color="primary"
                onClick={save}
              >
                {COPY.save(line.next)}
              </Button>
            </Stack>
          </Stack>
        )}
      </Stack>
      <ConfirmDialog
        open={discarding}
        onClose={() => setDiscarding(false)}
        title={COPY.discardTitle(kept.length)}
        content={COPY.discardBody(savedLabel)}
        action={
          <Button
            size="small"
            variant="contained"
            color="error"
            onClick={discard}
          >
            {COPY.discard}
          </Button>
        }
      />
    </SideDrawer>
  );
}

ReviewChangesDrawer.propTypes = {
  open: PropTypes.bool,
  env: PropTypes.shape({
    id: PropTypes.string,
    versioning: PropTypes.shape({ enabled: PropTypes.bool }),
  }),
  canEdit: PropTypes.bool,
  onClose: PropTypes.func,
  // ({ name }): the kept changes are saved.
  onSaved: PropTypes.func,
  // (tab id): show the tab that holds a change.
  onOpenTab: PropTypes.func,
};
