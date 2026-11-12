import PropTypes from "prop-types";
import {
  Alert,
  Box,
  Stack,
  Table,
  TableBody,
  TableCell,
  TableContainer,
  TableHead,
  TableRow,
  Tooltip,
  Typography,
} from "@mui/material";
import GatewayEmptyState from "../../components/GatewayEmptyState";
import { GATEWAY_MONO } from "../../utils/fonts";
import {
  amountNote,
  amountView,
  policyRevision,
  readPolicy,
} from "./admissionPolicy";
import { UNBOUND_KEY_MESSAGE } from "./capsWording";

// The exact status and error codes under a plain sentence, for a developer
// who matches them; the sentence above says what happens in words.
function CodeLine({ children }) {
  return (
    <Typography
      variant="caption"
      component="p"
      color="text.secondary"
      data-testid="admission-code"
      sx={{ fontFamily: GATEWAY_MONO, mt: 0.5 }}
    >
      {children}
    </Typography>
  );
}
CodeLine.propTypes = { children: PropTypes.node };

// The configured amount next to the threshold the gateway enforces: it floors
// the limit to whole micro-dollars, so these can differ.
function EnforcedCell({ value, testId }) {
  const view = amountView(value);
  const note = amountNote(value);
  return (
    <TableCell data-testid={testId}>
      {view.micros === null
        ? "Invalid stored amount"
        : `${view.enforced} (${view.micros.toLocaleString("en-US")} micro-dollars)`}
      {note && (
        <Typography variant="caption" display="block" color="warning.main">
          {note}
        </Typography>
      )}
    </TableCell>
  );
}
EnforcedCell.propTypes = {
  value: PropTypes.any,
  testId: PropTypes.string.isRequired,
};

export default function AdmissionPolicySummary({ config }) {
  const view = readPolicy(config);
  const caps = [
    ...view.agentCaps.map((cap) => ({ ...cap, scope: "Agent", kind: "agent" })),
    ...view.keyCaps.map((cap) => ({ ...cap, scope: "Key ID", kind: "key" })),
  ];
  const capByAgent = new Map(view.agentCaps.map((cap) => [cap.id, cap]));
  return (
    <Stack spacing={1.5} data-testid="admission-policy-current">
      <Typography variant="subtitle2">
        <span>
          {view.enabled
            ? "Daily caps active in saved policy"
            : "Daily caps inactive"}
        </span>{" "}
        · Revision {policyRevision(config)}
      </Typography>
      {caps.length > 0 || view.runCeiling !== null ? (
        <TableContainer>
          <Table size="small" aria-label="Current admission policy">
            <TableHead>
              <TableRow>
                {[
                  "Scope",
                  "Identity",
                  "Amount (USD)",
                  "Enforced threshold",
                  "Period",
                  "First activated at (UTC)",
                ].map((label) => (
                  <TableCell key={label}>{label}</TableCell>
                ))}
              </TableRow>
            </TableHead>
            <TableBody>
              {caps.map((row) => (
                <TableRow key={`${row.scope}:${row.id}`}>
                  <TableCell>{row.scope}</TableCell>
                  <TableCell sx={{ overflowWrap: "anywhere" }}>
                    {row.id}
                  </TableCell>
                  <TableCell>{amountView(row.limit).configured}</TableCell>
                  <EnforcedCell
                    value={row.limit}
                    testId={`admission-enforced-${row.kind}-${row.id}`}
                  />
                  <TableCell>{row.period || "daily"} (UTC)</TableCell>
                  <TableCell>
                    {row.activated_at || "Not recorded in current policy"}
                  </TableCell>
                </TableRow>
              ))}
              {view.runCeiling !== null && (
                <TableRow>
                  <TableCell>Run ceiling</TableCell>
                  <TableCell>Organization base ceiling</TableCell>
                  <TableCell>
                    {amountView(view.runCeiling).configured}
                  </TableCell>
                  <EnforcedCell
                    value={view.runCeiling}
                    testId="admission-enforced-run"
                  />
                  <TableCell>Per run, not daily</TableCell>
                  <TableCell>Not recorded by the server</TableCell>
                </TableRow>
              )}
            </TableBody>
          </Table>
        </TableContainer>
      ) : (
        <GatewayEmptyState
          title="No admission caps configured"
          description="An Owner or Admin can create an activation draft to set daily agent or key caps, or a per-run ceiling. The budgets switch alone does not turn admission caps on."
          sx={{ py: { xs: 3, sm: 4 }, flex: "0 0 auto" }}
        />
      )}
      {view.enabled && view.agentCaps.length > 0 && (
        <Typography
          variant="body2"
          color="text.secondary"
          data-testid="admission-unbound-key-note"
        >
          {UNBOUND_KEY_MESSAGE}
        </Typography>
      )}
      {view.enabled &&
        view.agentCaps.length > 0 &&
        view.bindings.length === 0 && (
          <Alert severity="error" data-testid="admission-bindings-missing">
            Agent caps are on, but no API key is linked to an agent, so the
            gateway refuses requests from every execution key. Edit the caps,
            link keys to agents and activate again.
            <CodeLine>402 call_cap_unenforceable</CodeLine>
          </Alert>
        )}
      {view.bindings.length > 0 && (
        <TableContainer data-testid="admission-bindings-current">
          <Table size="small" aria-label="Current key bindings">
            <TableHead>
              <TableRow>
                {["Key ID", "Bound to agent", "Agent cap"].map((label) => (
                  <TableCell key={label}>{label}</TableCell>
                ))}
              </TableRow>
            </TableHead>
            <TableBody>
              {view.bindings.map(({ key, agent }) => (
                <TableRow key={key}>
                  <TableCell sx={{ overflowWrap: "anywhere" }}>{key}</TableCell>
                  <TableCell sx={{ overflowWrap: "anywhere" }}>
                    {agent}
                  </TableCell>
                  <TableCell>
                    {capByAgent.has(agent)
                      ? amountView(capByAgent.get(agent).limit).configured
                      : "No agent cap"}
                  </TableCell>
                </TableRow>
              ))}
            </TableBody>
          </Table>
        </TableContainer>
      )}
      <Typography variant="body2" color="text.secondary">
        Activated by: Not recorded by the server. First-activation timestamps
        are retained across edits; they are not the latest activation time.
      </Typography>
      {/* The config read returns only the author's user id, so the copy
          names a member; the id shows on hover and on keyboard focus. */}
      {config?.created_by && (
        <Typography variant="body2" color="text.secondary">
          Last config change: saved by{" "}
          <Tooltip title={`User ID ${String(config.created_by)}`}>
            <Box
              component="span"
              tabIndex={0}
              data-testid="admission-config-author"
              sx={{
                textDecoration: "underline dotted",
                cursor: "help",
                borderRadius: 0.5,
                "&:focus-visible": {
                  outline: "2px solid",
                  outlineColor: "primary.main",
                },
              }}
            >
              a member of this organization
            </Box>
          </Tooltip>
          . This is who saved the config, not necessarily who turned the caps
          on.
        </Typography>
      )}
      <Alert severity="info">
        Daily agent and key caps count synchronous model spend, including BYOK,
        from when they were first turned on. Days start at 00:00 UTC, and
        earlier spend is not imported. Once a cap is used up, requests in its
        scope are refused until the next day. Agent caps use the key-to-agent
        links saved on the server, not an agent name the caller sends, and while
        any agent cap exists, a key that is not linked to an agent is refused.
        Key IDs are identifiers, never API key secrets.
        <CodeLine>
          429 when a daily cap is used up · 402 call_cap_unenforceable for a key
          with no agent link
        </CodeLine>
      </Alert>
      <Typography variant="body2" color="text.secondary">
        Daily run cap: not enforced yet. The per-run ceiling is a different
        control: once a run reaches it, the run&apos;s further requests are
        refused, and it does not reset each day. Existing runs keep their stored
        limit; lowering a ceiling can limit them further. Removing or raising a
        ceiling does not refund spend or release existing run limits. Other
        limits, including routing rollout ceilings, still apply.
      </Typography>
      <CodeLine>
        402 agent_budget_exhausted when a run reaches its ceiling (no daily
        reset)
      </CodeLine>
    </Stack>
  );
}
AdmissionPolicySummary.propTypes = { config: PropTypes.object };
