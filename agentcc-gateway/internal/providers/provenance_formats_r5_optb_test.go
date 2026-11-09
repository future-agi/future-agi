package providers

import (
	"reflect"
	"testing"

	"github.com/futureagi/agentcc-gateway/internal/config"
	"github.com/futureagi/agentcc-gateway/internal/tenant"
)

func TestOptBR5OrgProvenanceAllFactoryFormats(t *testing.T) {
	for _, format := range []string{"openai", "anthropic", "azure", "bedrock", "cohere", "gemini", "google", "deepgram", "cartesia"} {
		t.Run(format, func(t *testing.T) {
			cfg := config.ProviderConfig{APIFormat: format, BaseURL: "http://127.0.0.1:1", APIKey: "synthetic-platform"}
			var tenantCfg *tenant.ProviderConfig
			if format == "bedrock" {
				cfg.AWSAccessKeyID, cfg.AWSSecretAccessKey = "fixture-platform-access", "fixture-platform-secret"
				tenantCfg = &tenant.ProviderConfig{AWSAccessKeyID: "fixture-tenant-access", AWSSecretAccessKey: "fixture-tenant-secret"}
			}
			global, err := createProvider("edge_org_acme", cfg)
			if err != nil {
				t.Fatal(err)
			}
			defer global.Close()
			cache := NewOrgProviderCache(map[string]config.ProviderConfig{"edge": cfg}, false)
			org, err := cache.GetOrCreateWithTenantConfig("acme", "edge", "synthetic-tenant", tenantCfg)
			if err != nil {
				t.Fatal(err)
			}
			defer org.Close()
			if IsOrgInstance(global, "acme") || !IsOrgInstance(org, "acme") || IsOrgInstance(org, "other") {
				t.Fatal("ownership did not follow exact org-cache provenance")
			}
			gt, ot := reflect.TypeOf(global), reflect.TypeOf(org)
			for i := 0; i < gt.NumMethod(); i++ {
				gm := gt.Method(i)
				om, ok := ot.MethodByName(gm.Name)
				if !ok {
					t.Fatalf("lost %s method", gm.Name)
				}
				if gm.Type.NumIn() != om.Type.NumIn() || gm.Type.NumOut() != om.Type.NumOut() || gm.Type.IsVariadic() != om.Type.IsVariadic() {
					t.Fatalf("changed signature of %s", gm.Name)
				}
				for j := 1; j < gm.Type.NumIn(); j++ {
					if gm.Type.In(j) != om.Type.In(j) {
						t.Fatalf("changed input of %s", gm.Name)
					}
				}
				for j := 0; j < gm.Type.NumOut(); j++ {
					if gm.Type.Out(j) != om.Type.Out(j) {
						t.Fatalf("changed output of %s", gm.Name)
					}
				}
			}
		})
	}
}
