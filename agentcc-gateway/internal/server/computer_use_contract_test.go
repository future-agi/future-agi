package server

import (
	"context"
	"github.com/futureagi/agentcc-gateway/internal/modeldb"
	"github.com/futureagi/agentcc-gateway/internal/models"
	"github.com/futureagi/agentcc-gateway/internal/providers"
	"testing"
)

type syntheticCUASnapshot struct {
	bindings []models.NativeRouteBinding
	caps     *modeldb.ComputerUseCapabilities
}

func (s *syntheticCUASnapshot) Snapshot() ComputerUseSnapshot                  { return s }
func (s *syntheticCUASnapshot) Capabilities() *modeldb.ComputerUseCapabilities { return s.caps }
func (s *syntheticCUASnapshot) CandidateBindings(string, string, string, string, models.CredentialType, models.ComputerUseRequirement) []models.NativeRouteBinding {
	return s.bindings
}
func (s *syntheticCUASnapshot) ProviderBinding(string, string, string, string, string, models.CredentialType, models.ComputerUseRequirement, providers.Provider) (models.NativeRouteBinding, bool) {
	return models.NativeRouteBinding{}, false
}

func TestComputerUseCandidateCopiesDoNotSelect(t *testing.T) {
	rc := models.AcquireRequestContext()
	defer rc.Release()
	rc.EndpointType = "responses"
	rc.Routing.ComputerUse = models.ComputerUseRequirement{Required: true, Protocol: models.NativeResponses, Action: "generate", ToolProtocol: "synthetic", VendorVersion: "test"}
	selected := models.NativeRouteBinding{ProviderID: "selected"}
	rc.Routing.ComputerUseBinding = selected
	b := models.NativeRouteBinding{ProviderID: "candidate", SentModel: "model", EndpointType: "responses", CredentialType: models.CredentialBYOK, Protocol: models.NativeResponses, Action: "generate", ToolProtocol: "synthetic", VendorVersion: "test"}
	source := &syntheticCUASnapshot{bindings: []models.NativeRouteBinding{b}}
	h := &Handlers{computerUseSource: source}
	got := h.computerUseCandidates(source, rc, nil, "candidate", "model", false)
	if len(got) != 1 {
		t.Fatal("candidate copy missing")
	}
	got[0].ProviderID = "mutated"
	if source.bindings[0] != b || rc.Routing.ComputerUseBinding != selected {
		t.Fatal("candidate inspection mutated shared/selected state")
	}
	if _, err := h.checkComputerUseBeforeDispatch(context.Background(), rc, nil, nil, "model"); err == nil {
		t.Fatal("missing actual instance accepted")
	}
}
