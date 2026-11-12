package models

import (
	"bytes"
	"context"
	"encoding/json"
	"reflect"
	"testing"
)

var (
	_ PassThroughRequest                                                 = NativeBody(nil)
	_ func(NativeBody, context.Context) (*ChatCompletionRequest, string) = NativeBody.ChatRequest
	_ func(NativeBody) error                                             = NativeBody.ApplyChecks
	_ func(NativeBody) []byte                                            = NativeBody.Body
)

func nativeRoutingOptionsForTest() RoutingOptions {
	return RoutingOptions{
		Route: "test-route",
		ComputerUse: ComputerUseRequirement{
			Required: true, Protocol: NativeMessages, Action: "test-action", Stream: true,
			ToolProtocol: "test-tool", VendorVersion: "test-version",
		},
		ComputerUseBinding: NativeRouteBinding{
			CanonicalModel: "test-canonical", SentModel: "test-sent", ModelRevision: "test-model-revision",
			ProviderID: "test-provider", ProviderRevision: "test-provider-revision",
			ProviderRouteID: "test-provider-route", RouteRevision: "test-route-revision",
			EndpointType: "test-endpoint-type", EndpointID: "test-endpoint", UpstreamProvider: "test-upstream",
			CredentialType: CredentialBYOK, Protocol: NativeMessages, Action: "test-action", Stream: true,
			ToolProtocol: "test-tool", VendorVersion: "test-version", CapabilityRevision: "test-capability-revision",
		},
	}
}

func TestNativeRoutingFieldsJSONHidden(t *testing.T) {
	options := nativeRoutingOptionsForTest()
	withoutNative := options
	withoutNative.ComputerUse = ComputerUseRequirement{}
	withoutNative.ComputerUseBinding = NativeRouteBinding{}
	encoded, err := json.Marshal(options)
	if err != nil {
		t.Fatal(err)
	}
	baseline, err := json.Marshal(withoutNative)
	if err != nil {
		t.Fatal(err)
	}
	if !bytes.Equal(encoded, baseline) {
		t.Fatal("internal native state changed routing JSON")
	}
	var fields map[string]json.RawMessage
	if err := json.Unmarshal(encoded, &fields); err != nil {
		t.Fatal(err)
	}
	for _, name := range []string{"ComputerUse", "ComputerUseBinding"} {
		if _, exists := fields[name]; exists {
			t.Fatalf("internal field %s was serialized", name)
		}
		field, ok := reflect.TypeOf(options).FieldByName(name)
		if !ok || field.Tag.Get("json") != "-" {
			t.Fatalf("internal field %s must have json exclusion", name)
		}
	}
	if string(fields["Route"]) != `"test-route"` {
		t.Fatal("existing routing JSON changed")
	}
}

func TestNativeRoutingFieldsJSONInputIgnored(t *testing.T) {
	for _, keys := range [][2]string{
		{"ComputerUse", "ComputerUseBinding"},
		{"computerUse", "computerUseBinding"},
		{"computeruse", "computerusebinding"},
		{"computer_use", "computer_use_binding"},
		{"COMPUTERUSE", "COMPUTERUSEBINDING"},
	} {
		t.Run(keys[0], func(t *testing.T) {
			payload, err := json.Marshal(map[string]any{
				"Route": "caller-route",
				keys[0]: ComputerUseRequirement{
					Required: true, Protocol: NativeGenAI, Action: "caller-action", Stream: true,
					ToolProtocol: "caller-tool", VendorVersion: "caller-version",
				},
				keys[1]: NativeRouteBinding{
					CanonicalModel: "caller-model", Protocol: NativeGenAI,
					CredentialType: CredentialManaged, CapabilityRevision: "caller-revision",
				},
			})
			if err != nil {
				t.Fatal(err)
			}
			for _, initial := range []RoutingOptions{{}, nativeRoutingOptionsForTest()} {
				decoded := initial
				if err := json.Unmarshal(payload, &decoded); err != nil {
					t.Fatal(err)
				}
				if decoded.ComputerUse != initial.ComputerUse || decoded.ComputerUseBinding != initial.ComputerUseBinding {
					t.Fatal("caller JSON populated or replaced internal native state")
				}
				if decoded.Route != "caller-route" {
					t.Fatal("existing routing input was not decoded")
				}
			}
		})
	}
}

func TestNativeRoutingFieldsClone(t *testing.T) {
	original := nativeRoutingOptionsForTest()
	cloned := original.Clone()
	if cloned.ComputerUse != original.ComputerUse || cloned.ComputerUseBinding != original.ComputerUseBinding {
		t.Fatal("Clone lost internal native state")
	}
	for _, value := range []reflect.Value{
		reflect.ValueOf(&cloned.ComputerUse).Elem(),
		reflect.ValueOf(&cloned.ComputerUseBinding).Elem(),
	} {
		for i := 0; i < value.NumField(); i++ {
			field := value.Field(i)
			switch field.Kind() {
			case reflect.String:
				field.SetString("changed")
			case reflect.Bool:
				field.SetBool(!field.Bool())
			case reflect.Uint8:
				field.SetUint(uint64(NativeResponses))
			default:
				t.Fatalf("unexpected reference-bearing native field %s", value.Type().Field(i).Name)
			}
		}
	}
	if cloned.ComputerUse == original.ComputerUse || cloned.ComputerUseBinding == original.ComputerUseBinding {
		t.Fatal("clone mutation did not exercise both native values")
	}
	baseline := nativeRoutingOptionsForTest()
	if original.ComputerUse != baseline.ComputerUse || original.ComputerUseBinding != baseline.ComputerUseBinding {
		t.Fatal("clone mutation changed original native state")
	}
	if cloned.Route != original.Route {
		t.Fatal("native mutation changed unrelated routing state")
	}
}

func TestNativeContractScalarBindings(t *testing.T) {
	for _, value := range []any{ComputerUseRequirement{}, NativeRouteBinding{}} {
		typ := reflect.TypeOf(value)
		t.Run(typ.Name(), func(t *testing.T) {
			if !typ.Comparable() {
				t.Fatal("native binding must be comparable")
			}
			for i := 0; i < typ.NumField(); i++ {
				field := typ.Field(i)
				switch field.Type {
				case reflect.TypeOf(""), reflect.TypeOf(false), reflect.TypeOf(NativeProtocolUnknown), reflect.TypeOf(CredentialBYOK):
				default:
					t.Fatalf("%s must remain a scalar value", field.Name)
				}
			}
		})
	}
	binding := nativeRoutingOptionsForTest().ComputerUseBinding
	if !map[NativeRouteBinding]bool{binding: true}[binding] {
		t.Fatal("binding value lookup failed")
	}
	bindingType := reflect.TypeOf(binding)
	credential, ok := bindingType.FieldByName("CredentialType")
	if !ok || credential.Type != reflect.TypeOf(CredentialBYOK) {
		t.Fatal("binding must reuse existing CredentialType")
	}
	for _, value := range []any{ComputerUseRequirement{}, NativeRouteBinding{}} {
		protocol, ok := reflect.TypeOf(value).FieldByName("Protocol")
		if !ok || protocol.Type != reflect.TypeOf(NativeProtocolUnknown) {
			t.Fatal("native values must reuse the shared NativeProtocol")
		}
	}
}

func TestNativeProtocolZeroUnknown(t *testing.T) {
	var protocol NativeProtocol
	if protocol != NativeProtocolUnknown || NativeProtocolUnknown != 0 {
		t.Fatal("zero native protocol must remain unknown")
	}
	seen := map[NativeProtocol]bool{NativeProtocolUnknown: true}
	for _, known := range []NativeProtocol{NativeMessages, NativeResponses, NativeGenAI} {
		if seen[known] {
			t.Fatal("transport families must be distinct and nonzero")
		}
		seen[known] = true
	}
	var options RoutingOptions
	if options.ComputerUse != (ComputerUseRequirement{}) || options.ComputerUseBinding != (NativeRouteBinding{}) {
		t.Fatal("zero routing options must not assert a native requirement or binding")
	}
}

func TestNativeBodyInterface(t *testing.T) {
	typ := reflect.TypeOf((*NativeBody)(nil)).Elem()
	if typ.NumMethod() != 3 {
		t.Fatal("NativeBody must expose only pass-through checks and body access")
	}
	if !typ.Implements(reflect.TypeOf((*PassThroughRequest)(nil)).Elem()) {
		t.Fatal("NativeBody must preserve the existing pass-through interface")
	}
}
