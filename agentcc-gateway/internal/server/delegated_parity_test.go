package server

import (
	"crypto/rand"
	"crypto/rsa"
	"crypto/x509"
	"encoding/json"
	"encoding/pem"
	"fmt"
	"net/http"
	"strings"
	"testing"
	"time"

	"github.com/futureagi/agentcc-gateway/internal/batch"
	"github.com/futureagi/agentcc-gateway/internal/config"
	"github.com/futureagi/agentcc-gateway/internal/tenant"
)

func TestDelegated733TimeoutAndLicenseParity(t *testing.T) {
	private, err := rsa.GenerateKey(rand.Reader, 2048)
	if err != nil {
		t.Fatal(err)
	}
	der, err := x509.MarshalPKIXPublicKey(&private.PublicKey)
	if err != nil {
		t.Fatal(err)
	}
	public := string(pem.EncodeToMemory(&pem.Block{Type: "PUBLIC KEY", Bytes: der}))
	for _, licensed := range []bool{false, true} {
		for _, timeout := range []time.Duration{45 * time.Second, 120 * time.Second} {
			for _, mode := range []string{"sync", "sse", "batch"} {
				t.Run(fmt.Sprintf("license=%v/%s/%s", licensed, timeout, mode), func(t *testing.T) {
					srv := newDelegated733Server(t, func(cfg *config.Config) {
						cfg.LicenseAuth = config.LicenseAuthConfig{Enabled: licensed, PublicKey: public}
						cfg.Routing.ModelTimeouts = map[string]time.Duration{"gpt-4o-mini": 90 * time.Second}
						cfg.Server.MaxRequestTimeout = 3 * time.Minute
						p := cfg.Providers["openai"]
						p.DefaultTimeout = 3 * time.Minute
						cfg.Providers["openai"] = p
					})
					readyEpoch733(srv)
					org := srv.TenantStore.Get(delegated733Org)
					org.Routing = &tenant.RoutingConfig{ModelTimeouts: map[string]string{"gpt-4o-mini": timeout.String()}}
					srv.TenantStore.Set(delegated733Org, org)
					want := fmt.Sprint(timeout.Milliseconds())
					if mode != "batch" {
						body := delegated733Body
						if mode == "sse" {
							body = strings.TrimSuffix(body, "}") + `,"stream":true}`
						}
						env, grant, _ := signed733(delegated733Route, body, "playground", time.Now().Unix())
						w := request733(srv, delegated733Route, body, env, grant)
						if w.Code != 200 || w.Header().Get("x-agentcc-timeout-ms") != want {
							t.Fatalf("status=%d timeout=%q want=%s body=%s", w.Code, w.Header().Get("x-agentcc-timeout-ms"), want, w.Body.String())
						}
						if mode == "sse" && (!w.Flushed || !strings.Contains(w.Body.String(), "[DONE]")) {
							t.Fatal("SSE did not stream")
						}
					} else {
						prepareBatch733(t, srv)
						// Observe the actual item's regular HTTP chain, not a replacement
						// handler or a duplicated timeout calculation.
						original := srv.httpServer.Handler
						observed := make(chan string, 1)
						srv.httpServer.Handler = http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
							original.ServeHTTP(w, r)
							if r.URL.Path == batch.ChatPath {
								observed <- w.Header().Get("x-agentcc-timeout-ms")
							}
						})
						body := `{"requests":[` + delegated733Body + `],"max_concurrency":1}`
						env, grant, _ := signed733("/-/batches", body, "batch", time.Now().Unix())
						w := request733(srv, "/-/batches", body, env, grant)
						if w.Code != 202 {
							t.Fatalf("batch admission: %d %s", w.Code, w.Body.String())
						}
						var submitted struct {
							ID string `json:"batch_id"`
						}
						if err := json.Unmarshal(w.Body.Bytes(), &submitted); err != nil {
							t.Fatal(err)
						}
						b, ok := srv.batchProcessor.GetForOrg(submitted.ID, delegated733Org)
						if !ok {
							t.Fatal("batch missing")
						}
						for deadline := time.Now().Add(5 * time.Second); b.GetCompletedAt() == nil; time.Sleep(time.Millisecond) {
							if time.Now().After(deadline) {
								t.Fatal("batch did not finish")
							}
						}
						if results := b.GetResults(); len(results) != 1 || results[0].Status != "success" {
							t.Fatalf("batch failed: %+v", results)
						}
						select {
						case got := <-observed:
							if got != want {
								t.Fatalf("item timeout=%s want=%s", got, want)
							}
						default:
							t.Fatal("item did not traverse HTTP chain")
						}
					}
					if len(srv.keyStore.List()) != 0 {
						t.Fatal("delegation minted a key")
					}
				})
			}
		}
	}
}
