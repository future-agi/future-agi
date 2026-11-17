package mcp

import (
	"bytes"
	"context"
	"crypto/sha256"
	"encoding/hex"
	"encoding/json"
	"errors"
	"io"
	"strings"
	"time"

	"github.com/futureagi/agentcc-gateway/internal/mcp/vserver"
	"github.com/futureagi/agentcc-gateway/internal/tenant"
)

const approvalMetaKey = "io.futureagi/approval_id"

type approvalRetryKey struct{}
type approvalRetry struct {
	ID      string
	Invalid bool
}

// Capture before stripInbound: the token stays in gateway-only context, never
// in the forwarded message, headers, evidence or the parked-request path.
func captureApprovalRetry(cc *CallContext) {
	if cc.Message() == nil {
		return
	}
	raw, present := paramsMeta(cc.Message().Params)[approvalMetaKey]
	if !present {
		return
	}
	var retry approvalRetry
	retry.Invalid = json.Unmarshal(raw, &retry.ID) != nil || retry.ID == "" || len(retry.ID) > 4096
	cc.ctx = context.WithValue(cc.Context(), approvalRetryKey{}, retry)
}

// CanonicalApprovalArgs sorts nested object keys without ever rounding JSON
// numbers through float64. Numeric spellings remain distinct (a safe refusal,
// never an authorization of a different integer).
func CanonicalApprovalArgs(raw json.RawMessage) (string, error) {
	if len(bytes.TrimSpace(raw)) == 0 {
		raw = json.RawMessage(`{}`)
	}
	dec := json.NewDecoder(bytes.NewReader(raw))
	dec.UseNumber()
	var obj map[string]any
	if err := dec.Decode(&obj); err != nil || obj == nil {
		return "", errors.New("approval arguments must be an object")
	}
	var extra any
	if err := dec.Decode(&extra); err != io.EOF {
		return "", errors.New("trailing approval arguments")
	}
	canonical, err := json.Marshal(obj)
	if err != nil {
		return "", err
	}
	return approvalHash(canonical), nil
}
func approvalHash(b []byte) string { h := sha256.Sum256(b); return hex.EncodeToString(h[:]) }

// ApprovalPrincipal is the public contract's identity, not a credential.
type ApprovalPrincipal struct {
	Kind     PrincipalKind `json:"kind"`
	KeyID    string        `json:"key_id,omitempty"`
	UserID   string        `json:"user_id,omitempty"`
	ClientID string        `json:"client_id,omitempty"`
}

func approvalPrincipal(p Principal) ApprovalPrincipal {
	return ApprovalPrincipal{p.Kind, p.KeyID, p.UserID, p.ClientID}
}
func validApprovalPrincipal(p Principal) bool {
	if p.OrgID == "" || len(p.OrgID) > 256 || strings.ContainsAny(p.OrgID, "{}") {
		return false
	}
	switch p.Kind {
	case PrincipalKey:
		return p.KeyID != ""
	case PrincipalOAuth:
		return p.UserID != "" && p.ClientID != ""
	case PrincipalRuntime, PrincipalCallback:
		return p.TokenID != ""
	case PrincipalAdmin:
		return p.UserID != "" || p.KeyID != ""
	default:
		return false
	}
}
func approvalOwner(p Principal) string {
	// Bind runtime/callback token identity as well as its public subject.
	token := ""
	if p.Kind == PrincipalRuntime || p.Kind == PrincipalCallback {
		token = p.TokenID
	}
	raw, _ := json.Marshal(struct {
		Org       string
		Principal ApprovalPrincipal
		Token     string
	}{p.OrgID, approvalPrincipal(p), token})
	return approvalHash(raw)
}
func approvalBinding(cc *CallContext, argsHash string) string {
	run := "run:" + cc.Agent.RunID
	if cc.Agent.RunID == "" {
		run = "principal:" + approvalOwner(cc.Principal)
	}
	raw, _ := json.Marshal(struct{ Org, VS, Server, Tool, Account, Revision, Args, Run, Owner string }{
		cc.Principal.OrgID, cc.VirtualServer, cc.Upstream.ServerID, cc.Tool.UpstreamName, cc.Credential.Ref, cc.Tool.Revision, argsHash, run, approvalOwner(cc.Principal),
	})
	return approvalHash(raw)
}

func approvalPolicy(cc *CallContext) (tenant.MCPApprovalSettings, bool, error) {
	var policy tenant.MCPApprovalSettings
	if v := callViewOf(cc); v != nil {
		policy = v.vs.Approval
	} else if vs, ok := cc.OrgConfig.MCPVirtualServer(cc.VirtualServer); ok {
		policy = vs.Approval
	}
	if policy.Require == "" || policy.Require == "none" {
		return policy, false, nil
	}
	if policy.Validity != "once" && policy.Validity != "ttl" {
		return policy, false, errors.New("unsupported approval validity")
	}
	if policy.TTLSeconds <= 0 || int64(policy.TTLSeconds) > int64((time.Duration(1<<63-1)-approvalRetention)/time.Second) {
		return policy, false, errors.New("invalid approval TTL")
	}
	switch policy.Require {
	case "listed":
		names := []string{cc.Tool.ExposedName, cc.Upstream.ServerID + "/" + cc.Tool.UpstreamName, cc.Upstream.ServerID + "__" + cc.Tool.UpstreamName}
		if view := callViewOf(cc); view != nil && view.tools != nil {
			if entry, ok := view.tools.Lookup(cc.Tool.ExposedName); ok {
				names = view.names(entry).all
			}
		}
		return policy, vserver.Any(policy.Tools, names...), nil
	case "destructive":
		a := cc.Tool.Annotations
		if a != nil && a.DestructiveHint != nil && *a.DestructiveHint {
			return policy, true, nil
		}
		srv, _ := cc.OrgConfig.MCPServer(cc.Upstream.ServerID)
		trusted := srv.TrustedAnnotations
		if pool := TenantPoolOf(cc); pool != nil {
			resolved, ok := pool.Server(cc.Upstream.ServerID)
			if !ok {
				return policy, false, errors.New("approval upstream unavailable")
			}
			trusted = resolved.TrustedAnnotations
		}
		if a != nil && a.ReadOnlyHint != nil && *a.ReadOnlyHint {
			return policy, false, nil
		}
		if !trusted {
			return policy, true, nil
		}
		// The MCP default is destructive unless explicitly declared otherwise.
		return policy, a == nil || a.DestructiveHint == nil || *a.DestructiveHint, nil
	default:
		return policy, false, errors.New("unsupported approval policy")
	}
}
