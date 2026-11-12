package mcp

import (
	"context"
	"encoding/json"
	"errors"
	"net/url"
	"time"

	"github.com/futureagi/agentcc-gateway/internal/models"
	"github.com/oklog/ulid/v2"
)

// ApprovalStore is shared authority. CompareSwap must never create or refresh
// an expired record. No memory fallback is permitted for authorization.
type ApprovalStore interface {
	Create(context.Context, string, string, []byte, time.Duration) error
	Load(context.Context, string) ([]byte, bool, error)
	CompareSwap(context.Context, string, []byte, []byte, time.Time) (bool, error)
	List(context.Context, string) ([][]byte, error)
	// ListPage must allow traversal past nonmatching rows without offset skips.
	ListPage(context.Context, string, string, int) ([][]byte, string, error)
}

// ApprovalRecord is exactly mcp-approval-v1; private binding material stays in
// the stored wrapper. Neither shape contains arguments or upstream credentials.
type ApprovalRecord struct {
	ID              string            `json:"approval_id"`
	OrgID           string            `json:"org_id"`
	VirtualServerID string            `json:"virtual_server_id"`
	Tool            string            `json:"tool"`
	ArgsHash        string            `json:"args_hash"`
	AgentRunID      string            `json:"agent_run_id,omitempty"`
	Principal       ApprovalPrincipal `json:"principal"`
	Status          string            `json:"status"`
	RequestedAt     time.Time         `json:"requested_at"`
	ExpiresAt       time.Time         `json:"expires_at"`
	DecidedBy       string            `json:"decided_by,omitempty"`
	DecidedAt       *time.Time        `json:"decided_at,omitempty"`
	Note            string            `json:"note,omitempty"`
}
type approvalState struct {
	Record   ApprovalRecord `json:"record"`
	Binding  string         `json:"binding"`
	Owner    string         `json:"owner"`
	Validity string         `json:"validity"`
}
type approvalID struct {
	Org   string `json:"org"`
	Nonce string `json:"nonce"`
}

type ApprovalManager struct {
	store ApprovalStore
	codec *StateCodec
	now   func() time.Time
}

func NewApprovalManager(store ApprovalStore, codec *StateCodec, now func() time.Time) *ApprovalManager {
	if now == nil {
		now = time.Now
	}
	return &ApprovalManager{store: store, codec: codec, now: now}
}

var (
	ErrApprovalNotFound    = errors.New("approval not found")
	ErrApprovalUnavailable = errors.New("approval state unavailable")
	ErrApprovalConflict    = errors.New("approval is no longer pending")
	ErrApprovalInvalid     = errors.New("invalid approval request")
)

// Keep terminal rows briefly for polling/auditing. This retention never extends
// the grant's logical ExpiresAt; the sealed ID and Redis record share the TTL.
const approvalRetention = time.Hour

func approvalIndex(org string) string { return "mapr:{" + org + "}:index" }
func (m *ApprovalManager) key(org, id string) (string, error) {
	if m == nil || m.codec == nil || len(id) > 4096 {
		return "", ErrApprovalNotFound
	}
	raw, err := m.codec.Open(StatePurposeApproval, id)
	var p approvalID
	if err != nil || json.Unmarshal(raw, &p) != nil || p.Org != org || p.Org == "" || p.Nonce == "" {
		return "", ErrApprovalNotFound
	}
	return "mapr:{" + p.Org + "}:" + p.Nonce, nil
}
func (m *ApprovalManager) load(ctx context.Context, org, id string) (approvalState, []byte, string, error) {
	var state approvalState
	key, err := m.key(org, id)
	if err != nil {
		return state, nil, "", err
	}
	if m.store == nil {
		return state, nil, key, ErrApprovalUnavailable
	}
	raw, ok, err := m.store.Load(ctx, key)
	if err != nil {
		return state, nil, key, ErrApprovalUnavailable
	}
	if !ok {
		return state, nil, key, ErrApprovalNotFound
	}
	if json.Unmarshal(raw, &state) != nil || state.Record.OrgID != org || state.Record.ID != id {
		return state, nil, key, ErrApprovalUnavailable
	}
	return state, raw, key, nil
}
func (m *ApprovalManager) public(state approvalState) ApprovalRecord {
	rec := state.Record
	if !m.now().Before(rec.ExpiresAt) {
		rec.Status = "expired"
	}
	return rec
}
func approvalRefusal(code, message string) *Denial {
	return &Denial{Type: models.ErrTypePermission, Code: code, Message: message}
}
func approvalMismatch(id, message string) *Denial {
	if id == "" || len(id) > 4096 {
		id = "invalid"
	}
	d := approvalRefusal("approval_mismatch", message)
	d.Metadata = map[string]any{"approval_id": id}
	return d
}
func approvalUnavailable() *Denial {
	return &Denial{Type: models.ErrTypeServer, Code: CodePolicyUnavailable, Message: "Approval state is unavailable; the call was not made. Retry when the approval service is available."}
}

// Check runs after ordinary authorization and pinning on every retry. A once
// approval is atomically consumed before dispatch; a later refusal or crash
// does not restore it (at most once, not a promise of exactly-once delivery).
func (m *ApprovalManager) Check(cc *CallContext) *Denial {
	policy, required, err := approvalPolicy(cc)
	if err != nil {
		return approvalUnavailable()
	}
	retry, _ := cc.Context().Value(approvalRetryKey{}).(approvalRetry)
	if retry.Invalid {
		return approvalMismatch("invalid", "The approval ID is malformed; request a new approval for this call.")
	}
	if !required && retry.ID == "" {
		return nil
	}
	if !validApprovalPrincipal(cc.Principal) || cc.VirtualServer == "" {
		return approvalUnavailable()
	}
	args, err := CanonicalApprovalArgs(cc.Args)
	if err != nil {
		return approvalMismatch(retry.ID, "Approval arguments are not a valid object.")
	}
	cc.ArgsHash = args
	if retry.ID != "" {
		return m.claim(cc, retry.ID, approvalBinding(cc, args), policy.Validity)
	}
	if m == nil || m.store == nil || m.codec == nil {
		return approvalUnavailable()
	}
	ttl := time.Duration(policy.TTLSeconds) * time.Second
	now := m.now().UTC()
	p := approvalID{Org: cc.Principal.OrgID, Nonce: ulid.Make().String()}
	payload, _ := json.Marshal(p)
	id, err := m.codec.Seal(StatePurposeApproval, payload, ttl+approvalRetention)
	if err != nil {
		return approvalUnavailable()
	}
	rec := ApprovalRecord{ID: id, OrgID: p.Org, VirtualServerID: cc.VirtualServer, Tool: cc.Upstream.ServerID + "/" + cc.Tool.UpstreamName, ArgsHash: args, AgentRunID: cc.Agent.RunID, Principal: approvalPrincipal(cc.Principal), Status: "pending", RequestedAt: now, ExpiresAt: now.Add(ttl)}
	state := approvalState{Record: rec, Binding: approvalBinding(cc, args), Owner: approvalOwner(cc.Principal), Validity: policy.Validity}
	raw, _ := json.Marshal(state)
	if err = m.store.Create(cc.Context(), "mapr:{"+p.Org+"}:"+p.Nonce, approvalIndex(p.Org), raw, ttl+approvalRetention); err != nil {
		return approvalUnavailable()
	}
	return approvalRequired(cc, rec)
}
func approvalRequired(cc *CallContext, rec ApprovalRecord) *Denial {
	endpoint := "/mcp"
	if cc.Request() != nil {
		endpoint = cc.Request().URL.Path
	}
	d := approvalRefusal("approval_required", "Approval is required. Ask an administrator to decide, poll the approval, then retry the identical call with io.futureagi/approval_id.")
	d.Metadata = map[string]any{"approval_id": rec.ID, "status": rec.Status, "approve_url": "/dashboard/gateway/mcp?tab=approvals", "poll_url": endpoint + "/approvals/" + url.PathEscape(rec.ID), "expires_at": rec.ExpiresAt}
	return d
}
func (m *ApprovalManager) claim(cc *CallContext, id, binding, validity string) *Denial {
	for attempt := 0; attempt < 8; attempt++ {
		state, raw, key, err := m.load(cc.Context(), cc.Principal.OrgID, id)
		if errors.Is(err, ErrApprovalUnavailable) {
			return approvalUnavailable()
		}
		if err != nil || state.Binding != binding || (validity != "" && validity != state.Validity) {
			return approvalMismatch(id, "The approval does not match this call; request a new approval.")
		}
		rec := m.public(state)
		switch rec.Status {
		case "pending":
			return approvalRequired(cc, rec)
		case "denied", "expired", "used":
			return approvalMismatch(id, "The approval is "+rec.Status+"; this call was not made. Request a new approval if appropriate.")
		case "approved":
			if state.Validity == "ttl" {
				return nil
			}
			if state.Validity != "once" {
				return approvalUnavailable()
			}
			state.Record.Status = "used"
			next, _ := json.Marshal(state)
			ok, err := m.store.CompareSwap(cc.Context(), key, raw, next, state.Record.ExpiresAt)
			if err != nil {
				return approvalUnavailable()
			}
			if ok {
				return nil
			}
		default:
			return approvalUnavailable()
		}
	}
	return approvalUnavailable()
}
