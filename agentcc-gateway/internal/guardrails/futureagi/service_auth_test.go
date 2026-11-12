package futureagi

import (
	"context"
	"encoding/json"
	"io"
	"net/http"
	"net/http/httptest"
	"strings"
	"sync"
	"sync/atomic"
	"testing"
	"time"

	"github.com/futureagi/agentcc-gateway/internal/auth"
	"github.com/futureagi/agentcc-gateway/internal/config"
	"github.com/futureagi/agentcc-gateway/internal/guardrails"
	"github.com/futureagi/agentcc-gateway/internal/middleware"
	"github.com/futureagi/agentcc-gateway/internal/models"
	authplugin "github.com/futureagi/agentcc-gateway/internal/plugins/auth"
)

const serviceOrg = "11111111-1111-4111-8111-111111111111"
const serviceUser = "33333333-3333-4333-8333-333333333333"
const serviceWorkspace = "22222222-2222-4222-8222-222222222222"

func serviceInput() *guardrails.CheckInput {
	return makeInput([]models.Message{makeMsg("user", "evaluate me")})
}

// Use the real admission and auth plugin, not caller-supplied identity headers.
func personalCheck(t *testing.T, g *FutureAGIGuardrail, input *guardrails.CheckInput) *guardrails.CheckResult {
	t.Helper()
	ks := auth.NewKeyStore(config.AuthConfig{Enabled: true})
	if ks.LoadFromHashes([]auth.SyncedKey{{
		ID: "service-person", KeyHash: auth.HashKey("test-only-personal-gateway-credential"), Name: "personal", Owner: serviceUser,
		Metadata: map[string]string{"org_id": serviceOrg, "user_id": serviceUser, "workspace_id": serviceWorkspace},
	}}) != 1 {
		t.Fatal("control-plane person fixture was not loaded")
	}
	var result *guardrails.CheckResult
	h := middleware.KeyAuth(ks, true, nil)(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		rc := &models.RequestContext{Metadata: map[string]string{"authorization": r.Header.Get("Authorization")}}
		authplugin.New(ks, true).ProcessRequest(r.Context(), rc)
		if rc.Metadata["auth_key_id"] == "" {
			t.Fatal("fixture was not authenticated")
		}
		if input != nil {
			input.Metadata = rc.Metadata
		}
		result = g.Check(r.Context(), input)
	}))
	r := httptest.NewRequest(http.MethodPost, "/v1/chat/completions", nil)
	r.Header.Set("Authorization", "Bearer test-only-personal-gateway-credential")
	h.ServeHTTP(httptest.NewRecorder(), r)
	if result == nil {
		t.Fatal("fixture never reached guardrail")
	}
	return result
}

func Test733GatewayServicePersonalRetry(t *testing.T) {
	secret := checkServiceVectors(t)
	t.Setenv("AGENTCC_ADMIN_TOKEN", secret)
	var mu sync.Mutex
	var ids []string
	input := serviceInput()
	stub := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		body, _ := io.ReadAll(r.Body)
		f, err := verifyServiceWire(secret, r.Header.Get(auth.EnvelopeHeader))
		if err != nil || len(f) != 19 {
			t.Error("FA stub rejected envelope")
			w.WriteHeader(http.StatusUnauthorized)
			return
		}
		if f[1] != "request" || f[2] != "k1" || f[3] != "gateway" || f[4] != "fa-eval" || f[5] != r.Method || f[6] != r.URL.Path ||
			f[10] != "-" || f[11] != serviceOrg || f[12] != serviceWorkspace || f[13] != serviceUser || f[14] != "key:"+input.Metadata["auth_key_id"] ||
			f[15] != "-" || f[16] != "eval" || f[17] != auth.EnvelopeDigest(body) || f[18] != "-" {
			t.Error("FA stub rejected principal/request binding")
			w.WriteHeader(http.StatusUnauthorized)
			return
		}
		assertServiceWindow(t, f)
		if r.Header.Get("X-Api-Key") != "" || r.Header.Get("X-Secret-Key") != "" || r.Header.Get(auth.GrantHeader) != "" {
			t.Error("service mode sent a pair or unexpected grant")
		}
		mu.Lock()
		defer mu.Unlock()
		for _, previous := range ids {
			if previous == f[9] {
				t.Error("retry reused a consumed nonce")
				w.WriteHeader(http.StatusUnauthorized)
				return
			}
		}
		ids = append(ids, f[9])
		if len(ids) == 1 {
			w.WriteHeader(http.StatusServiceUnavailable)
			return
		}
		io.WriteString(w, `{"result":[{"evaluations":[{"output":"Passed"}]}]}`)
	}))
	defer stub.Close()
	t.Setenv("FI_BASE_URL", stub.URL)
	cfg := map[string]interface{}{"provider": "futureagi", "auth": "gateway_service", "eval_id": "15", "retry": 1, "api_key": "stale", "secret_key": "stale"}
	g := New("service", cfg, nil)
	if err := ValidateConfig("service", map[string]interface{}{"provider": "futureagi", "auth": "gateway_service", "eval_id": "15"}); err != nil {
		t.Fatalf("service validation required a pair: %v", err)
	}
	if got := personalCheck(t, g, input); !got.Pass {
		t.Fatalf("signed retry failed: %s", got.Message)
	}
	mu.Lock()
	defer mu.Unlock()
	if len(ids) != 2 {
		t.Fatalf("expected two independently signed attempts, got %d", len(ids))
	}
}

func Test733GatewayServiceDelegated(t *testing.T) {
	secret := checkServiceVectors(t)
	t.Setenv("AGENTCC_ADMIN_TOKEN", secret)
	for _, kind := range []string{"playground", "tryit", "guardrail-test", "batch"} {
		t.Run(kind, func(t *testing.T) {
			grant, claims := serviceGrant(t, secret, kind)
			var calls atomic.Int32
			stub := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
				calls.Add(1)
				body, _ := io.ReadAll(r.Body)
				f, err := verifyServiceWire(secret, r.Header.Get(auth.EnvelopeHeader))
				if err != nil || len(f) != 19 {
					t.Error("missing or invalid eval envelope")
					w.WriteHeader(401)
					return
				}
				if f[14] != "grant:"+claims.ID || f[11] != claims.Org || f[12] != claims.Workspace || f[13] != claims.User ||
					f[17] != auth.EnvelopeDigest(body) || f[18] != auth.EnvelopeDigest([]byte(grant)) || r.Header.Get(auth.GrantHeader) != grant {
					t.Error("delegation was not forwarded and bound exactly")
					w.WriteHeader(401)
					return
				}
				assertServiceWindow(t, f)
				io.WriteString(w, `{"result":[{"evaluations":[{"output":"Passed"}]}]}`)
			}))
			defer stub.Close()
			t.Setenv("FI_BASE_URL", stub.URL)
			g := New("service", map[string]interface{}{"auth": "gateway_service", "eval_id": "15"}, nil)
			ctx := auth.WithDelegatedPrincipal(context.Background(), claims, grant)
			for _, input := range []*guardrails.CheckInput{serviceInput(), makeOutputInput("streamed answer")} {
				if got := g.Check(ctx, input); !got.Pass {
					t.Fatalf("delegated check failed: %s", got.Message)
				}
			}
			claims.Exp = time.Now().Unix() - 1
			if got := g.Check(auth.WithDelegatedPrincipal(context.Background(), claims, grant), serviceInput()); got.Pass {
				t.Fatal("expired delegation passed")
			}
			if calls.Load() != 2 {
				t.Fatalf("expired delegation reached FA; calls=%d", calls.Load())
			}
		})
	}
}

func Test733GatewayServiceNeverSignsOtherDestinations(t *testing.T) {
	t.Setenv("AGENTCC_ADMIN_TOKEN", checkServiceVectors(t))
	var calls atomic.Int32
	other := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		calls.Add(1)
		io.WriteString(w, `{"result":[]}`)
	}))
	defer other.Close()
	t.Setenv("FI_BASE_URL", "http://127.0.0.1:1")
	for _, target := range []string{other.URL, other.URL + "/prefix", defaultBaseURL, "http://127.0.0.1:1?x=1", "http://127.0.0.1:1#x", "http://user@127.0.0.1:1"} {
		cfg := map[string]interface{}{"auth": "gateway_service", "eval_id": "15", "base_url": target, "api_key": "pair", "secret_key": "pair"}
		for _, g := range []*FutureAGIGuardrail{newGuard("service", cfg), NewConfigFileRule("service", cfg)} {
			if got := personalCheck(t, g, serviceInput()); got.Pass {
				t.Fatal("service mode accepted a non-operator URL")
			}
		}
	}
	if calls.Load() != 0 {
		t.Fatal("non-operator base_url was called")
	}
}

func Test733GatewayServiceRefusesRedirects(t *testing.T) {
	t.Setenv("AGENTCC_ADMIN_TOKEN", checkServiceVectors(t))
	for _, code := range []int{http.StatusFound, http.StatusTemporaryRedirect, http.StatusPermanentRedirect} {
		t.Run(http.StatusText(code), func(t *testing.T) {
			var redirected atomic.Int32
			sink := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
				redirected.Add(1)
				io.WriteString(w, `{"result":[]}`)
			}))
			defer sink.Close()
			stub := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
				http.Redirect(w, r, sink.URL+"/sdk/api/v1/eval/", code)
			}))
			defer stub.Close()
			t.Setenv("FI_BASE_URL", stub.URL)
			g := New("service", map[string]interface{}{"auth": "gateway_service", "eval_id": "15"}, nil)
			if got := personalCheck(t, g, serviceInput()); got.Pass || redirected.Load() != 0 {
				t.Fatal("service mode followed a redirect")
			}
		})
	}
}

func Test733GatewayServiceAndUnsetPairFailClosed(t *testing.T) {
	t.Setenv("FI_BASE_URL", "http://127.0.0.1:1")
	t.Setenv("AGENTCC_ADMIN_TOKEN", "")
	t.Setenv("FI_API_KEY", "process-pair-must-not-be-used")
	t.Setenv("FI_SECRET_KEY", "process-pair-must-not-be-used")
	for _, mode := range []string{"", "gateway_service", "typo"} {
		g := New("unset", map[string]interface{}{"auth": mode, "eval_id": "15"}, nil)
		for _, input := range []*guardrails.CheckInput{nil, {}, serviceInput(), makeOutputInput("answer")} {
			if got := g.Check(context.Background(), input); got.Pass {
				t.Fatalf("no credential passed for auth=%q", mode)
			}
		}
	}
}

func Test733GatewayServiceNeedsAuthenticatedPrincipal(t *testing.T) {
	t.Setenv("FI_BASE_URL", "http://127.0.0.1:1")
	t.Setenv("AGENTCC_ADMIN_TOKEN", checkServiceVectors(t))
	g := New("service", map[string]interface{}{"auth": "gateway_service", "eval_id": "15"}, nil)
	input := serviceInput()
	input.Metadata = map[string]string{"auth_key_id": "spoof", "key_org_id": serviceOrg, "key_user_id": serviceUser}
	if got := g.Check(context.Background(), input); got.Pass || !strings.Contains(got.Message, "principal") {
		t.Fatal("metadata alone must not authorize signing")
	}
}

func Test733GatewayServicePairModeUnchanged(t *testing.T) {
	t.Setenv("AGENTCC_ADMIN_TOKEN", checkServiceVectors(t))
	var calls atomic.Int32
	stub := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		calls.Add(1)
		if r.Header.Get(auth.EnvelopeHeader) != "" || r.Header.Get(auth.GrantHeader) != "" ||
			r.Header.Get("X-Api-Key") != "operator-personal-key" || r.Header.Get("X-Secret-Key") != "operator-personal-secret" {
			t.Error("pair mode changed")
		}
		json.NewEncoder(w).Encode(map[string]interface{}{"result": []interface{}{}})
	}))
	defer stub.Close()
	t.Setenv("FI_BASE_URL", stub.URL)
	g := New("pair", map[string]interface{}{"eval_id": "15", "api_key": "operator-personal-key", "secret_key": "operator-personal-secret"}, nil)
	if got := g.Check(context.Background(), serviceInput()); !got.Pass || calls.Load() != 1 {
		t.Fatal("pair mode did not call its backend")
	}
}
