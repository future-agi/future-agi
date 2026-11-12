package mcp

import (
	"bytes"
	"context"
	"encoding/json"
	"errors"
	"io"
	"net/http"
	"reflect"
	"sync/atomic"
	"testing"
	"time"
)

// Keep this interface independent of the dispatch implementation so the same
// tests can also verify the raw-call contract before the lifetime guard.
type adoptedRawCaller interface {
	CallToolRaw(context.Context, string, json.RawMessage, CallOptions) (json.RawMessage, error)
	ReadResourceRaw(context.Context, string, CallOptions) (json.RawMessage, error)
	GetPromptRaw(context.Context, string, json.RawMessage, CallOptions) (json.RawMessage, error)
}

var adoptedMethods = []string{MethodToolsCall, MethodResourcesRead, MethodPromptsGet}

const adoptedArguments = `{"number":9007199254740993,"nested":{"ok":true}}`
const adoptedResult = `{"content":[],"contents":[],"messages":[],"vendor":{"number":9007199254740993}}`

func callAdopted(c adoptedRawCaller, method string, opts CallOptions) (json.RawMessage, error) {
	ctx := context.Background()
	switch method {
	case MethodToolsCall:
		return c.CallToolRaw(ctx, "echo", json.RawMessage(adoptedArguments), opts)
	case MethodResourcesRead:
		return c.ReadResourceRaw(ctx, "file:///a", opts)
	default:
		return c.GetPromptRaw(ctx, "greet", json.RawMessage(adoptedArguments), opts)
	}
}

// The mock records only operational requests; handshakes and list refreshes
// use the existing fake upstream. All values and endpoints are synthetic.
func adoptedDispatchHarness(t *testing.T) (*Server, *Client, <-chan fakeRequest, *atomic.Int32) {
	t.Helper()
	calls := make(chan fakeRequest, 16)
	mode := &atomic.Int32{}
	f := newFakeUpstream(t, "/mcp", func(f *fakeUpstream) {
		f.wrap = func(next http.Handler) http.Handler {
			return http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
				body, _ := io.ReadAll(r.Body)
				r.Body = io.NopCloser(bytes.NewReader(body))
				var msg Message
				_ = json.Unmarshal(body, &msg)
				if msg.Method != MethodToolsCall && msg.Method != MethodResourcesRead && msg.Method != MethodPromptsGet {
					next.ServeHTTP(w, r)
					return
				}
				calls <- fakeRequest{Method: msg.Method, Params: msg.Params, Header: r.Header.Clone()}
				w.Header().Set("Content-Type", "application/json")
				switch mode.Load() {
				case 1:
					<-r.Context().Done()
				case 2:
					_, _ = w.Write([]byte(`{"jsonrpc":"2.0","id":` + string(msg.ID) + `,"error":{"code":-32042,"message":"synthetic refusal","data":{"vendor":true}}}`))
				default:
					_, _ = w.Write([]byte(`{"jsonrpc":"2.0","id":` + string(msg.ID) + `,"result":` + adoptedResult + `}`))
				}
			})
		}
	})
	c := connectFake(t, f, ClientConfig{ServerID: "adopted", Auth: AuthConfig{Type: "bearer", Token: "synthetic-static"},
		Call: CallSettings{ForwardHeaders: []string{"X-Request-Id"}}})
	s := NewServer(ServerConfig{})
	t.Cleanup(s.Close)
	s.RegisterClient(c)
	return s, c, calls, mode
}

func resolveAdopted(t *testing.T, s *Server, clients map[string]UpstreamClient) adoptedRawCaller {
	t.Helper()
	pool := &w3bPool{org: "org-a", servers: map[string]PoolServer{"adopted": {ID: "adopted", Source: "static"}}, clients: clients}
	ctx := context.WithValue(context.Background(), tenantPoolKey{}, pool)
	ctx = context.WithValue(ctx, callViewKey{}, &callView{})
	c, denial := s.callClient(&CallContext{ctx: ctx}, "adopted")
	if denial != nil {
		t.Fatal("adopted client resolution refused")
	}
	return c
}

func TestAdoptedPoolPreservesRawCallOptions(t *testing.T) {
	for _, method := range adoptedMethods {
		t.Run(method, func(t *testing.T) {
			for _, full := range []bool{false, true} {
				name := "forwarded_headers"
				if full {
					name = "all_options"
				}
				t.Run(name, func(t *testing.T) {
					s, direct, calls, _ := adoptedDispatchHarness(t)
					opts := CallOptions{ForwardHeaders: http.Header{"X-Request-Id": {"synthetic-first", "synthetic-second"}, "X-Not-Configured": {"omit"}}}
					wantAuth := "Bearer synthetic-static"
					if full {
						wantAuth = "Bearer synthetic-override"
						opts.Auth = &AuthOverride{Header: http.Header{"Authorization": {wantAuth}}}
						opts.Timeout = 2 * time.Second
						opts.ProgressToken = `"synthetic-progress"`
						opts.Meta = map[string]json.RawMessage{"vendor/trace": json.RawMessage(`{"request":"synthetic"}`)}
					}
					// The raw client is the control; StaticClients -> AdaptClient ->
					// legacyClient must preserve the same wire behavior in the pool.
					for _, client := range []adoptedRawCaller{direct, resolveAdopted(t, s, s.StaticClients())} {
						out, err := callAdopted(client, method, opts)
						if err != nil {
							t.Fatalf("valid adopted call failed: %v", err)
						}
						if string(out) != adoptedResult {
							t.Fatal("raw upstream result was changed")
						}
						got := <-calls
						if got.Method != method || !reflect.DeepEqual(got.Header.Values("X-Request-Id"), opts.ForwardHeaders.Values("X-Request-Id")) || got.Header.Get("X-Not-Configured") != "" {
							t.Fatal("configured forwarded headers were not preserved")
						}
						if got.Header.Get("Authorization") != wantAuth {
							t.Fatal("the per-call credential was not preserved")
						}
						var params map[string]json.RawMessage
						if err := json.Unmarshal(got.Params, &params); err != nil {
							t.Fatal(err)
						}
						if method != MethodResourcesRead && string(params["arguments"]) != adoptedArguments {
							t.Fatal("raw arguments were changed")
						}
						if full {
							var meta map[string]json.RawMessage
							_ = json.Unmarshal(params["_meta"], &meta)
							if string(meta["vendor/trace"]) != string(opts.Meta["vendor/trace"]) || len(meta["progressToken"]) == 0 || string(meta["progressToken"]) == opts.ProgressToken {
								t.Fatal("metadata or the rewritten progress token was lost")
							}
						}
					}
				})
			}
		})
	}
}

func TestAdoptedPoolPreservesRawErrorsAndBounds(t *testing.T) {
	for _, method := range adoptedMethods {
		t.Run(method, func(t *testing.T) {
			s, direct, calls, mode := adoptedDispatchHarness(t)
			c := resolveAdopted(t, s, s.StaticClients())
			mode.Store(2)
			_, err := callAdopted(c, method, CallOptions{})
			var rpc *UpstreamError
			if !errors.As(err, &rpc) || rpc.Code != -32042 || string(rpc.Data) != `{"vendor":true}` {
				t.Fatal("raw upstream error lost its code or data")
			}
			<-calls
			mode.Store(1)
			if _, err := callAdopted(c, method, CallOptions{Timeout: 100 * time.Millisecond}); !errors.Is(err, context.DeadlineExceeded) {
				t.Fatalf("per-call timeout was not honored: %v", err)
			}
			mode.Store(0)
			settings := direct.CallSettings()
			settings.MaxResultBytes = 32
			direct.SetCallSettings(settings)
			if _, err := callAdopted(c, method, CallOptions{}); !errors.Is(err, ErrResultTooLarge) {
				t.Fatalf("configured result bound was not honored: %v", err)
			}
		})
	}
}

// A pool guard can change after acquisition, and exposes Upstream solely for
// settings discovery. No operational call may use that unwrapped client.
type adoptedLifetimeGuard struct {
	UpstreamClient
	revoked bool
}

func (g *adoptedLifetimeGuard) Upstream() UpstreamClient { return g.UpstreamClient }
func (g *adoptedLifetimeGuard) refusal() error {
	return &Denial{Type: "auth_required", Code: "auth_required", Message: "Connect your account for this server to use its tools."}
}
func (g *adoptedLifetimeGuard) CallTool(ctx context.Context, n string, a json.RawMessage, o CallOptions) (json.RawMessage, error) {
	if g.revoked {
		return nil, g.refusal()
	}
	return g.UpstreamClient.CallTool(ctx, n, a, o)
}
func (g *adoptedLifetimeGuard) ReadResource(ctx context.Context, u string, o CallOptions) (json.RawMessage, error) {
	if g.revoked {
		return nil, g.refusal()
	}
	return g.UpstreamClient.ReadResource(ctx, u, o)
}
func (g *adoptedLifetimeGuard) GetPrompt(ctx context.Context, n string, a json.RawMessage, o CallOptions) (json.RawMessage, error) {
	if g.revoked {
		return nil, g.refusal()
	}
	return g.UpstreamClient.GetPrompt(ctx, n, a, o)
}

func TestCredentialLifecycleAdoptedDispatchAfterAcquisition(t *testing.T) {
	for _, method := range adoptedMethods {
		t.Run(method, func(t *testing.T) {
			s, _, calls, _ := adoptedDispatchHarness(t)
			clients := s.StaticClients()
			guard := &adoptedLifetimeGuard{UpstreamClient: clients["adopted"]}
			clients["adopted"] = guard
			c := resolveAdopted(t, s, clients)
			opts := CallOptions{ForwardHeaders: http.Header{"X-Request-Id": {"synthetic"}}}
			if _, err := callAdopted(c, method, opts); err != nil {
				t.Fatalf("active adopted client refused: %v", err)
			}
			<-calls
			guard.revoked = true
			_, err := callAdopted(c, method, opts)
			var denial *Denial
			if !errors.As(err, &denial) || denial.Code != "auth_required" || denial.Type != "auth_required" || !denial.InBand() {
				t.Fatal("retained dispatch client did not preserve the lifetime refusal")
			}
			if len(calls) != 0 {
				t.Fatal("revoked client reached upstream")
			}
		})
	}
}
