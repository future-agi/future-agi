package emit

import (
	"github.com/futureagi/agentcc-gateway/internal/connectorcatalog/bundle/canonjson"
	"github.com/futureagi/agentcc-gateway/internal/connectorcatalog/manifest"
	"testing"
)

func TestGatewayEmitsPartnerBinding(t *testing.T) {
	cat := vendorApps(1)
	provider := cat.Apps[0].Providers[0]
	provider.Kind = manifest.KindPartner
	provider.Auth.Partner = &manifest.PartnerAuth{Platform: "composio", Toolkit: "gmail", Version: "1.0.0", Binding: "mint", ToolMode: "fixed"}
	entry := list(t, parse(t, emitTarget(t, TargetGateway, cat, testMeta)["gateway-servers.json"]), "servers")[0]
	raw, err := canonjson.Compact(member(entry, "partner"))
	if err != nil || string(raw) != `{"platform": "composio", "toolkit": "gmail", "version": "1.0.0", "binding": "mint", "toolMode": "fixed"}` {
		t.Fatal("partner binding was dropped")
	}
}
