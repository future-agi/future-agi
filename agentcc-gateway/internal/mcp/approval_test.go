package mcp

import (
	"context"
	"encoding/json"
	"errors"
	"os"
	"strings"
	"sync"
	"sync/atomic"
	"testing"
	"time"

	"github.com/futureagi/agentcc-gateway/internal/redisstate"
	"github.com/futureagi/agentcc-gateway/internal/tenant"
	"github.com/oklog/ulid/v2"
)

func w5Manager(t *testing.T, now func() time.Time) (*ApprovalManager, string) {
	t.Helper()
	addr := os.Getenv("TEST_REDIS_ADDR")
	if addr == "" {
		t.Skip("TEST_REDIS_ADDR required for real approval authority")
	}
	client, err := redisstate.NewClient(redisstate.Config{Address: addr, DB: 13})
	if err != nil {
		t.Fatal(err)
	}
	org := "w5-" + ulid.Make().String()
	t.Cleanup(func() {
		keys, err := client.Redis().Keys(context.Background(), "mapr:{"+org+"}:*").Result()
		if err == nil && len(keys) > 0 {
			client.Redis().Del(context.Background(), keys...)
		}
		client.Close()
	})
	codec, err := NewStateCodec([]string{"synthetic-w5-state-material-not-a-real-secret"}, false)
	if err != nil {
		t.Fatal(err)
	}
	if now != nil {
		codec.now = now
	}
	return NewApprovalManager(redisstate.NewMCPApprovalStore(client), codec, now), org
}
func w5Call(org string) *CallContext {
	var cfg tenant.OrgConfig
	_ = json.Unmarshal([]byte(`{"mcp":{"servers":{"mock":{}},"virtual_servers":{"review":{"id":"vs-review","members":["mock"],"approval":{"require":"listed","tools":["mock/*"],"validity":"once","ttl_seconds":30}}}}}`), &cfg)
	return &CallContext{Principal: Principal{Kind: PrincipalKey, KeyID: "key-owner", OrgID: org}, OrgConfig: &cfg, VirtualServer: "vs-review", Upstream: UpstreamRef{ServerID: "mock"}, Tool: ToolRef{ExposedName: "mock__echo", UpstreamName: "echo"}, Args: json.RawMessage(`{"v":1}`)}
}
func w5Pending(t *testing.T, m *ApprovalManager, cc *CallContext) string {
	t.Helper()
	d := m.Check(cc)
	if d == nil || d.Code != "approval_required" {
		t.Fatalf("pending = %v", d)
	}
	meta, ok := d.Metadata.(map[string]any)
	if !ok || len(meta) != 5 || meta["status"] != "pending" {
		t.Fatalf("metadata = %#v", d.Metadata)
	}
	return meta["approval_id"].(string)
}
func w5Retry(cc *CallContext, id string) {
	cc.ctx = context.WithValue(cc.Context(), approvalRetryKey{}, approvalRetry{ID: id})
}
func w5Approve(t *testing.T, m *ApprovalManager, org, id string) {
	t.Helper()
	if _, err := m.Decide(context.Background(), id, ApprovalDecision{OrgID: org, Decision: "approve", Actor: "synthetic-admin"}); err != nil {
		t.Fatal(err)
	}
}

func TestW5ApprovalCanonicalArgs(t *testing.T) {
	a, err := CanonicalApprovalArgs(json.RawMessage(`{"n":9007199254740992,"o":{"z":[1,2],"a":true}}`))
	if err != nil {
		t.Fatal(err)
	}
	b, _ := CanonicalApprovalArgs(json.RawMessage(` { "o": {"a": true, "z":[1,2]}, "n": 9007199254740992 } `))
	c, _ := CanonicalApprovalArgs(json.RawMessage(`{"n":9007199254740993,"o":{"z":[1,2],"a":true}}`))
	if a != b || a == c {
		t.Fatal("canonicalization reordered or rounded arguments")
	}
	for _, raw := range []string{`null`, `[]`, `{} {}`, `{"a":`} {
		if _, err := CanonicalApprovalArgs(json.RawMessage(raw)); err == nil {
			t.Fatalf("accepted %s", raw)
		}
	}
}
func TestW5ApprovalPolicy(t *testing.T) {
	yes, no := true, false
	for _, tc := range []struct {
		name, require, validity string
		annotations             *ToolAnnotations
		trusted, want, invalid  bool
	}{
		{name: "listed glob", require: "listed", want: true},
		{name: "default none", require: "none"},
		{name: "untrusted missing", require: "destructive", want: true},
		{name: "untrusted destructive false", require: "destructive", annotations: &ToolAnnotations{DestructiveHint: &no}, want: true},
		{name: "explicit readonly", require: "destructive", annotations: &ToolAnnotations{ReadOnlyHint: &yes}},
		{name: "destructive wins", require: "destructive", annotations: &ToolAnnotations{ReadOnlyHint: &yes, DestructiveHint: &yes}, want: true},
		{name: "trusted false", require: "destructive", annotations: &ToolAnnotations{DestructiveHint: &no}, trusted: true},
		{name: "trusted missing defaults destructive", require: "destructive", trusted: true, want: true},
		{name: "step closed", require: "listed", validity: "step", invalid: true},
	} {
		t.Run(tc.name, func(t *testing.T) {
			cc := w5Call("org")
			cc.Tool.Annotations = tc.annotations
			p := cc.OrgConfig.MCP.VirtualServers["review"].Approval
			p.Require = tc.require
			if tc.validity != "" {
				p.Validity = tc.validity
			}
			cc.OrgConfig.MCP.Servers["mock"].TrustedAnnotations = &tc.trusted
			_, got, err := approvalPolicy(cc)
			if got != tc.want || (err != nil) != tc.invalid {
				t.Fatalf("required=%v err=%v", got, err)
			}
		})
	}
	cc := w5Call("org")
	cc.OrgConfig.MCP.VirtualServers["review"].Approval.Tools = []string{"other/*"}
	if _, needed, err := approvalPolicy(cc); needed || err != nil {
		t.Fatal("unlisted tool requires approval")
	}
}
func TestW5ApprovalBindings(t *testing.T) {
	m, org := w5Manager(t, nil)
	cc := w5Call(org)
	id := w5Pending(t, m, cc)
	w5Approve(t, m, org, id)
	for _, tc := range []struct {
		name   string
		mutate func(*CallContext)
	}{
		{"org", func(c *CallContext) { c.Principal.OrgID = "other" }},
		{"virtual server", func(c *CallContext) { c.VirtualServer = "vs-other" }},
		{"tool", func(c *CallContext) { c.Tool.UpstreamName = "delete" }},
		{"server", func(c *CallContext) { c.Upstream.ServerID = "other" }},
		{"account", func(c *CallContext) { c.Credential.Ref = "other" }},
		{"run", func(c *CallContext) { c.Agent.RunID = "other" }},
		{"run versus principal fallback", func(c *CallContext) { c.Agent.RunID = "principal:" + approvalOwner(c.Principal) }},
		{"principal", func(c *CallContext) { c.Principal.KeyID = "other" }},
		{"arguments", func(c *CallContext) { c.Args = json.RawMessage(`{"v":2}`) }},
	} {
		t.Run(tc.name, func(t *testing.T) {
			c := w5Call(org)
			tc.mutate(c)
			w5Retry(c, id)
			if d := m.Check(c); d == nil || d.Code != "approval_mismatch" {
				t.Fatalf("mismatch=%v", d)
			}
		})
	}
	w5Retry(cc, id)
	if d := m.Check(cc); d != nil {
		t.Fatal(d)
	}
}
func TestW5ApprovalTTLAndDeny(t *testing.T) {
	for _, mode := range []string{"once", "ttl", "deny", "pending", "expired", "malformed"} {
		t.Run(mode, func(t *testing.T) {
			now := time.Now()
			m, org := w5Manager(t, func() time.Time { return now })
			cc := w5Call(org)
			if mode == "ttl" {
				cc.OrgConfig.MCP.VirtualServers["review"].Approval.Validity = "ttl"
			}
			id := w5Pending(t, m, cc)
			switch mode {
			case "pending", "malformed":
			case "deny":
				if _, err := m.Decide(context.Background(), id, ApprovalDecision{OrgID: org, Decision: "deny", Actor: "admin"}); err != nil {
					t.Fatal(err)
				}
			default:
				w5Approve(t, m, org, id)
			}
			if mode == "expired" {
				now = now.Add(31 * time.Second)
			}
			if mode == "malformed" {
				id = "not-an-approval"
			}
			w5Retry(cc, id)
			d := m.Check(cc)
			if mode == "once" || mode == "ttl" {
				if d != nil {
					t.Fatal(d)
				}
				second := m.Check(cc)
				if (second == nil) != (mode == "ttl") {
					t.Fatalf("second=%v", second)
				}
				if _, err := m.Decide(context.Background(), id, ApprovalDecision{OrgID: org, Decision: "approve", Actor: "admin"}); !errors.Is(err, ErrApprovalConflict) {
					t.Fatalf("reset decision=%v", err)
				}
				now = now.Add(31 * time.Second)
				if m.Check(cc) == nil {
					t.Fatal("expired grant released call")
				}
			} else if d == nil {
				t.Fatal("terminal approval released call")
			}
		})
	}
}
func TestW5ApprovalConcurrentOnce(t *testing.T) {
	m, org := w5Manager(t, nil)
	other := NewApprovalManager(m.store, m.codec, nil)
	cc := w5Call(org)
	id := w5Pending(t, m, cc)
	w5Approve(t, m, org, id)
	var executed atomic.Int32
	var wg sync.WaitGroup
	for i := 0; i < 64; i++ {
		wg.Add(1)
		go func(i int) {
			defer wg.Done()
			c := w5Call(org)
			w5Retry(c, id)
			manager := m
			if i%2 == 0 {
				manager = other
			}
			if manager.Check(c) == nil {
				executed.Add(1)
			}
		}(i)
	}
	wg.Wait()
	if executed.Load() != 1 {
		t.Fatalf("executions=%d", executed.Load())
	}
}
func TestW5ApprovalStoreOutage(t *testing.T) {
	codec, _ := NewStateCodec([]string{"synthetic-w5-state-material-not-a-real-secret"}, false)
	m := NewApprovalManager(redisstate.NewMCPApprovalStore(nil), codec, nil)
	if d := m.Check(w5Call("org")); d == nil || d.Code != CodePolicyUnavailable {
		t.Fatalf("outage=%v", d)
	}
	if d := (noApprovals{}).Check(w5Call("org")); d == nil {
		t.Fatal("missing provider allowed configured policy")
	}
	t.Run("approved retry during Redis transport outage", func(t *testing.T) {
		healthy, org := w5Manager(t, nil)
		cc := w5Call(org)
		id := w5Pending(t, healthy, cc)
		w5Approve(t, healthy, org, id)
		client, err := redisstate.NewClient(redisstate.Config{Address: os.Getenv("TEST_REDIS_ADDR"), DB: 13})
		if err != nil {
			t.Fatal(err)
		}
		client.Close()
		down := NewApprovalManager(redisstate.NewMCPApprovalStore(client), healthy.codec, nil)
		w5Retry(cc, id)
		if d := down.Check(cc); d == nil || d.Code != CodePolicyUnavailable {
			t.Fatalf("outage retry = %v", d)
		}
		if d := healthy.Check(cc); d != nil {
			t.Fatalf("outage consumed the approval: %v", d)
		}
	})
}
func TestW5ApprovalMetadataNeverForwarded(t *testing.T) {
	cc := w5Call("org")
	cc.msg = &Message{Params: json.RawMessage(`{"_meta":{"io.futureagi/approval_id":"synthetic","client":"kept"}}`)}
	if d := parseAgent(nil, cc); d != nil {
		t.Fatal(d)
	}
	retry, _ := cc.Context().Value(approvalRetryKey{}).(approvalRetry)
	if retry.ID != "synthetic" || strings.Contains(string(cc.Message().Params), "approval_id") {
		t.Fatal("retry lost or forwarded")
	}
}
