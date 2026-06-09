package ipacl

import (
	"context"
	"net"
	"testing"

	"github.com/futureagi/agentcc-gateway/internal/config"
	"github.com/futureagi/agentcc-gateway/internal/models"
	"github.com/futureagi/agentcc-gateway/internal/pipeline"
)

func newRC(clientIP string) *models.RequestContext {
	rc := models.AcquireRequestContext()
	rc.Metadata["client_ip"] = clientIP
	return rc
}

func TestPlugin_Name(t *testing.T) {
	p := New(config.IPACLConfig{}, nil)
	if p.Name() != "ipacl" {
		t.Fatalf("expected name ipacl, got %s", p.Name())
	}
}

func TestPlugin_Priority(t *testing.T) {
	p := New(config.IPACLConfig{}, nil)
	if p.Priority() != 10 {
		t.Fatalf("expected priority 10, got %d", p.Priority())
	}
}

func TestPlugin_Disabled(t *testing.T) {
	p := New(config.IPACLConfig{Enabled: false, Deny: []string{"1.2.3.4"}}, nil)
	rc := newRC("1.2.3.4")
	defer rc.Release()

	result := p.ProcessRequest(context.Background(), rc)
	if result.Action != pipeline.Continue {
		t.Fatalf("disabled plugin should continue, got action %d", result.Action)
	}
}

func TestPlugin_DenyList_BlocksIP(t *testing.T) {
	p := New(config.IPACLConfig{
		Enabled: true,
		Deny:    []string{"10.0.0.1"},
	}, nil)
	rc := newRC("10.0.0.1")
	defer rc.Release()

	result := p.ProcessRequest(context.Background(), rc)
	if result.Error == nil {
		t.Fatal("expected error for denied IP")
	}
	if result.Error.Status != 403 {
		t.Fatalf("expected 403, got %d", result.Error.Status)
	}
}

func TestPlugin_DenyList_CIDR(t *testing.T) {
	p := New(config.IPACLConfig{
		Enabled: true,
		Deny:    []string{"192.168.1.0/24"},
	}, nil)

	// IP in the denied range.
	rc := newRC("192.168.1.42")
	defer rc.Release()
	result := p.ProcessRequest(context.Background(), rc)
	if result.Error == nil {
		t.Fatal("expected error for IP in denied CIDR")
	}

	// IP outside the denied range.
	rc2 := newRC("192.168.2.1")
	defer rc2.Release()
	result2 := p.ProcessRequest(context.Background(), rc2)
	if result2.Action != pipeline.Continue {
		t.Fatal("expected continue for IP outside denied CIDR")
	}
}

func TestPlugin_AllowList_OnlyAllowsListed(t *testing.T) {
	p := New(config.IPACLConfig{
		Enabled: true,
		Allow:   []string{"10.0.0.0/8"},
	}, nil)

	// Allowed IP.
	rc := newRC("10.1.2.3")
	defer rc.Release()
	result := p.ProcessRequest(context.Background(), rc)
	if result.Action != pipeline.Continue {
		t.Fatal("expected continue for allowed IP")
	}

	// Disallowed IP.
	rc2 := newRC("192.168.1.1")
	defer rc2.Release()
	result2 := p.ProcessRequest(context.Background(), rc2)
	if result2.Error == nil {
		t.Fatal("expected error for IP not in allow list")
	}
}

func TestPlugin_DenyTakesPrecedence(t *testing.T) {
	p := New(config.IPACLConfig{
		Enabled: true,
		Allow:   []string{"10.0.0.0/8"},
		Deny:    []string{"10.0.0.99"},
	}, nil)

	// Allowed IP.
	rc := newRC("10.0.0.1")
	defer rc.Release()
	result := p.ProcessRequest(context.Background(), rc)
	if result.Action != pipeline.Continue {
		t.Fatal("expected continue for allowed IP")
	}

	// Denied IP (even though in allow range).
	rc2 := newRC("10.0.0.99")
	defer rc2.Release()
	result2 := p.ProcessRequest(context.Background(), rc2)
	if result2.Error == nil {
		t.Fatal("expected error for denied IP even when in allow range")
	}
}

func TestPlugin_EmptyAllowList_AllowsAll(t *testing.T) {
	p := New(config.IPACLConfig{
		Enabled: true,
		// No allow or deny lists — allow everything.
	}, nil)

	rc := newRC("8.8.8.8")
	defer rc.Release()
	result := p.ProcessRequest(context.Background(), rc)
	if result.Action != pipeline.Continue {
		t.Fatal("empty allow list should allow all IPs")
	}
}

func TestPlugin_NoClientIP_Denied(t *testing.T) {
	p := New(config.IPACLConfig{Enabled: true}, nil)
	rc := newRC("")
	defer rc.Release()

	result := p.ProcessRequest(context.Background(), rc)
	if result.Error == nil {
		t.Fatal("expected error when client IP is empty")
	}
}

func TestPlugin_InvalidClientIP_Denied(t *testing.T) {
	p := New(config.IPACLConfig{Enabled: true}, nil)
	rc := newRC("not-an-ip")
	defer rc.Release()

	result := p.ProcessRequest(context.Background(), rc)
	if result.Error == nil {
		t.Fatal("expected error for invalid client IP")
	}
}

func TestPlugin_IPv6(t *testing.T) {
	p := New(config.IPACLConfig{
		Enabled: true,
		Allow:   []string{"::1/128", "fd00::/8"},
	}, nil)

	// Allowed IPv6.
	rc := newRC("::1")
	defer rc.Release()
	result := p.ProcessRequest(context.Background(), rc)
	if result.Action != pipeline.Continue {
		t.Fatal("expected continue for allowed IPv6")
	}

	// Allowed IPv6 range.
	rc2 := newRC("fd00::1234")
	defer rc2.Release()
	result2 := p.ProcessRequest(context.Background(), rc2)
	if result2.Action != pipeline.Continue {
		t.Fatal("expected continue for IPv6 in allowed range")
	}

	// Disallowed IPv6.
	rc3 := newRC("2001:db8::1")
	defer rc3.Release()
	result3 := p.ProcessRequest(context.Background(), rc3)
	if result3.Error == nil {
		t.Fatal("expected error for IPv6 not in allow list")
	}
}

func TestPlugin_ProcessResponse_NoOp(t *testing.T) {
	p := New(config.IPACLConfig{Enabled: true, Deny: []string{"1.2.3.4"}}, nil)
	rc := newRC("1.2.3.4")
	defer rc.Release()

	result := p.ProcessResponse(context.Background(), rc)
	if result.Action != pipeline.Continue {
		t.Fatal("ProcessResponse should always continue")
	}
}

func TestPlugin_InvalidEntries_Skipped(t *testing.T) {
	p := New(config.IPACLConfig{
		Enabled: true,
		Allow:   []string{"not-a-cidr", "10.0.0.0/8"},
		Deny:    []string{"also-bad"},
	}, nil)

	// Only the valid allow entry should be parsed.
	if p.AllowCount() != 1 {
		t.Fatalf("expected 1 allow rule, got %d", p.AllowCount())
	}
	if p.DenyCount() != 0 {
		t.Fatalf("expected 0 deny rules, got %d", p.DenyCount())
	}
}

func TestPlugin_Counts(t *testing.T) {
	p := New(config.IPACLConfig{
		Enabled: true,
		Allow:   []string{"10.0.0.0/8", "172.16.0.0/12"},
		Deny:    []string{"10.0.0.99", "10.0.0.100"},
	}, nil)

	if p.AllowCount() != 2 {
		t.Fatalf("expected 2 allow rules, got %d", p.AllowCount())
	}
	if p.DenyCount() != 2 {
		t.Fatalf("expected 2 deny rules, got %d", p.DenyCount())
	}
}

// --- CheckIP tests (used by auth plugin for per-key IP check) ---

func TestCheckIP_Allowed(t *testing.T) {
	ip := net.ParseIP("10.1.2.3")
	if !CheckIP(ip, []string{"10.0.0.0/8"}) {
		t.Fatal("expected IP to be allowed")
	}
}

func TestCheckIP_NotAllowed(t *testing.T) {
	ip := net.ParseIP("192.168.1.1")
	if CheckIP(ip, []string{"10.0.0.0/8"}) {
		t.Fatal("expected IP to not be allowed")
	}
}

func TestCheckIP_ExactMatch(t *testing.T) {
	ip := net.ParseIP("1.2.3.4")
	if !CheckIP(ip, []string{"1.2.3.4"}) {
		t.Fatal("expected exact IP match")
	}
}

func TestCheckIP_EmptyList(t *testing.T) {
	ip := net.ParseIP("1.2.3.4")
	if CheckIP(ip, nil) {
		t.Fatal("empty list should not match")
	}
}

// --- parseCIDROrIP tests ---

func TestParseCIDROrIP_CIDR(t *testing.T) {
	n := parseCIDROrIP("10.0.0.0/8")
	if n == nil {
		t.Fatal("expected non-nil for valid CIDR")
	}
	if !n.Contains(net.ParseIP("10.1.2.3")) {
		t.Fatal("expected CIDR to contain 10.1.2.3")
	}
}

func TestParseCIDROrIP_BareIP(t *testing.T) {
	n := parseCIDROrIP("1.2.3.4")
	if n == nil {
		t.Fatal("expected non-nil for bare IP")
	}
	if !n.Contains(net.ParseIP("1.2.3.4")) {
		t.Fatal("expected /32 to contain the IP itself")
	}
	if n.Contains(net.ParseIP("1.2.3.5")) {
		t.Fatal("expected /32 to not contain other IPs")
	}
}

func TestParseCIDROrIP_IPv6(t *testing.T) {
	n := parseCIDROrIP("::1")
	if n == nil {
		t.Fatal("expected non-nil for bare IPv6")
	}
	ones, _ := n.Mask.Size()
	if ones != 128 {
		t.Fatalf("expected /128 mask for bare IPv6, got /%d", ones)
	}
}

func TestParseCIDROrIP_Invalid(t *testing.T) {
	if parseCIDROrIP("garbage") != nil {
		t.Fatal("expected nil for invalid input")
	}
	if parseCIDROrIP("") != nil {
		t.Fatal("expected nil for empty input")
	}
}
