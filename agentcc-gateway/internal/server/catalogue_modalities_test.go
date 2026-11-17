package server

import (
	"context"
	"crypto/sha256"
	"encoding/base64"
	"encoding/json"
	"errors"
	"fmt"
	"net"
	"net/http"
	"net/http/httptest"
	"os"
	"slices"
	"strconv"
	"strings"
	"sync/atomic"
	"testing"
	"time"

	"github.com/futureagi/agentcc-gateway/internal/catalog"
	"github.com/futureagi/agentcc-gateway/internal/models"
	creditsplugin "github.com/futureagi/agentcc-gateway/internal/plugins/credits"
	"github.com/futureagi/agentcc-gateway/internal/tenant"
)

// Every provider socket in these cases goes through this fail-closed dialer.
// Refusals log only the destination, never a request, credential or payload.
func cat2ModalitiesDialer(t *testing.T, dialed, refused *atomic.Int64) func(context.Context, string, string) (net.Conn, error) {
	return func(ctx context.Context, network, addr string) (net.Conn, error) {
		host, port, err := net.SplitHostPort(addr)
		if ip := net.ParseIP(host); err != nil || ip == nil || !ip.IsLoopback() {
			refused.Add(1)
			record, _ := json.Marshal(map[string]any{"ts": time.Now().UnixNano(), "pid": os.Getpid(), "process": "server.test", "host": host, "port": port})
			t.Logf("socket-refused %s", record)
			return nil, errCat2NonLoopback
		}
		dialed.Add(1)
		var d net.Dialer
		return d.DialContext(ctx, network, addr)
	}
}

// Separate positive-refusal invocation: exactly two reserved .invalid attempts.
func TestCAT2ModalitiesSocketGuard(t *testing.T) {
	var dialed, refused atomic.Int64
	dial := cat2ModalitiesDialer(t, &dialed, &refused)
	for _, addr := range []string{"one.invalid:443", "two.invalid:80"} {
		if c, err := dial(context.Background(), "tcp", addr); !errors.Is(err, errCat2NonLoopback) {
			if c != nil {
				c.Close()
			}
			t.Fatalf("guard did not refuse before connect: %v", err)
		}
	}
	s := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, _ *http.Request) { w.WriteHeader(204) }))
	defer s.Close()
	transport := &http.Transport{DialContext: dial}
	defer transport.CloseIdleConnections()
	response, err := (&http.Client{Transport: transport}).Get(s.URL)
	if err != nil {
		t.Fatal(err)
	}
	response.Body.Close()
	if response.StatusCode != 204 || dialed.Load() != 1 || refused.Load() != 2 {
		t.Fatalf("guard control: status=%d dialed=%d refused=%d", response.StatusCode, dialed.Load(), refused.Load())
	}
}

func cat2ModalitiesGateway(t *testing.T) (*managedTestGateway, *r601Owner, *atomic.Int64) {
	t.Helper()
	var dialed, refused atomic.Int64
	g := newManagedTestGatewayWith(t, managedOptIn(), managedGatewayOptions{dialContext: cat2ModalitiesDialer(t, &dialed, &refused)})
	g.srv.cfg.Managed.CatalogueFamilies = []string{"cat2"}
	if err := g.srv.handlers.catalog.Configure(catalog.OptionsFromConfig(g.srv.cfg, []catalog.Source{{ProviderID: "openrouter", Type: catalog.SourceOpenRouter}})); err != nil {
		t.Fatal(err)
	}
	_, install := installBundledCatalogue(t)
	install(t, g)
	owner := newR601Owner()
	g.ledger = creditsplugin.NewOrgLedger(owner)
	g.credits.SetOrgLedger(g.ledger, true)
	g.srv.managed.Ledger, g.srv.handlers.orgLedger = g.ledger, g.ledger
	g.ledger.SetMicros("org-m", 2000000000)
	t.Cleanup(func() {
		t.Logf("provider socket counters: loopback=%d refused=%d", dialed.Load(), refused.Load())
		if refused.Load() != 0 {
			t.Error("non-loopback attempt")
		}
	})
	return g, owner, &dialed
}

func TestCAT2ModalitiesImageSettlement(t *testing.T) {
	for _, stream := range []bool{false, true} {
		for _, tc := range []struct {
			name, cost string
			images     int
			spent      int64
		}{
			{"one", "0.000021", 1, 22},
			{"two", "0.000042", 2, 44},
			{"image-only-large", "0.000021", 1, 22},
			{"image-only-png-over-1MiB", "0.000021", 1, 22},
			{"missing-total", "", 1, 276060},
		} {
			t.Run(fmt.Sprintf("%s/stream=%t", tc.name, stream), func(t *testing.T) {
				g, owner, dialed := cat2ModalitiesGateway(t)
				defer releaseCat2Gateway(t, g)
				// A pending grant push is independent of the settlement outbox.
				// Seed it explicitly and require settlement to preserve it.
				if err := owner.MarkNeedsPush(context.Background(), "org-m"); err != nil {
					t.Fatal(err)
				}
				before := cat2ChargeLedgerOf(t, owner)
				images := make([]map[string]any, tc.images)
				for i := range images {
					payload := fmt.Sprintf("synthetic image %d", i)
					if tc.name == "image-only-large" {
						// Larger than the startup metadata budget, but valid output.
						payload = strings.Repeat(payload, maxStreamPrefixBytes/len(payload)+1)
					}
					data := base64.StdEncoding.EncodeToString([]byte(payload))
					if tc.name == "image-only-png-over-1MiB" {
						data = base64.StdEncoding.EncodeToString(cat2LargePNG(t))
						if len(data) <= 1024*1024 {
							t.Fatal("PNG event must exceed the former 1 MiB adapter limit")
						}
					}
					images[i] = map[string]any{"type": "image_url", "index": i, "image_url": map[string]string{"url": "data:image/png;base64," + data}, "fixture": "preserve metadata"}
				}
				text := "generated"
				if strings.HasPrefix(tc.name, "image-only-") {
					text = ""
				}
				answer := upstreamStream(func(w http.ResponseWriter, _ *http.Request, model string) {
					account, err := owner.Account(context.Background(), "org-m")
					// Independent bound from the bundled listing: 131072*2 +
					// 64*12 + 1*2 = 262914 upstream micros, then 5% half-up.
					if err != nil || account.HeldMicros != 276060 || account.SpentMicros != 0 {
						t.Errorf("at dispatch: account=%+v err=%v; want hold=276060", account, err)
					}
					message, _ := json.Marshal(map[string]any{"role": "assistant", "content": text, "images": images})
					usage := `{"prompt_tokens":1,"completion_tokens":1}`
					if tc.cost != "" {
						usage = `{"prompt_tokens":1,"completion_tokens":1,"cost":` + tc.cost + `}`
					}
					if stream {
						w.Header().Set("Content-Type", "text/event-stream")
						fmt.Fprintf(w, "data: {\"id\":\"gen-modalities\",\"model\":%q,\"choices\":[{\"index\":0,\"delta\":{\"role\":\"assistant\",\"content\":%q}}]}\n\n", model, text)
						for _, image := range images {
							delta, _ := json.Marshal(map[string]any{"images": []any{image}})
							fmt.Fprintf(w, "data: {\"id\":\"gen-modalities\",\"model\":%q,\"choices\":[{\"index\":0,\"delta\":%s}]}\n\n", model, delta)
						}
						fmt.Fprintf(w, "data: {\"id\":\"gen-modalities\",\"model\":%q,\"choices\":[],\"usage\":%s}\n\ndata: [DONE]\n\n", model, usage)
					} else {
						w.Header().Set("Content-Type", "application/json")
						fmt.Fprintf(w, `{"id":"gen-modalities","model":%q,"choices":[{"index":0,"message":%s,"finish_reason":"stop"}],"usage":%s}`, model, message, usage)
					}
				})
				g.managed.responseWith.Store(&answer)
				g.managed.streamWith.Store(&answer)
				body := fmt.Sprintf(`{"model":"google/gemini-3-pro-image","max_tokens":64,"stream":%t,"modalities":["image","text"],"messages":[{"role":"user","content":"draw"}]}`, stream)
				w := g.chat(t, body, nil)
				if w.Code != 200 {
					t.Fatalf("image modalities refused: status=%d body=%s", w.Code, w.Body)
				}
				cat2AssertReturnedImages(t, w.Body.String(), stream, images)
				settledAccount(t, g)
				if g.managed.calls() != 1 || g.operator.calls() != 0 || dialed.Load() == 0 {
					t.Fatalf("provider not reached exactly once: calls=%d dialed=%d", g.managed.calls(), dialed.Load())
				}
				g.managed.mu.Lock()
				sent := slices.Clone(g.managed.modalities[0])
				g.managed.mu.Unlock()
				if !slices.Equal(sent, []string{"image", "text"}) {
					t.Fatalf("modalities lost on rewrite: %v", sent)
				}
				after := cat2ChargeLedgerOf(t, owner)
				if after.account.SpentMicros != tc.spent || after.account.HeldMicros != 0 || after.account.GrantedMicros != before.account.GrantedMicros || after.account.GrantedVersion != before.account.GrantedVersion || len(after.entries) != 1 || after.pending != 1 || !after.needsPush || !after.outboxOrg {
					t.Fatalf("settlement ledger=%+v want spent=%d", after, tc.spent)
				}
				rec := after.entries[0]
				if rec["hold_micros"] != "276060" || rec["billed_micros"] != strconv.FormatInt(tc.spent, 10) || rec["kind"] != "spend" || rec["v"] != "1" {
					t.Fatalf("settlement record=%v", rec)
				}
				evidence := owner.OwnedSettlement("org-m", rec["request_id"])
				if evidence == nil {
					t.Fatal("missing durable settlement evidence")
				}
				var dimensions struct {
					Attempts []models.InclusiveAttempt `json:"inclusive_attempts"`
				}
				if err := json.Unmarshal([]byte(evidence.FieldMap()["dimensions"]), &dimensions); err != nil || len(dimensions.Attempts) != 1 {
					t.Fatalf("bad inclusive evidence: %+v err=%v", dimensions, err)
				}
				a := dimensions.Attempts[0]
				if a.HoldMicros != 276060 || a.BilledMicros != tc.spent || a.Estimated != (tc.cost == "") || a.ProviderTotal != tc.cost || a.GenerationID != "gen-modalities" || a.ServedModel != "google/gemini-3-pro-image" {
					t.Fatalf("inclusive evidence=%+v", a)
				}
			})
		}
	}
}

// Decode the public wire shape independently of models.Message/Delta, so a
// dropped field cannot disappear from both sides of the assertion.
func cat2AssertReturnedImages(t *testing.T, body string, stream bool, want []map[string]any) {
	t.Helper()
	var got []map[string]any
	read := func(data []byte) {
		var response struct {
			Choices []struct {
				Message struct {
					Images []map[string]any `json:"images"`
				} `json:"message"`
				Delta struct {
					Images []map[string]any `json:"images"`
				} `json:"delta"`
			} `json:"choices"`
		}
		if err := json.Unmarshal(data, &response); err != nil {
			t.Fatalf("decode image response: %v", err)
		}
		for _, choice := range response.Choices {
			if stream {
				got = append(got, choice.Delta.Images...)
			} else {
				got = append(got, choice.Message.Images...)
			}
		}
	}
	if stream {
		done := 0
		for _, line := range strings.Split(body, "\n") {
			if !strings.HasPrefix(line, "data: ") {
				continue
			}
			data := strings.TrimPrefix(line, "data: ")
			if data == "[DONE]" {
				done++
				continue
			}
			read([]byte(data))
		}
		if done != 1 {
			t.Errorf("stream completion count=%d; want 1", done)
		}
	} else {
		read([]byte(body))
	}
	if len(got) != len(want) {
		t.Errorf("returned image count=%d; want %d", len(got), len(want))
		return
	}
	for i := range want {
		a, _ := json.Marshal(got[i])
		b, _ := json.Marshal(want[i])
		if sha256.Sum256(a) != sha256.Sum256(b) {
			t.Errorf("returned image %d digest mismatch", i)
		}
		if sha256.Sum256(cat2DecodedImage(t, a)) != sha256.Sum256(cat2DecodedImage(t, b)) {
			t.Errorf("returned image %d decoded payload digest mismatch", i)
		}
	}
	t.Logf("image payloads checked: count=%d, per-image SHA-256 equal", len(got))
}

func TestCAT2ModalitiesRefusal(t *testing.T) {
	for _, tc := range []struct{ model, values string }{
		{"google/gemini-3-pro-image", `["audio"]`},
		{"google/gemini-3-pro-image", `["image","private-canary"]`},
		{"google/gemini-3-pro-image", `["IMAGE"]`},
		{"openai/o1-pro", `["image","text"]`},
		{"openai/o1-pro", `["text"]`},
		{"google/lyria-3-pro-preview", `["image"]`},
		{"google/gemini-3.5-flash", `["image"]`}, // input image price is not output permission
	} {
		t.Run(tc.model+tc.values, func(t *testing.T) {
			g, owner, _ := cat2ModalitiesGateway(t)
			defer releaseCat2Gateway(t, g)
			before := cat2ChargeLedgerOf(t, owner)
			w := g.chat(t, fmt.Sprintf(`{"model":%q,"max_tokens":64,"modalities":%s,"messages":[{"role":"user","content":"draw"}]}`, tc.model, tc.values), nil)
			var failure models.ErrorResponse
			if err := json.Unmarshal(w.Body.Bytes(), &failure); err != nil {
				t.Fatal(err)
			}
			if w.Code != 400 || failure.Error.Code != "unsupported_field_for_managed" || failure.Error.Param == nil || *failure.Error.Param != "modalities" {
				t.Fatalf("refusal: status=%d body=%s", w.Code, w.Body)
			}
			if strings.Contains(w.Body.String(), "private-canary") || strings.Contains(w.Body.String(), tc.values) {
				t.Fatal("refusal exposed modality values")
			}
			if g.managed.calls() != 0 || g.operator.calls() != 0 || !cat2ChargeLedgerOf(t, owner).equal(before) {
				t.Fatal("refusal did work or changed credits/ledger")
			}
		})
	}
}

func TestCAT2ModalitiesTokenAndBYOK(t *testing.T) {
	g, owner, dialed := cat2ModalitiesGateway(t)
	defer releaseCat2Gateway(t, g)
	w := g.chat(t, `{"model":"openai/o1-pro","max_tokens":64,"messages":[{"role":"user","content":"hi"}]}`, nil)
	if w.Code != 200 || g.managed.calls() != 1 || dialed.Load() == 0 {
		t.Fatalf("token request changed: %d %s", w.Code, w.Body)
	}
	before := cat2ChargeLedgerOf(t, owner)
	g.tenants.Set("org-m", &tenant.OrgConfig{Providers: map[string]*tenant.ProviderConfig{"openrouter": {APIKey: "synthetic-user-key", APIFormat: "openai", Models: []string{"google/gemini-3-pro-image"}, Enabled: true}}})
	w = g.chat(t, `{"model":"google/gemini-3-pro-image","modalities":["text","audio","private-canary"],"messages":[{"role":"user","content":"hi"}]}`, nil)
	if w.Code != 200 || w.Header().Get("x-agentcc-billing-mode") != "byok" || g.managed.calls() != 2 || g.operator.calls() != 0 || !cat2ChargeLedgerOf(t, owner).equal(before) {
		t.Fatalf("BYOK changed: %d %s", w.Code, w.Body)
	}
	g.managed.mu.Lock()
	defer g.managed.mu.Unlock()
	if !slices.Equal(g.managed.modalities[1], []string{"text", "audio", "private-canary"}) {
		t.Fatal("BYOK modalities changed")
	}
}
