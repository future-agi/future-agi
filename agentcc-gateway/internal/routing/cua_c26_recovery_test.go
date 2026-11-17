package routing

import (
	"reflect"
	"testing"

	"github.com/futureagi/agentcc-gateway/internal/models"
)

// Reflection keeps this regression executable on the pre-port assembly: a
// missing contract is an assertion failure, not an undefined-symbol failure.
func cuaC26RequiredPolicy(t *testing.T) AutoPolicy {
	t.Helper()
	policy := AutoPolicy{}
	required := reflect.ValueOf(&policy).Elem().FieldByName("ComputerUse")
	if !required.IsValid() || required.Kind() != reflect.Struct {
		t.Fatal("CUA recovery: auto routing has no computer-use restriction contract")
	}
	required.FieldByName("Required").SetBool(true)
	required.FieldByName("Protocol").SetUint(2) // models.NativeResponses; numeric on the pre-port baseline
	required.FieldByName("Action").SetString("generate")
	required.FieldByName("ToolProtocol").SetString("synthetic")
	required.FieldByName("VendorVersion").SetString("test")
	return policy
}

func TestCUAC26RestrictionCannotUseGenericCapabilities(t *testing.T) {
	req := &models.ChatCompletionRequest{Messages: []models.Message{userMsg("synthetic")}}
	pool := autoPool()
	if _, err := ChooseAuto(req, pool, AutoPolicy{}); err != nil {
		t.Fatalf("ordinary routing control failed: %v", err)
	}
	if _, err := ChooseAuto(req, pool, cuaC26RequiredPolicy(t)); err == nil {
		t.Fatal("generic tools/vision capability authorized computer use without an exact attestation")
	}
}

func TestCUAC26CandidateEvidenceIsNotAuthority(t *testing.T) {
	req := &models.ChatCompletionRequest{Messages: []models.Message{userMsg("synthetic")}}
	policy := cuaC26RequiredPolicy(t)
	pool := autoPool()
	bindings := reflect.ValueOf(&pool[1]).Elem().FieldByName("ComputerUseBindings")
	if !bindings.IsValid() || bindings.Kind() != reflect.Slice {
		t.Fatal("CUA recovery: candidate bindings are missing")
	}
	hints := reflect.MakeSlice(bindings.Type(), 1, 1)
	hint := hints.Index(0)
	for field, value := range map[string]string{
		"CanonicalModel": pool[1].Model, "SentModel": pool[1].Model,
		"ProviderID": pool[1].ProviderID, "ModelRevision": "synthetic-m1",
		"ProviderRevision": "synthetic-p1", "ProviderRouteID": "synthetic-route",
		"RouteRevision": "synthetic-r1", "EndpointType": "responses",
		"EndpointID": "synthetic-endpoint", "UpstreamProvider": "synthetic-upstream",
		"Action": "generate", "ToolProtocol": "synthetic", "VendorVersion": "test",
		"CapabilityRevision": "synthetic-c1",
	} {
		hint.FieldByName(field).SetString(value)
	}
	hint.FieldByName("Protocol").SetUint(2)
	hint.FieldByName("CredentialType").SetString(string(models.CredentialBYOK))
	bindings.Set(hints)
	if _, err := ChooseAuto(req, pool, policy); err == nil {
		t.Fatal("unattested candidate scalar hints granted native authority")
	}
}
