package eligibility

import (
	"github.com/futureagi/agentcc-gateway/internal/catalog"
	"github.com/futureagi/agentcc-gateway/internal/modeldb"
	"github.com/futureagi/agentcc-gateway/internal/models"
	"testing"
)

func cuaBinding() models.NativeRouteBinding {
	return models.NativeRouteBinding{CanonicalModel: "model", SentModel: "model", ModelRevision: "m1", ProviderID: "provider", ProviderRevision: "p1", ProviderRouteID: "route", RouteRevision: "r1", EndpointType: "native", EndpointID: "supported", UpstreamProvider: "upstream", CredentialType: models.CredentialManaged, Protocol: models.NativeResponses, Action: "generate", ToolProtocol: "synthetic", VendorVersion: "test", CapabilityRevision: "c1"}
}
func cuaRequirement() models.ComputerUseRequirement {
	return models.ComputerUseRequirement{Required: true, Protocol: models.NativeResponses, Action: "generate", ToolProtocol: "synthetic", VendorVersion: "test"}
}

func TestComputerUseNarrowsOnlyAliveEndpoints(t *testing.T) {
	b := cuaBinding()
	s, err := modeldb.NewComputerUseCapabilities("c1", []modeldb.ComputerUseAttestation{{Binding: b, Support: modeldb.ComputerUseSupported}})
	if err != nil {
		t.Fatal(err)
	}
	in := Input{Model: "model", ProviderID: "provider", Managed: true, ComputerUse: cuaRequirement(), ComputerUseBindings: []models.NativeRouteBinding{b}, ComputerUseSource: s, Entry: &catalog.Model{Endpoints: []catalog.Endpoint{{ID: "unknown", Provider: "provider", UpstreamProvider: "upstream"}, {ID: "supported", Provider: "provider", UpstreamProvider: "upstream"}}}}
	c := checker{in: in}
	c.initEndpoints()
	if code, _ := checkComputerUse(&c); code != "" || c.alive != 2 {
		t.Fatal("unknown endpoint survived")
	}
	c.alive = 1
	if code, _ := checkComputerUse(&c); code == "" || c.alive != 0 {
		t.Fatal("earlier denial restored")
	}
}
func TestComputerUseRestrictionAndGenericControl(t *testing.T) {
	b := cuaBinding()
	b.CredentialType = models.CredentialBYOK
	s, _ := modeldb.NewComputerUseCapabilities("c1", []modeldb.ComputerUseAttestation{{Binding: b, Support: modeldb.ComputerUseSupported}})
	base := Input{Model: "model", ProviderID: "provider", ComputerUse: cuaRequirement(), ComputerUseBindings: []models.NativeRouteBinding{b}, ComputerUseSource: s}
	for _, change := range []func(*Input){func(in *Input) { in.ComputerUseSource = nil }, func(in *Input) { in.Model = "other" }, func(in *Input) { in.ProviderID = "other" }, func(in *Input) { in.ComputerUse.Stream = true }, func(in *Input) { in.ComputerUse.VendorVersion = "other" }, func(in *Input) { in.ComputerUseBindings = nil }} {
		in := base
		change(&in)
		c := checker{in: in}
		if code, _ := checkComputerUse(&c); code == "" {
			t.Fatal("mismatch accepted")
		}
	}
	c := checker{in: base}
	if code, _ := checkComputerUse(&c); code != "" {
		t.Fatal("synthetic exact match refused")
	}
	c = checker{in: Input{}}
	if code, _ := checkComputerUse(&c); code != "" {
		t.Fatal("generic behavior changed")
	}
	in := base
	in.Key.AllowedModels = "another"
	if v := Check(&in); v.OK || v.Code == CodeComputerUseUnavailable {
		t.Fatal("CUA overrode earlier ACL")
	}
}
