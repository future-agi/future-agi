package tenancy

import (
	"net/http"
	"sync"
	"testing"

	"github.com/futureagi/agentcc-gateway/internal/mcp"
)

// requestSpy records every request tenancy makes of the admission policy and
// admits them all.
type requestSpy struct {
	mu   sync.Mutex
	reqs []mcp.ClientAdmissionRequest
}

func (s *requestSpy) AdmitClient(request mcp.ClientAdmissionRequest) mcp.ClientAdmission {
	s.mu.Lock()
	defer s.mu.Unlock()
	s.reqs = append(s.reqs, request)
	return mcp.ClientAdmission{}
}

func (s *requestSpy) all() []mcp.ClientAdmissionRequest {
	s.mu.Lock()
	defer s.mu.Unlock()
	return append([]mcp.ClientAdmissionRequest(nil), s.reqs...)
}

// Construction asks for the inventory and names no bound client; every later
// use asks about the binding in the store now, and names the binding the
// client was built for.
func TestAdmissionRequestsCarryCurrentAndBoundBindings(t *testing.T) {
	spy := &requestSpy{}
	r := newRig(t, Options{})
	r.factory.upstream(urlA, "search")
	r.factory.upstream(urlB, "search")
	d := *r.deps.Load()
	d.ClientAdmission = spy
	r.deps.Store(&d)
	r.push("org-a", 1, `{"enabled":true,"servers":{"github":{"url":"`+urlA+`","credential":{"mode":"per_user"}}}}`)
	_, pool := r.resolve("org-a")

	first := spy.all()[0]
	if !first.Inventory || first.Bound != nil || first.OrgID != "org-a" || first.ServerID != "github" || first.URL != urlA || first.CredentialMode != "per_user" {
		t.Fatalf("construction request: %+v", first)
	}
	retained, err := pool.Client("github", "user-one", mcp.AuthOverride{Header: http.Header{"Authorization": {"Bearer synthetic-first"}}})
	if err != nil {
		t.Fatal(err)
	}
	if err := credentialCall(retained); err != nil {
		t.Fatal(err)
	}
	use := spy.all()
	last := use[len(use)-1]
	if last.Inventory || last.Bound == nil || last.Bound.URL != urlA || last.URL != urlA || last.OrgID != "org-a" {
		t.Fatalf("send-time request: %+v", last)
	}

	// A store write that re-points the server is evaluated at once, against the
	// binding the retained client was built for.
	r.push("org-a", 2, `{"enabled":true,"servers":{"github":{"url":"`+urlB+`","credential":{"mode":"per_user"}}}}`)
	use = spy.all()
	last = use[len(use)-1]
	if last.URL != urlB || last.Bound == nil || last.Bound.URL != urlA || last.Inventory {
		t.Fatalf("write-time request: %+v", last)
	}
}
