package cache

import (
	"context"
	"testing"
	"time"

	"github.com/futureagi/agentcc-gateway/internal/cache"
	"github.com/futureagi/agentcc-gateway/internal/models"
	"github.com/futureagi/agentcc-gateway/internal/pipeline"
	"github.com/futureagi/agentcc-gateway/internal/tenant"
)

// The response cache must never cross tenants. The org comes from the
// authenticated key: the auth plugin and Handlers.peekKeyOrgID copy the key's
// metadata org_id into rc.Metadata["key_org_id"], and caller-supplied metadata
// cannot write key_* keys. These tests drive the plugin the way a request does
// (ProcessRequest, provider answer, ProcessResponse) with only that key set.

const (
	orgA = "11111111-1111-1111-1111-111111111111"
	orgB = "22222222-2222-2222-2222-222222222222"
)

// newOrgScopedPlugin returns a plugin with both cache layers on the in-memory
// backends.
func newOrgScopedPlugin() *Plugin {
	p := NewFromStore(cache.NewStore(1000), 5*time.Minute)
	p.SetSemanticStoreFromConcrete(cache.NewSemanticStore(0.8, 256, 1000))
	return p
}

// prime runs one request for org through the plugin and stores respID as the
// provider's answer. orgID "" is a request whose key carries no org. It
// returns the exact-match key the entry was written under.
func prime(t *testing.T, p *Plugin, orgID, model, prompt, respID string) string {
	t.Helper()
	rc := models.AcquireRequestContext()
	defer rc.Release()
	rc.Request = makeReq(model, prompt)
	rc.Model = model
	if orgID != "" {
		rc.Metadata[tenant.MetadataKeyOrgID] = orgID
	}
	if res := p.ProcessRequest(context.Background(), rc); res.Action != pipeline.Continue {
		t.Fatalf("prime(%q): first request must miss, got action %v status %q", orgID, res.Action, rc.Metadata["cache_status"])
	}
	rc.Response = makeResp(respID)
	p.ProcessResponse(context.Background(), rc)
	return rc.Metadata["cache_key"]
}

// ask sends one request for org and reports the cache status and the response
// ID it was served ("" on a miss).
func ask(p *Plugin, orgID, model, prompt string) (status, respID string) {
	rc := models.AcquireRequestContext()
	defer rc.Release()
	rc.Request = makeReq(model, prompt)
	rc.Model = model
	if orgID != "" {
		rc.Metadata[tenant.MetadataKeyOrgID] = orgID
	}
	res := p.ProcessRequest(context.Background(), rc)
	if res.Action == pipeline.ShortCircuit && rc.Response != nil {
		respID = rc.Response.ID
	}
	return rc.Metadata["cache_status"], respID
}

const (
	franceQ     = "what is the capital of France"
	franceQNear = "what is the capital of france?"
)

func TestOrgScope_ExactHitNeverCrossesOrgs(t *testing.T) {
	p := newOrgScopedPlugin()
	prime(t, p, orgA, "gpt-4o", franceQ, "org-a-secret")

	status, got := ask(p, orgB, "gpt-4o", franceQ)
	if got != "" {
		t.Fatalf("org B was served org A's cached response %q (status %q)", got, status)
	}
	if status != "miss" {
		t.Fatalf("org B status = %q, want miss", status)
	}
}

func TestOrgScope_SemanticHitNeverCrossesOrgs(t *testing.T) {
	p := newOrgScopedPlugin()
	prime(t, p, orgA, "gpt-4o", franceQ, "org-a-secret")

	status, got := ask(p, orgB, "gpt-4o", franceQNear)
	if got != "" {
		t.Fatalf("org B was served org A's response %q through the semantic layer (status %q)", got, status)
	}
	if status != "miss" {
		t.Fatalf("org B status = %q, want miss", status)
	}
}

func TestOrgScope_SameOrgStillHits(t *testing.T) {
	p := newOrgScopedPlugin()
	prime(t, p, orgA, "gpt-4o", franceQ, "org-a-resp")

	if status, got := ask(p, orgA, "gpt-4o", franceQ); status != "hit_exact" || got != "org-a-resp" {
		t.Fatalf("same org exact: status %q resp %q, want hit_exact org-a-resp", status, got)
	}
	if status, got := ask(p, orgA, "gpt-4o", franceQNear); status != "hit_semantic" || got != "org-a-resp" {
		t.Fatalf("same org semantic: status %q resp %q, want hit_semantic org-a-resp", status, got)
	}
}

func TestOrgScope_NoOrgIsItsOwnPartition(t *testing.T) {
	t.Run("org entry is invisible to a no-org key", func(t *testing.T) {
		p := newOrgScopedPlugin()
		prime(t, p, orgA, "gpt-4o", franceQ, "org-a-secret")
		for _, prompt := range []string{franceQ, franceQNear} {
			if status, got := ask(p, "", "gpt-4o", prompt); got != "" {
				t.Fatalf("no-org key served org A's response %q for %q (status %q)", got, prompt, status)
			}
		}
	})
	t.Run("no-org entry is invisible to an org key", func(t *testing.T) {
		p := newOrgScopedPlugin()
		prime(t, p, "", "gpt-4o", franceQ, "no-org-resp")
		for _, prompt := range []string{franceQ, franceQNear} {
			if status, got := ask(p, orgB, "gpt-4o", prompt); got != "" {
				t.Fatalf("org B served the no-org response %q for %q (status %q)", got, prompt, status)
			}
		}
	})
	t.Run("no-org still hits itself", func(t *testing.T) {
		p := newOrgScopedPlugin()
		prime(t, p, "", "gpt-4o", franceQ, "no-org-resp")
		if status, got := ask(p, "", "gpt-4o", franceQ); status != "hit_exact" || got != "no-org-resp" {
			t.Fatalf("no-org exact: status %q resp %q", status, got)
		}
		if status, got := ask(p, "", "gpt-4o", franceQNear); status != "hit_semantic" || got != "no-org-resp" {
			t.Fatalf("no-org semantic: status %q resp %q", status, got)
		}
	})
}

// The namespace header is caller input, so it may only narrow a partition,
// never reach into another org's.
func TestOrgScope_NamespaceCannotReachAnotherOrg(t *testing.T) {
	p := newOrgScopedPlugin()
	prime(t, p, orgA, "gpt-4o", franceQ, "org-a-secret")

	for _, ns := range []string{"", "default", orgA, orgA + "|default", "|" + orgA} {
		rc := models.AcquireRequestContext()
		rc.Request = makeReq("gpt-4o", franceQ)
		rc.Model = "gpt-4o"
		rc.Metadata[tenant.MetadataKeyOrgID] = orgB
		if ns != "" {
			rc.Metadata["cache_namespace"] = ns
		}
		res := p.ProcessRequest(context.Background(), rc)
		if res.Action == pipeline.ShortCircuit {
			t.Errorf("org B with namespace %q was served %q", ns, rc.Response.ID)
		}
		rc.Release()
	}
}
