import React, { useState } from "react";
import PropTypes from "prop-types";
import Box from "@mui/material/Box";
import Tab from "@mui/material/Tab";
import Tabs from "@mui/material/Tabs";
import Typography from "@mui/material/Typography";
import { useSnackbar } from "notistack";
import { Navigate, useNavigate, useParams } from "react-router-dom";

import { OMEGA_STATE } from "src/sections/omega/shared";

import CodeButton from "../code/CodeButton";
import {
  creatorOnlyWithheld,
  sandboxDetailRequests,
} from "../code/sandboxRequests";
import { CREATOR_ONLY_NOTE, useMayOperateSandboxes } from "../code/sandboxRole";
import { useSandbox, useSandboxList, useSandboxMutation } from "./api/queries";
import CopyIdButton from "./components/CopyIdButton";
import RelativeTime from "./components/RelativeTime";
import SandboxErrorState from "./components/SandboxErrorState";
import SandboxPageFrame from "./components/SandboxPageFrame";
import { QuietTag, RuntimeChip, StateChip } from "./components/SandboxStatus";
import SessionGate, { LoadingRows } from "./components/SessionGate";
import ActivityTab from "./detail/ActivityTab";
import DetailActions from "./detail/DetailActions";
import FilesTab from "./detail/FilesTab";
import OverviewTab from "./detail/OverviewTab";
import RunTab from "./detail/RunTab";
import SnapshotsTab from "./detail/SnapshotsTab";
import { sandboxErrorText } from "./lib/offerCopy";
import useNow from "./lib/useNow";
import {
  SANDBOX_TABS,
  isSandboxId,
  sandboxPath,
  sandboxesPath,
} from "./lib/links";

const TAB_LABELS = {
  overview: "Overview",
  files: "Files",
  run: "Run",
  snapshots: "Snapshots",
  activity: "Activity",
};

const BACK = { href: sandboxesPath(), label: "Sandboxes" };

// A deleted sandbox keeps its record and its activity, but it has no files to
// browse, no container to run commands in and no snapshots to restore, so
// only Overview and Activity are drawn; any other tab's URL lands on Activity.
const DELETED_TABS = ["overview", "activity"];

// A refusal of the route itself on one sandbox's URL means the id cannot name a
// sandbox (a mistyped link): it is "not found", not "Sandboxes can't be opened".
const ROUTE_REFUSALS = new Set(["route_not_in_manifest", "path_refused"]);
const isRouteRefusal = (error) =>
  error?.state === OMEGA_STATE.REFUSED &&
  (ROUTE_REFUSALS.has(error.code) || ROUTE_REFUSALS.has(error.reason));

function DetailBody({ id, tab }) {
  const navigate = useNavigate();
  const { enqueueSnackbar } = useSnackbar();
  const query = useSandbox(id);
  const caps = useSandboxList().data?.caps;
  const now = useNow();
  // EE lets only a sandbox's creator run, snapshot, hibernate or resume it.
  const mayOperate = useMayOperateSandboxes();
  const resume = useSandboxMutation((api) => api.resume(id), {
    onError: (error) =>
      enqueueSnackbar(sandboxErrorText(error), { variant: "error" }),
  });
  const sandbox = query.data;
  const current = SANDBOX_TABS.includes(tab) ? tab : "overview";
  // Set from the moment Delete is confirmed, cleared if the DELETE fails (DetailActions onLeaving).
  const [leaving, setLeaving] = useState(false);

  if (query.isLoading) {
    return (
      <SandboxPageFrame
        title="Sandbox"
        back={BACK}
        testId="omega-tab-sandbox-detail"
      >
        <LoadingRows />
      </SandboxPageFrame>
    );
  }
  if (!sandbox) {
    return (
      <SandboxPageFrame
        title="Sandbox"
        back={BACK}
        testId="omega-tab-sandbox-detail"
      >
        <SandboxErrorState
          error={
            isRouteRefusal(query.error)
              ? { state: OMEGA_STATE.NOT_FOUND }
              : query.error
          }
          onRetry={() => query.refetch()}
        />
      </SandboxPageFrame>
    );
  }

  const tabs = sandbox.state === "destroyed" ? DELETED_TABS : SANDBOX_TABS;
  if (!tabs.includes(current)) {
    // Deleted from this page: it is already on its way to the list, and a
    // redirect now would race that navigation (DEMO-POLISH-3). That holds
    // from the confirm on, so a poll that reads "destroyed" while the DELETE
    // is still in flight draws a loading frame too.
    if (leaving) {
      return (
        <SandboxPageFrame
          title={sandbox.name}
          back={BACK}
          testId="omega-tab-sandbox-detail"
        >
          <LoadingRows />
        </SandboxPageFrame>
      );
    }
    return <Navigate to={sandboxPath(sandbox.id, "activity")} replace />;
  }
  const resumeProps = {
    onResume: () => resume.mutate(),
    resuming: resume.isPending,
  };
  const panels = {
    overview: <OverviewTab sandbox={sandbox} caps={caps} />,
    files: <FilesTab sandbox={sandbox} {...resumeProps} />,
    run: <RunTab sandbox={sandbox} {...resumeProps} />,
    snapshots: <SnapshotsTab sandbox={sandbox} />,
    activity: <ActivityTab sandbox={sandbox} />,
  };

  return (
    <SandboxPageFrame
      testId="omega-tab-sandbox-detail"
      title={sandbox.name}
      documentTitle={`${sandbox.name} · Sandboxes`}
      back={BACK}
      meta={
        <>
          <StateChip state={sandbox.state} />
          <RuntimeChip provider={sandbox.provider} />
          {sandbox.readOnly && <QuietTag>Agent run · read-only</QuietTag>}
          <Box
            component="span"
            sx={{ display: "inline-flex", alignItems: "center", gap: 0.25 }}
          >
            <Typography
              typography="s2"
              color="text.secondary"
              sx={{ fontFamily: "monospace", overflowWrap: "anywhere" }}
              data-testid="sandbox-id"
            >
              {sandbox.id}
            </Typography>
            <CopyIdButton value={sandbox.id} />
          </Box>
          <Typography
            typography="s2"
            color="text.secondary"
            data-testid="sandbox-times"
          >
            <RelativeTime
              prefix="Created "
              iso={sandbox.createdAt}
              now={now}
              testId="sandbox-created"
              focusable
            />
            {" · "}
            <RelativeTime
              prefix="Updated "
              iso={sandbox.updatedAt}
              now={now}
              testId="sandbox-updated"
              focusable
            />
          </Typography>
        </>
      }
      action={
        <>
          <CodeButton
            requests={sandboxDetailRequests(sandbox, current, { mayOperate })}
            subject="this sandbox"
            note={
              creatorOnlyWithheld(sandbox, { mayOperate })
                ? CREATOR_ONLY_NOTE
                : null
            }
          />
          <DetailActions sandbox={sandbox} onLeaving={setLeaving} />
        </>
      }
    >
      <Box sx={{ borderBottom: "1px solid", borderColor: "divider", mb: 2 }}>
        <Tabs
          value={current}
          onChange={(_, value) => navigate(sandboxPath(sandbox.id, value))}
          variant="scrollable"
          scrollButtons={false}
          aria-label="Sandbox sections"
          sx={{
            minHeight: 40,
            "& .MuiTab-root": {
              minHeight: 40,
              px: 0,
              mr: { xs: 2.5, sm: 3 },
              typography: "s1",
              fontWeight: 500,
            },
          }}
        >
          {tabs.map((key) => (
            <Tab
              key={key}
              value={key}
              label={TAB_LABELS[key]}
              data-testid={`tab-${key}`}
              disableRipple
            />
          ))}
        </Tabs>
      </Box>
      <Box
        role="tabpanel"
        data-testid={`panel-${current}`}
        sx={{ minWidth: 0 }}
      >
        {panels[current]}
      </Box>
    </SandboxPageFrame>
  );
}

DetailBody.propTypes = {
  id: PropTypes.string.isRequired,
  tab: PropTypes.string,
};

export default function SandboxDetailPage() {
  const { id, tab } = useParams();
  // A tab this page does not have (an old /services link, a typo) goes to the
  // sandbox itself, so its URL never shows a section that does not exist.
  if (isSandboxId(id) && tab && !SANDBOX_TABS.includes(tab)) {
    return <Navigate to={sandboxPath(id)} replace />;
  }
  if (!isSandboxId(id)) {
    return (
      <SandboxPageFrame
        title="Sandbox"
        back={BACK}
        testId="omega-tab-sandbox-detail"
      >
        <SandboxErrorState error={{ state: OMEGA_STATE.NOT_FOUND }} />
      </SandboxPageFrame>
    );
  }
  return (
    <SessionGate
      fallbackFrame={(node) => (
        <SandboxPageFrame
          title="Sandbox"
          back={BACK}
          testId="omega-tab-sandbox-detail"
        >
          {node}
        </SandboxPageFrame>
      )}
    >
      <DetailBody key={id} id={id} tab={tab} />
    </SessionGate>
  );
}
