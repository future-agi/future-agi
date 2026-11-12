package tenancy

import (
	"errors"
	"net/http"
	"sync"
	"testing"

	"github.com/futureagi/agentcc-gateway/internal/mcp"
)

// verdictPolicy is a client-admission policy whose answer can change at any
// time and that counts how often it is asked. It stands for the K2 gate; the
// tenancy rule under test is policy-independent: no recorded "admitted"
// outcome authorises a credential lookup or a send.
type verdictPolicy struct {
	mu        sync.Mutex
	refuseAll bool
	refuseURL string
	asked     int
}

func (p *verdictPolicy) set(refuse bool) {
	p.mu.Lock()
	p.refuseAll = refuse
	p.mu.Unlock()
}

func (p *verdictPolicy) count() int {
	p.mu.Lock()
	defer p.mu.Unlock()
	return p.asked
}

func (p *verdictPolicy) AdmitClient(request mcp.ClientAdmissionRequest) mcp.ClientAdmission {
	p.mu.Lock()
	defer p.mu.Unlock()
	p.asked++
	if p.refuseAll || (p.refuseURL != "" && request.URL == p.refuseURL) {
		return mcp.ClientAdmission{Denial: mcp.CredentialUnavailable()}
	}
	return mcp.ClientAdmission{}
}

func requireAdmissionRefusal(t *testing.T, what string, err error) {
	t.Helper()
	var d *mcp.Denial
	if !errors.As(err, &d) || d.Code != mcp.CodePolicyUnavailable {
		t.Errorf("%s: want the admission refusal, got %v", what, err)
	}
}

// admittedRetained gives a rig whose only server is an admitted per-user
// server with a used, retained client handle.
func admittedRetained(t *testing.T, policy *verdictPolicy) (*rig, *fakeUpstream, mcp.TenantPool, mcp.UpstreamClient, mcp.AuthOverride) {
	t.Helper()
	r := newRig(t, Options{})
	up := r.factory.upstream(urlA, "search")
	r.factory.upstream(urlB, "search")
	d := *r.deps.Load()
	d.ClientAdmission = policy
	r.deps.Store(&d)
	r.push("org-a", 1, `{"enabled":true,"servers":{"github":{"url":"`+urlA+`","credential":{"mode":"per_user"}}}}`)
	_, pool := r.resolve("org-a")
	auth := mcp.AuthOverride{Header: http.Header{"Authorization": {"Bearer synthetic-first"}}}
	retained, err := pool.Client("github", "user-one", auth)
	if err != nil {
		t.Fatalf("admitted control gave no client: %v", err)
	}
	if err := credentialCall(retained); err != nil {
		t.Fatalf("admitted control could not send: %v", err)
	}
	return r, up, pool, retained, auth
}

// Every use asks the policy again; a verdict that changes with no config push
// at all ends the retained client's service at once.
func TestAdmissionIsEvaluatedAtEveryUse(t *testing.T) {
	policy := &verdictPolicy{}
	r, up, pool, retained, auth := admittedRetained(t, policy)
	gate, ok := pool.(mcp.ClientAdmissionPool)
	if !ok {
		t.Fatal("pool lost the credential-lookup gate")
	}

	asked := policy.count()
	if err := credentialCall(retained); err != nil {
		t.Fatal(err)
	}
	if policy.count() <= asked {
		t.Error("a send did not consult the admission policy")
	}
	asked = policy.count()
	if _, err := pool.Client("github", "user-one", auth); err != nil {
		t.Fatal(err)
	}
	if policy.count() <= asked {
		t.Error("a client lookup did not consult the admission policy")
	}
	asked = policy.count()
	if d := gate.ClientAdmission("github"); d != nil {
		t.Fatal(d)
	}
	if policy.count() <= asked {
		t.Error("the credential-lookup gate answered from a recorded verdict")
	}

	builds, sent := r.factory.totalBuilds(), len(up.called())
	policy.set(true)
	requireAdmissionRefusal(t, "retained send", credentialCall(retained))
	_, err := pool.Client("github", "user-one", auth)
	requireAdmissionRefusal(t, "client lookup", err)
	if d := gate.ClientAdmission("github"); d == nil {
		t.Error("the credential-lookup gate admitted a refused server")
	}
	if got := r.factory.totalBuilds(); got != builds {
		t.Errorf("refused server constructed %d more clients", got-builds)
	}
	if got := len(up.called()); got != sent {
		t.Errorf("refused server sent %d more requests", got-sent)
	}
}

// A binding that the policy refuses ends the retained client even when the
// pushed config cannot be loaded because of a sibling: last-known-good must
// never resurrect the refused binding, whatever the credential fields say.
func TestRefusedBindingBehindInvalidSiblingIsNotLastKnownGood(t *testing.T) {
	for _, tc := range []struct{ name, servers string }{
		{"url-changed", `{"github":{"url":"` + urlB + `","credential":{"mode":"per_user"}},"broken":{"url":"not-a-url"}}`},
	} {
		t.Run(tc.name, func(t *testing.T) {
			policy := &verdictPolicy{refuseURL: urlB}
			r, up, pool, retained, auth := admittedRetained(t, policy)
			builds, sent := r.factory.totalBuilds(), len(up.called())

			r.push("org-a", 2, `{"enabled":true,"servers":`+tc.servers+`}`)
			// The store write itself ended it: nothing has resolved the pool yet.
			requireAdmissionRefusal(t, "retained send after write", credentialCall(retained))

			_, pool = r.resolve("org-a") // the sibling error keeps last-known-good
			requireAdmissionRefusal(t, "retained send after resolve", credentialCall(retained))
			_, err := pool.Client("github", "user-one", auth)
			requireAdmissionRefusal(t, "client lookup", err)
			if d := pool.(mcp.ClientAdmissionPool).ClientAdmission("github"); d == nil {
				t.Error("the credential-lookup gate admitted the refused binding through last-known-good")
			}
			if got := r.factory.totalBuilds(); got != builds {
				t.Errorf("refused binding constructed %d more clients", got-builds)
			}
			if got := len(up.called()); got != sent {
				t.Errorf("refused binding sent %d more requests", got-sent)
			}
		})
	}
}
