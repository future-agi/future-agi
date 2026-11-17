package server

import (
	"context"
	"fmt"
	"net/http"
	"strings"
	"testing"

	"github.com/futureagi/agentcc-gateway/internal/config"
	"github.com/futureagi/agentcc-gateway/internal/modeldb"
	"github.com/futureagi/agentcc-gateway/internal/models"
	capbudget "github.com/futureagi/agentcc-gateway/internal/plugins/budget"
	"github.com/futureagi/agentcc-gateway/internal/providers"
	"github.com/futureagi/agentcc-gateway/internal/tenant"
)

type cuaC26R2Route struct {
	name, path, model, provider, endpoint string
	protocol                              models.NativeProtocol
	stream                                bool
	body, ordinaryTools                   string
	declarations                          []string
}

func cuaC26R2Routes() []cuaC26R2Route {
	var routes []cuaC26R2Route
	for _, stream := range []bool{false, true} {
		routes = append(routes,
			cuaC26R2Route{
				name: "messages", path: "/v1/messages", model: postBlockClaude, provider: "anthropic", endpoint: "anthropic_messages", protocol: models.NativeMessages, stream: stream,
				body: messagesBody(postBlockClaude, stream), ordinaryTools: `[{"name":"computer","input_schema":{}}]`,
				declarations: []string{`[{"type":"computer_20251124","name":"computer","display_width_px":1024,"display_height_px":768}]`, `[{"type":"computer_20990101","name":"computer"}]`},
			},
			cuaC26R2Route{
				name: "responses", path: "/v1/responses", model: "gpt-4o", provider: "openai", endpoint: "responses", protocol: models.NativeResponses, stream: stream,
				body: responsesBody(stream), ordinaryTools: `[{"type":"function","name":"computer_use_preview","parameters":{"type":"object"}}]`,
				declarations: []string{`[{"type":"computer_use_preview","display_width":1024,"display_height":768,"environment":"browser"}]`, `[{"type":"computer_use_future"}]`},
			},
		)
		action, endpoint := "generateContent", "genai_generate"
		if stream {
			action, endpoint = "streamGenerateContent", "genai_stream"
		}
		routes = append(routes, cuaC26R2Route{
			name: "gemini", path: "/v1beta/models/" + postBlockGemini + ":" + action, model: postBlockGemini, provider: "gemini", endpoint: endpoint, protocol: models.NativeGenAI, stream: stream,
			body: genaiBody(), ordinaryTools: `[{"functionDeclarations":[{"name":"computerUse","parameters":{"type":"object"}}]}]`,
			declarations: []string{`[{"computerUse":{"environment":"ENVIRONMENT_BROWSER"}}]`, `{"computer_use":{"environment":"ENVIRONMENT_BROWSER"}}`},
		})
	}
	return routes
}

func (r cuaC26R2Route) withTools(tools string) string {
	return strings.TrimSuffix(r.body, "}") + `,"tools":` + tools + `,"opaque":{"private_canary":"synthetic-c26-r2-request"}}`
}

func (r cuaC26R2Route) binding() models.NativeRouteBinding {
	return models.NativeRouteBinding{
		CanonicalModel: r.model, SentModel: r.model, ModelRevision: "synthetic-model-r1",
		ProviderID: r.provider, ProviderRevision: "synthetic-provider-r1", ProviderRouteID: "synthetic-route", RouteRevision: "synthetic-route-r1",
		EndpointType: r.endpoint, EndpointID: "synthetic-endpoint", UpstreamProvider: r.provider, CredentialType: models.CredentialBYOK,
		Protocol: r.protocol, Action: "generate", Stream: r.stream, ToolProtocol: "synthetic-c26-r2", VendorVersion: "synthetic-v1", CapabilityRevision: "synthetic-caps-r1",
	}
}

type cuaC26R2Source struct {
	binding models.NativeRouteBinding
	caps    *modeldb.ComputerUseCapabilities
	actual  providers.Provider
}

func (s *cuaC26R2Source) Snapshot() ComputerUseSnapshot {
	if s == nil {
		return nil
	}
	return s
}
func (s *cuaC26R2Source) Capabilities() *modeldb.ComputerUseCapabilities { return s.caps }
func (s *cuaC26R2Source) CandidateBindings(string, string, string, string, models.CredentialType, models.ComputerUseRequirement) []models.NativeRouteBinding {
	return []models.NativeRouteBinding{s.binding}
}
func (s *cuaC26R2Source) ProviderBinding(orgID, providerID, sentModel, endpointType, endpointID string, mode models.CredentialType, required models.ComputerUseRequirement, actual providers.Provider) (models.NativeRouteBinding, bool) {
	return s.binding, orgID == postBlockOrg && providerID == s.binding.ProviderID && sentModel == s.binding.SentModel && endpointType == s.binding.EndpointType && mode == s.binding.CredentialType && modeldb.MatchesComputerUse(s.binding, required) && actual == s.actual && (endpointID == "" || endpointID == s.binding.EndpointID)
}

func cuaC26R2Capabilities(t *testing.T, binding models.NativeRouteBinding, state string) *modeldb.ComputerUseCapabilities {
	t.Helper()
	var records []modeldb.ComputerUseAttestation
	switch state {
	case "nil-caps":
		return nil
	case "unknown":
	case "unsupported":
		records = []modeldb.ComputerUseAttestation{{Binding: binding, Support: modeldb.ComputerUseUnsupported}}
	case "other-route-supported":
		binding.RouteRevision = "synthetic-other-route"
		records = []modeldb.ComputerUseAttestation{{Binding: binding, Support: modeldb.ComputerUseSupported}}
	case "supported":
		records = []modeldb.ComputerUseAttestation{{Binding: binding, Support: modeldb.ComputerUseSupported}}
	default:
		t.Fatal("unknown synthetic capability state")
	}
	caps, err := modeldb.NewComputerUseCapabilities("synthetic-caps-r1", records)
	if err != nil {
		t.Fatal(err)
	}
	return caps
}

func TestCUAC26R2NativeHandlerUnqualifiedRefusal(t *testing.T) {
	for _, route := range cuaC26R2Routes() {
		for _, state := range []string{"nil-source", "nil-snapshot", "nil-caps", "unknown", "unsupported", "other-route-supported"} {
			t.Run(fmt.Sprintf("%s/stream=%t/%s", route.name, route.stream, state), func(t *testing.T) {
				g, calls, spy := nativeCompatGateway(t, config.ToolPolicyConfig{Enabled: false})
				h := g.srv.handlers
				org := h.tenantStore.Get(postBlockOrg)
				if capbudget.CapsEnabled(org) || h.nativeCaps != nil || (org.ToolPolicy != nil && org.ToolPolicy.Enabled) {
					t.Fatal("negative fixture enabled a cap or ordinary tool policy")
				}
				switch state {
				case "nil-source":
					h.SetComputerUseSource(nil)
				case "nil-snapshot":
					h.SetComputerUseSource((*cuaC26R2Source)(nil))
				default:
					h.SetComputerUseSource(&cuaC26R2Source{binding: route.binding(), caps: cuaC26R2Capabilities(t, route.binding(), state)})
				}
				for i, declaration := range route.declarations {
					t.Run(fmt.Sprintf("declaration=%d", i), func(t *testing.T) {
						code, response := g.do(t, route.path, route.withTools(declaration))
						if code != http.StatusServiceUnavailable || calls.count() != 0 || spy.calls != 0 {
							t.Errorf("unqualified CUA reached compatibility: status=%d upstream=%d pipeline=%d", code, calls.count(), spy.calls)
						}
						if !strings.Contains(response, "computer use is unavailable for this route") || strings.Contains(response, "synthetic-c26-r2-request") || strings.Contains(response, "DROP TABLE") {
							t.Error("refusal omitted its stable reason or exposed request/upstream content")
						}
					})
				}
				// Same real authenticated handler/provider, with a custom function
				// whose name resembles CUA. Disabled policy must still pass it.
				t.Run("ordinary-control", func(t *testing.T) {
					before := calls.count()
					body := route.withTools(route.ordinaryTools)
					code, response := g.do(t, route.path, body)
					if code != http.StatusOK || calls.count() != before+1 || !spy.ordinary || !strings.Contains(response, "DROP TABLE") {
						t.Fatalf("ordinary native request changed: status=%d upstream=%d", code, calls.count()-before)
					}
					calls.mu.Lock()
					outgoing := calls.bodies[len(calls.bodies)-1]
					calls.mu.Unlock()
					if outgoing != body {
						t.Fatal("ordinary native outgoing bytes changed")
					}
				})
			})
		}
	}
}

// Positive control of the existing exact-attestation dispatch check, not a
// production native factory. H01-H05 still own schema/admission installation.
// No server hook is bypassed or replaced to manufacture a handler-level success.
func TestCUAC26R2QualifiedAttestationControl(t *testing.T) {
	for _, route := range cuaC26R2Routes() {
		t.Run(fmt.Sprintf("%s/stream=%t", route.name, route.stream), func(t *testing.T) {
			g, calls, _ := nativeCompatGateway(t, config.ToolPolicyConfig{Enabled: false})
			h := g.srv.handlers
			provider, ok := h.registry.GetProvider(route.provider)
			if !ok {
				t.Fatal("synthetic provider missing")
			}
			binding := route.binding()
			rc := models.AcquireRequestContext()
			defer rc.Release()
			rc.Model, rc.Provider, rc.EndpointType, rc.IsStream = route.model, route.provider, route.endpoint, route.stream
			rc.Metadata[tenant.MetadataKeyOrgID] = postBlockOrg
			rc.Metadata["billing_mode"] = billingBYOK
			rc.Metadata["key_type"] = "byok"
			rc.Routing.ComputerUse = models.ComputerUseRequirement{Required: true, Protocol: route.protocol, Action: binding.Action, Stream: route.stream, ToolProtocol: binding.ToolProtocol, VendorVersion: binding.VendorVersion}
			h.SetComputerUseSource(&cuaC26R2Source{binding: binding, caps: cuaC26R2Capabilities(t, binding, "supported"), actual: provider})
			got, refusal := h.checkComputerUseBeforeDispatch(context.Background(), rc, h.tenantStore.Get(postBlockOrg), provider, route.model)
			if refusal != nil || got != binding {
				t.Fatalf("exact synthetic attestation was not accepted: %v", refusal)
			}
			if calls.count() != 0 {
				t.Fatal("attestation inspection performed provider I/O")
			}
		})
	}
}
