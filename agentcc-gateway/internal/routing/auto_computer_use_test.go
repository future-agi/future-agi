package routing

import (
	"github.com/futureagi/agentcc-gateway/internal/modeldb"
	"github.com/futureagi/agentcc-gateway/internal/models"
	"testing"
)

func TestAutoComputerUseNeverInfersFromGenericFlags(t *testing.T) {
	req := &models.ChatCompletionRequest{Messages: []models.Message{userMsg("synthetic")}}
	policy := AutoPolicy{ComputerUse: models.ComputerUseRequirement{Required: true, Protocol: models.NativeResponses, Action: "generate", ToolProtocol: "synthetic", VendorVersion: "test"}}
	pool := autoPool()
	if _, err := ChooseAuto(req, pool, policy); err == nil {
		t.Fatal("generic tools or vision granted CUA")
	}
	b := models.NativeRouteBinding{CanonicalModel: pool[1].Model, SentModel: pool[1].Model, ModelRevision: "m1", ProviderID: pool[1].ProviderID, ProviderRevision: "p1", ProviderRouteID: "route", RouteRevision: "r1", EndpointType: "responses", EndpointID: "endpoint", UpstreamProvider: "upstream", CredentialType: models.CredentialBYOK, Protocol: models.NativeResponses, Action: "generate", ToolProtocol: "synthetic", VendorVersion: "test", CapabilityRevision: "c1"}
	source, err := modeldb.NewComputerUseCapabilities("c1", []modeldb.ComputerUseAttestation{{Binding: b, Support: modeldb.ComputerUseSupported}})
	if err != nil {
		t.Fatal(err)
	}
	policy.ComputerUseSource = source
	pool[1].ComputerUseBindings = []models.NativeRouteBinding{b}
	d, err := ChooseAuto(req, pool, policy)
	if err != nil || d.Model != pool[1].Model {
		t.Fatal("exact synthetic candidate not selected")
	}
	policy.ComputerUse.Stream = true
	if _, err := ChooseAuto(req, pool, policy); err == nil {
		t.Fatal("sync attestation borrowed for stream")
	}
	policy.ComputerUse.Required = false
	generic, err := ChooseAuto(req, pool, policy)
	baseline, baseErr := ChooseAuto(req, pool, AutoPolicy{})
	if err != nil || baseErr != nil || generic.Model != baseline.Model || generic.Ranked[0].Score != baseline.Ranked[0].Score {
		t.Fatal("generic ranking changed")
	}
	if supportsRequired(pool[1].Info, "unagreed-computer-marker") {
		t.Fatal("public unknown marker enabled")
	}
}
