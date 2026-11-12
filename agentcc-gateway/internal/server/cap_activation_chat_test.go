package server

import (
	"context"
	"encoding/json"
	"net/http"
	"os"
	"strconv"
	"testing"
	"time"

	"github.com/futureagi/agentcc-gateway/internal/tenant"
)

// This fixture is emitted by the real Django HTTP create/create/activate test
// in the same gate, not hand-authored config metadata. Only test identities
// are rebound to this gateway's synthetic auth key; revisions and policies
// cross the control-plane/gateway boundary unmodified.
func TestCapConfigActivationToChat(t *testing.T) {
	path := os.Getenv("G8_CONFIG_ACTIVATION_FIXTURE")
	if path == "" {
		t.Skip("requires Django activation fixture from the integration gate")
	}
	raw, err := os.ReadFile(path)
	if err != nil {
		t.Fatal(err)
	}
	var sequence []*tenant.OrgConfig
	if err = json.Unmarshal(raw, &sequence); err != nil {
		t.Fatal(err)
	}
	if len(sequence) != 3 {
		t.Fatalf("activation sequence length %d", len(sequence))
	}
	g, client, prefix := capComposedGateway(t)
	key := g.srv.keyStore.Authenticate(managedOrgKey)
	base := g.tenants.Get("org-m")
	apply := func(index int) {
		cfg := sequence[index]
		copy := *base
		copy.Meta = cfg.Meta
		budgets := *cfg.Budgets
		budgets.KeyIDs = map[string]*tenant.AdmissionCapConfig{key.ID: cfg.Budgets.KeyIDs["key-a"]}
		copy.Budgets = &budgets
		copy.Routing = &tenant.RoutingConfig{Agents: &tenant.AgentsConfig{KeyBindings: map[string]string{key.ID: "agent-a"}}}
		g.tenants.Set("org-m", &copy)
	}
	for i := range sequence {
		if i > 0 && sequence[i].Budgets.PolicyRevision <= sequence[i-1].Budgets.PolicyRevision {
			t.Fatal("activation revision did not advance")
		}
		apply(i)
		w := g.chat(t, chatBody("openai/gpt-6-sol"), nil)
		if w.Code != http.StatusOK {
			t.Fatalf("config %d chat=%d %s", i, w.Code, w.Body)
		}
		state := prefix + "bhold:{org-m}:" + time.Now().UTC().Format("2006-01-02") + ":state"
		if got := client.Redis().HGet(context.Background(), state, "spent:agent:agent-a").Val(); got != strconv.Itoa((i+1)*7350) {
			t.Fatalf("activation reset spend: %s", got)
		}
	}
	if sequence[2].Meta.Version != sequence[0].Meta.Version {
		t.Fatal("not a historical saved-version activation")
	}
	apply(1)
	w := g.chat(t, chatBody("openai/gpt-6-sol"), nil)
	if w.Code != http.StatusServiceUnavailable || g.managed.calls() != 3 {
		t.Fatalf("stale replica admitted: %d calls=%d", w.Code, g.managed.calls())
	}
}
