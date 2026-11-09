package logging

import (
	"context"
	"fmt"
	"io"
	"net/http"
	"net/http/httptest"
	"strings"
	"testing"

	"github.com/futureagi/agentcc-gateway/internal/billing"
	"github.com/futureagi/agentcc-gateway/internal/capability"
	"github.com/futureagi/agentcc-gateway/internal/config"
	"github.com/futureagi/agentcc-gateway/internal/models"
	"github.com/futureagi/agentcc-gateway/internal/routing"
	"github.com/futureagi/agentcc-gateway/internal/tenant"
)

// This pins the real Call -> Publish -> EnqueueRequest -> webhook path. A
// mutable credential_type label is not used to exempt arbitrary diagnostics.
func TestOptBCapabilityFlusherOwnership(t *testing.T) {
	for _, byok := range []bool{false, true} {
		t.Run(fmt.Sprintf("byok_%t", byok), func(t *testing.T) {
			const key = "optb-capability-log-wire-fixture"
			upstream := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
				_, _ = io.WriteString(w, "opaque-upstream-value "+r.Header.Get("X-Api-Key"))
			}))
			defer upstream.Close()
			var original error
			reg := capability.NewRegistry()
			if err := reg.RegisterCapability("search", capability.Spec{Class: capability.ClassStatelessCrossVendor, EndpointType: "search"}); err != nil {
				t.Fatal(err)
			}
			if err := reg.Register("search", "fixture", capability.AdapterFunc(func(ctx context.Context, c capability.VendorCall) (capability.VendorResponse, error) {
				req, _ := http.NewRequestWithContext(ctx, "POST", c.Credential.BaseURL, nil)
				req.Header.Set("X-Api-Key", c.Credential.APIKey)
				resp, err := upstream.Client().Do(req)
				if err != nil {
					return capability.VendorResponse{}, err
				}
				defer resp.Body.Close()
				raw, err := io.ReadAll(resp.Body)
				if err != nil {
					return capability.VendorResponse{}, err
				}
				original = &capability.VendorError{Status: 401, Message: string(raw)}
				return capability.VendorResponse{}, original
			})); err != nil {
				t.Fatal(err)
			}
			card := &capability.Card{Vendor: "fixture", Capability: "search", SKUs: []capability.SKU{{SKU: "search.fixture", Unit: "request", USDPerUnit: 0.001, FreeOnFailure: true}}, ResaleStatus: capability.ResaleManagedPartner, ResaleTermsRef: "fixture", TermsURL: "https://fixture.invalid", TermsCheckedAt: "2026-10-16", ZDRCapable: true, TrainsOnInput: capability.TrainsNo, Regions: []string{"us", "eu"}, ResultsRetention: "full", Cacheable: capability.CacheNone, EvalExport: "allowed"}
			ledger := billing.NewMemoryLedger()
			if _, err := ledger.Grant(context.Background(), "fixture-org", billing.GrantPush{GrantedTotalMicros: 1_000_000, Version: 1}); err != nil {
				t.Fatal(err)
			}
			core := capability.NewCore(capability.Options{Registry: reg, Cards: capability.NewStaticCards(card), Ledger: ledger, Credentials: capability.ConfigCredentials{Operator: map[string]config.ProviderConfig{"fixture": {APIKey: key, BaseURL: upstream.URL, Managed: true}}}})
			on := true
			org := &tenant.OrgConfig{Providers: map[string]*tenant.ProviderConfig{}, Tools: &tenant.ToolsConfig{Capabilities: map[string]*tenant.ToolsCapabilityConfig{"search": {Enabled: &on, Providers: []string{"fixture"}}}}, MCP: &tenant.MCPOrgConfig{ManagedTools: &tenant.MCPManagedToolsConfig{Enabled: &on, Allow: []string{"search/*"}}}}
			if byok {
				org.Providers["fixture"] = &tenant.ProviderConfig{Enabled: true, APIKey: key}
			}
			rc := models.AcquireRequestContext()
			defer rc.Release()
			rc.RequestID = "req_optb_capability_log"
			rc.Metadata["org_id"] = "fixture-org"
			_, err := core.Do(context.Background(), rc, "search", capability.Request{Org: org})
			if err == nil || original == nil {
				t.Fatal("missing outbound failure")
			}
			if !strings.Contains(original.Error(), key) {
				t.Fatal("outbound credential not reflected")
			}
			rc.AddError(err)
			capability.Publish(rc)
			rec := buildRecord(rc, config.RequestLoggingConfig{Enabled: true})
			entry := optbExport(t, rec, rc)
			attempts := entry["attempts"].([]any)
			msg := attempts[0].(map[string]any)["error_message"]
			if byok {
				if msg != routing.AttemptErrorMessage(original) {
					t.Error("source-bound BYOK attempt diagnostic differs from deployed bytes")
				}
			} else {
				optbAssertErrorFields(t, entry, key)
			}
		})
	}
}
