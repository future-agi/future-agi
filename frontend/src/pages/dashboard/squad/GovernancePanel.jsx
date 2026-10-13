import { useCallback, useEffect, useState } from "react";
import PropTypes from "prop-types";
import {
  Alert,
  Button,
  Card,
  CardContent,
  Chip,
  CircularProgress,
  Grid,
  List,
  ListItem,
  ListItemText,
  MenuItem,
  Stack,
  TextField,
  Typography,
} from "@mui/material";

function reason(result) {
  return result?.reason || "The remote governance service is unavailable.";
}

function collection(result, key) {
  if (!result?.available) return [];
  if (Array.isArray(result[key])) return result[key];
  if (Array.isArray(result.items)) return result.items;
  return [];
}

function GovernanceResult({ result, children }) {
  if (result?.available === false)
    return <Alert severity="info">{reason(result)}</Alert>;
  return children;
}

GovernanceResult.propTypes = {
  result: PropTypes.shape({
    available: PropTypes.bool,
    reason: PropTypes.string,
  }),
  children: PropTypes.node,
};

export default function GovernancePanel({ client }) {
  const [orgsResult, setOrgsResult] = useState(null);
  const [orgId, setOrgId] = useState("");
  const [detail, setDetail] = useState(null);
  const [loading, setLoading] = useState(false);
  const [inviteEmail, setInviteEmail] = useState("");
  const [inviteRole, setInviteRole] = useState("member");
  const [mutationError, setMutationError] = useState("");
  const [issuedToken, setIssuedToken] = useState("");

  const loadOrgs = useCallback(async () => {
    setLoading(true);
    const result = await client.listOrganizations();
    setOrgsResult(result);
    if (result.available) {
      const orgs = Array.isArray(result.orgs) ? result.orgs : [];
      setOrgId((current) =>
        orgs.some((org) => org.id === current) ? current : orgs[0]?.id || "",
      );
    } else setOrgId("");
    setLoading(false);
  }, [client]);

  const loadDetail = useCallback(
    async (id) => {
      if (!id) {
        setDetail(null);
        return;
      }
      setLoading(true);
      const [members, invites, activity, plan] = await Promise.all([
        client.listOrganizationMembers(id),
        client.listOrganizationInvites(id),
        client.getOrganizationActivity(id, { limit: 25 }),
        client.getOrganizationPlan(id),
      ]);
      setDetail({ members, invites, activity, plan });
      setLoading(false);
    },
    [client],
  );

  useEffect(() => {
    void loadOrgs();
  }, [loadOrgs]);
  useEffect(() => {
    void loadDetail(orgId);
  }, [loadDetail, orgId]);

  const refreshDetail = () => loadDetail(orgId);
  const createInvite = async () => {
    if (!inviteEmail.trim() || !orgId) return;
    setMutationError("");
    setIssuedToken("");
    const result = await client.createOrganizationInvite(
      orgId,
      { email: inviteEmail.trim(), role: inviteRole, manualDelivery: true },
      `squad-invite-${Date.now()}`,
    );
    if (!result.available) {
      setMutationError(reason(result));
      return;
    }
    setInviteEmail("");
    setIssuedToken(typeof result.token === "string" ? result.token : "");
    await refreshDetail();
  };
  const revokeInvite = async (inviteId) => {
    if (
      !inviteId ||
      !orgId ||
      (typeof globalThis.confirm === "function" &&
        !globalThis.confirm("Revoke this pending invite?"))
    )
      return;
    const result = await client.revokeOrganizationInvite(
      orgId,
      inviteId,
      `squad-invite-revoke-${Date.now()}`,
    );
    if (!result.available) setMutationError(reason(result));
    else await refreshDetail();
  };
  const updateRole = async (accountId, role) => {
    if (!accountId || !orgId) return;
    const result = await client.updateOrganizationMemberRole(
      orgId,
      accountId,
      role,
      `squad-role-${accountId}-${Date.now()}`,
    );
    if (!result.available) setMutationError(reason(result));
    else await refreshDetail();
  };
  const removeMember = async (accountId) => {
    if (
      !accountId ||
      !orgId ||
      (typeof globalThis.confirm === "function" &&
        !globalThis.confirm("Remove this organization member?"))
    )
      return;
    const result = await client.removeOrganizationMember(
      orgId,
      accountId,
      `squad-member-remove-${accountId}-${Date.now()}`,
    );
    if (!result.available) setMutationError(reason(result));
    else await refreshDetail();
  };

  const orgs = collection(orgsResult, "orgs");
  const members = collection(detail?.members, "members");
  const invites = collection(detail?.invites, "invites");
  const activity = collection(detail?.activity, "activity");
  const plan = detail?.plan?.available ? detail.plan.plan || detail.plan : null;

  return (
    <Stack spacing={2} aria-label="Organization governance">
      <Stack
        direction={{ xs: "column", sm: "row" }}
        justifyContent="space-between"
        alignItems={{ sm: "center" }}
        gap={1}
      >
        <BoxHeading />
        {orgs.length > 0 ? (
          <TextField
            select
            size="small"
            label="Organization"
            value={orgId}
            onChange={(event) => setOrgId(event.target.value)}
            sx={{ minWidth: 240 }}
          >
            {orgs.map((org) => (
              <MenuItem key={org.id} value={org.id}>
                {org.name || org.id} · {org.role || "member"}
              </MenuItem>
            ))}
          </TextField>
        ) : null}
      </Stack>
      {loading ? <CircularProgress size={22} /> : null}
      <GovernanceResult result={orgsResult}>
        {orgs.length === 0 ? (
          <Typography color="text.secondary">
            No tenant organization is visible to this session.
          </Typography>
        ) : null}
      </GovernanceResult>
      {orgId && detail ? (
        <Grid container spacing={2}>
          {mutationError ? (
            <Grid item xs={12}>
              <Alert severity="error">{mutationError}</Alert>
            </Grid>
          ) : null}
          {issuedToken ? (
            <Grid item xs={12}>
              <Alert severity="warning">
                <Typography variant="subtitle2">
                  One-time invite token
                </Typography>
                <Typography variant="body2">
                  Copy this token now. It is held only in this page&apos;s
                  memory and will not be persisted by Squad.
                </Typography>
                <Typography
                  component="pre"
                  variant="caption"
                  sx={{
                    mt: 1,
                    whiteSpace: "pre-wrap",
                    overflowWrap: "anywhere",
                  }}
                >
                  {issuedToken}
                </Typography>
                <Button
                  size="small"
                  onClick={() =>
                    void globalThis.navigator?.clipboard?.writeText(issuedToken)
                  }
                >
                  Copy token
                </Button>
              </Alert>
            </Grid>
          ) : null}
          <Grid item xs={12} md={6}>
            <Card variant="outlined">
              <CardContent>
                <Typography variant="subtitle2">Members</Typography>
                <Typography
                  variant="body2"
                  color="text.secondary"
                  sx={{ mt: 0.5 }}
                >
                  Server-derived roster; roles and emails are read-only here.
                </Typography>
                <List dense disablePadding sx={{ mt: 0.5 }}>
                  {members.length === 0 ? (
                    <ListItem>
                      <ListItemText primary="No members returned." />
                    </ListItem>
                  ) : (
                    members.map((member) => (
                      <ListItem
                        key={member.accountId || member.id || member.email}
                        divider
                        secondaryAction={
                          <Stack
                            direction="row"
                            spacing={0.5}
                            alignItems="center"
                          >
                            <TextField
                              select
                              size="small"
                              value={member.role || "member"}
                              onChange={(event) =>
                                void updateRole(
                                  member.accountId || member.id,
                                  event.target.value,
                                )
                              }
                              sx={{ minWidth: 105 }}
                            >
                              <MenuItem value="member">Member</MenuItem>
                              <MenuItem value="admin">Admin</MenuItem>
                              <MenuItem value="owner">Owner</MenuItem>
                            </TextField>
                            <Button
                              size="small"
                              color="error"
                              onClick={() =>
                                void removeMember(member.accountId || member.id)
                              }
                            >
                              Remove
                            </Button>
                          </Stack>
                        }
                      >
                        <ListItemText
                          primary={
                            member.email || member.accountId || member.id
                          }
                          secondary={`${member.role || "member"} · ${member.status || "active"}`}
                        />
                      </ListItem>
                    ))
                  )}
                </List>
              </CardContent>
            </Card>
          </Grid>
          <Grid item xs={12} md={6}>
            <Card variant="outlined">
              <CardContent>
                <Typography variant="subtitle2">Pending invites</Typography>
                <Typography
                  variant="body2"
                  color="text.secondary"
                  sx={{ mt: 0.5 }}
                >
                  Invite tokens are never rendered or persisted by Squad.
                </Typography>
                <Stack
                  direction={{ xs: "column", sm: "row" }}
                  spacing={1}
                  sx={{ mt: 1 }}
                >
                  <TextField
                    size="small"
                    label="Invite email"
                    value={inviteEmail}
                    onChange={(event) => setInviteEmail(event.target.value)}
                  />
                  <TextField
                    select
                    size="small"
                    label="Role"
                    value={inviteRole}
                    onChange={(event) => setInviteRole(event.target.value)}
                  >
                    <MenuItem value="member">Member</MenuItem>
                    <MenuItem value="admin">Admin</MenuItem>
                    <MenuItem value="owner">Owner</MenuItem>
                  </TextField>
                  <Button
                    size="small"
                    variant="contained"
                    onClick={() => void createInvite()}
                    disabled={!inviteEmail.trim()}
                  >
                    Invite
                  </Button>
                </Stack>
                <List dense disablePadding sx={{ mt: 0.5 }}>
                  {invites.length === 0 ? (
                    <ListItem>
                      <ListItemText primary="No pending invites." />
                    </ListItem>
                  ) : (
                    invites.map((invite) => (
                      <ListItem
                        key={invite.id || invite.inviteId || invite.email}
                        divider
                        secondaryAction={
                          <Button
                            size="small"
                            color="error"
                            onClick={() =>
                              void revokeInvite(invite.id || invite.inviteId)
                            }
                          >
                            Revoke
                          </Button>
                        }
                      >
                        <ListItemText
                          primary={invite.email || "Pending invite"}
                          secondary={[
                            invite.role || "member",
                            invite.status || "pending",
                            invite.expiresAt,
                          ]
                            .filter(Boolean)
                            .join(" · ")}
                        />
                      </ListItem>
                    ))
                  )}
                </List>
              </CardContent>
            </Card>
          </Grid>
          <Grid item xs={12} md={6}>
            <Card variant="outlined">
              <CardContent>
                <Typography variant="subtitle2">
                  Plan and access activity
                </Typography>
                <Stack direction="row" spacing={1} sx={{ mt: 1 }}>
                  <Chip
                    size="small"
                    variant="outlined"
                    label={`Plan: ${plan?.planId || plan?.status || "unknown"}`}
                  />
                  <Chip
                    size="small"
                    variant="outlined"
                    label={`${activity.length} recent events`}
                  />
                </Stack>
                <List dense disablePadding sx={{ mt: 0.5 }}>
                  {activity.slice(0, 8).map((event, index) => (
                    <ListItem key={event.id || event.eventId || index} divider>
                      <ListItemText
                        primary={
                          event.kind ||
                          event.type ||
                          event.action ||
                          "Access event"
                        }
                        secondary={
                          event.at ||
                          event.createdAt ||
                          event.actorEmail ||
                          "Remote governance activity"
                        }
                      />
                    </ListItem>
                  ))}
                </List>
              </CardContent>
            </Card>
          </Grid>
          <Grid item xs={12} md={6}>
            <Alert severity="info">
              Governance mutations remain server-authorized. Squad only reveals
              a manually requested invite token once in page memory; it does not
              persist account credentials or invite tokens.
            </Alert>
          </Grid>
        </Grid>
      ) : null}
    </Stack>
  );
}

function BoxHeading() {
  return (
    <div>
      <Typography variant="h6">Organization governance</Typography>
      <Typography variant="body2" color="text.secondary">
        Tenant members, pending invites, access activity, and plan state from
        the remote accounts service.
      </Typography>
    </div>
  );
}

GovernancePanel.propTypes = { client: PropTypes.object.isRequired };
