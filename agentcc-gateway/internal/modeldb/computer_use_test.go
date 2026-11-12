package modeldb

import (
	"github.com/futureagi/agentcc-gateway/internal/models"
	"reflect"
	"testing"
)

func syntheticBinding() models.NativeRouteBinding {
	return models.NativeRouteBinding{CanonicalModel: "canonical", SentModel: "sent", ModelRevision: "m1", ProviderID: "provider", ProviderRevision: "p1", ProviderRouteID: "route", RouteRevision: "r1", EndpointType: "native", EndpointID: "endpoint", UpstreamProvider: "upstream", CredentialType: models.CredentialBYOK, Protocol: models.NativeMessages, Action: "generate", ToolProtocol: "synthetic", VendorVersion: "test", CapabilityRevision: "c1"}
}

func TestComputerUseExactImmutableUnknown(t *testing.T) {
	b := syntheticBinding()
	var nilSource *ComputerUseCapabilities
	if nilSource.Lookup(b).Support() != ComputerUseUnknown || (ComputerUseDecision{}).Support() != ComputerUseUnknown {
		t.Fatal("zero must be unknown")
	}
	records := []ComputerUseAttestation{{Binding: b, Support: ComputerUseSupported}}
	s, err := NewComputerUseCapabilities("c1", records)
	if err != nil {
		t.Fatal(err)
	}
	records[0].Support = ComputerUseUnsupported
	if s.Lookup(b).Support() != ComputerUseSupported {
		t.Fatal("records escaped")
	}
	// Every scalar is part of the exact key, including sync versus stream.
	for i := 0; i < reflect.TypeOf(b).NumField(); i++ {
		other := b
		v := reflect.ValueOf(&other).Elem().Field(i)
		switch v.Kind() {
		case reflect.String:
			v.SetString(v.String() + "different")
		case reflect.Bool:
			v.SetBool(!v.Bool())
		case reflect.Uint8:
			v.SetUint(v.Uint() + 1)
		}
		if s.Lookup(other).Support() != ComputerUseUnknown {
			t.Fatalf("field %d was not exact", i)
		}
	}
}

func TestComputerUseRegistryValidation(t *testing.T) {
	b := syntheticBinding()
	for _, records := range [][]ComputerUseAttestation{
		{{Binding: b, Support: ComputerUseSupported}, {Binding: b, Support: ComputerUseUnsupported}},
		{{Binding: models.NativeRouteBinding{}, Support: ComputerUseSupported}},
		{{Binding: b, Support: ComputerUseSupport(99)}},
	} {
		if _, err := NewComputerUseCapabilities("c1", records); err == nil {
			t.Fatal("invalid registry accepted")
		}
	}
	if _, err := NewComputerUseCapabilities("", nil); err == nil {
		t.Fatal("missing revision")
	}
	s, err := NewComputerUseCapabilities("c1", []ComputerUseAttestation{{Binding: b, Support: ComputerUseUnsupported}})
	if err != nil || s.Lookup(b).Support() != ComputerUseUnsupported {
		t.Fatal("explicit unsupported lost")
	}
}
