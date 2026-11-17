package tenancy

import (
	"testing"

	"github.com/futureagi/agentcc-gateway/internal/mcp"
)

func TestCredentialLifecycleSuppliedServerUsesCurrentCustomerCredential(t *testing.T) {
	r := newRig(t, Options{})
	u := r.factory.upstream(urlA, "search")
	d := *r.deps.Load()
	d.ManagedServers = fixedManaged{"org-a": {{ServerID: "fagi.search", URL: urlA, Auth: mcp.AuthConfig{Type: "bearer", Token: "synthetic-operator"}}}}
	r.deps.Store(&d)
	r.push("org-a", 1, `{"enabled":true,"managed_tools":{"enabled":true,"credentials":{"fagi.search":{"mode":"shared","credential_id":"customer-one","state":"active","secret":{"type":"bearer","value":"synthetic-customer"}}}}}`)
	_, pool := r.resolve("org-a")
	client, err := pool.Client("fagi.search", "", mcp.AuthOverride{})
	if err != nil {
		t.Fatal(err)
	}
	u.mu.Lock()
	usedCustomer := len(u.auths) == 1 && u.auths[0].Token == "synthetic-customer"
	u.mu.Unlock()
	if !usedCustomer {
		t.Fatal("connection did not use the configured customer credential")
	}
	r.push("org-a", 2, `{"enabled":true,"managed_tools":{"enabled":true,"credentials":{"fagi.search":{"mode":"shared","credential_id":"customer-one","state":"revoked"}}}}`)
	requireCredentialRefusal(t, credentialCall(client))
	if len(u.called()) != 0 {
		t.Fatal("lapsed customer credential reached supplied upstream")
	}
}

func TestCredentialLifecycleStoreRemovalClosesIdleSession(t *testing.T) {
	for _, method := range []string{"delete", "load", "merge"} {
		t.Run(method, func(t *testing.T) {
			r := newRig(t, Options{})
			r.factory.upstream(urlA, "search")
			r.push("org-a", 1, credentialConfig("active", "synthetic-first", ""))
			_, pool := r.resolve("org-a")
			c, err := pool.Client("github", "", mcp.AuthOverride{})
			if err != nil {
				t.Fatal(err)
			}
			switch method {
			case "delete":
				r.store.Delete("org-a")
			case "load":
				r.store.LoadBulk(nil)
			case "merge":
				r.store.MergeBulk(nil, nil)
			}
			eventually(t, "removed org's idle session closed", func() bool { return !c.Healthy() })
			requireCredentialRefusal(t, credentialCall(c))
		})
	}
}
