import React, { useState } from "react";
import PropTypes from "prop-types";
import { useSearchParams } from "react-router-dom";
import {
  Alert,
  Button,
  Dialog,
  DialogActions,
  DialogContent,
  DialogTitle,
  Skeleton,
  Typography,
} from "@mui/material";
import { useOrganization } from "src/contexts/OrganizationContext";
import { useGatewayAdmin } from "../hooks/useGatewayAdmin";
import { useGatewayContext } from "../context/useGatewayContext";
import { parseBudgetLink, resolveBudgetTarget } from "./budgetCapTargets";
import { useBudgetCapEditor } from "./hooks/useBudgetCapEditor";
import BudgetCapForm from "./BudgetCapForm";

function Message({ children, onClose }) {
  return (
    <>
      <DialogContent>
        <Alert severity="warning">{children}</Alert>
      </DialogContent>
      <DialogActions>
        <Button onClick={onClose}>Close</Button>
      </DialogActions>
    </>
  );
}
Message.propTypes = {
  children: PropTypes.node,
  onClose: PropTypes.func.isRequired,
};

function Editor({ link, orgId, gatewayId, onClose }) {
  const { query, mutation } = useBudgetCapEditor({ link, orgId, gatewayId });
  if (query.isLoading || query.isFetching) {
    return (
      <DialogContent>
        <Skeleton height={180} aria-label="Loading cap" />
      </DialogContent>
    );
  }
  const target = query.error ? null : resolveBudgetTarget(link, query.data);
  const error = query.error?.message || target?.error;
  if (error) {
    return (
      <>
        <DialogContent>
          <Alert severity="error">{error}</Alert>
        </DialogContent>
        <DialogActions>
          <Button onClick={onClose}>Close</Button>
          <Button onClick={() => query.refetch()}>Reload cap</Button>
        </DialogActions>
      </>
    );
  }
  return (
    <BudgetCapForm
      key={query.dataUpdatedAt}
      target={target}
      mutation={mutation}
      busy={Boolean(query.isFetching)}
      onClose={onClose}
      onReload={() => {
        mutation.reset?.();
        query.refetch();
      }}
    />
  );
}
Editor.propTypes = {
  link: PropTypes.object.isRequired,
  orgId: PropTypes.string.isRequired,
  gatewayId: PropTypes.string,
  onClose: PropTypes.func.isRequired,
};

function BoundEditor({ link, orgId, gatewayId, onClose }) {
  // Changing the org while a dialog is open never retargets a draft or its HTTP request.
  const [openedOrgId] = useState(orgId);
  if (openedOrgId !== orgId) {
    return (
      <Message onClose={onClose}>
        The organization changed. Close this editor and open the budget link
        again.
      </Message>
    );
  }
  return (
    <Editor link={link} orgId={orgId} gatewayId={gatewayId} onClose={onClose} />
  );
}
BoundEditor.propTypes = Editor.propTypes;

export default function BudgetCapDeepLink() {
  const [params, setParams] = useSearchParams();
  const { currentOrganizationId: orgId, currentOrganizationName: orgName } =
    useOrganization();
  const { canEdit } = useGatewayAdmin();
  const { gatewayId } = useGatewayContext();
  const link = parseBudgetLink(params);
  if (!link) return null;
  const close = () => {
    const next = new URLSearchParams(params);
    ["level", "id", "org"].forEach((key) => next.delete(key));
    setParams(next, { replace: true });
  };
  let message = link.error;
  if (!message && !canEdit)
    message = "Only an organization Owner or Admin can raise this cap.";
  if (!message && !orgId)
    message = "Select the organization that owns this budget before editing.";
  if (!message && link.orgId && link.orgId !== orgId) {
    message =
      "Switch to the organization named in this budget link before editing. No budget was changed.";
  }
  if (!message && link.readOnly) message = link.notice;
  if (!message && link.mode !== "workspace" && !gatewayId)
    message = "Select a gateway before editing this cap.";
  return (
    <Dialog
      open
      onClose={close}
      fullWidth
      maxWidth="sm"
      aria-labelledby="budget-cap-title"
    >
      <DialogTitle id="budget-cap-title">
        Raise budget cap
        <Typography
          component="span"
          variant="body2"
          display="block"
          color="text.secondary"
        >
          {`Organization: ${orgName || orgId || "Not selected"}`}
        </Typography>
      </DialogTitle>
      {message ? (
        <Message onClose={close}>{message}</Message>
      ) : (
        <BoundEditor
          key={params.toString()}
          link={link}
          orgId={orgId}
          gatewayId={gatewayId}
          onClose={close}
        />
      )}
    </Dialog>
  );
}
