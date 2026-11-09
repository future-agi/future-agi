package capability

import (
	"context"
	"encoding/json"
	"errors"
	"fmt"
	"io"
	"net/http"
	"net/http/httptest"
	"strings"
	"testing"

	"github.com/futureagi/agentcc-gateway/internal/config"
	"github.com/futureagi/agentcc-gateway/internal/routing"
)

// The canary is the credential actually sent over loopback, not an unrelated
// marker added only to an error. No real upstream or credential is used.
func TestOptBCapabilityCanary(t *testing.T) {
	for _, owner := range []string{"platform", "byok", "native"} {
		for _, status := range []int{401, 403, 429, 400, 503, 0} {
			t.Run(fmt.Sprintf("%s/%d", owner, status), func(t *testing.T) {
				const key = "optb-outbound-capability-fixture"
				sent := make(chan string, 1)
				upstream := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
					sent <- r.Header.Get("X-Api-Key")
					_, _ = io.WriteString(w, "opaque-upstream-value "+r.Header.Get("X-Api-Key"))
				}))
				defer upstream.Close()
				var original error
				adapter := AdapterFunc(func(ctx context.Context, call VendorCall) (VendorResponse, error) {
					req, err := http.NewRequestWithContext(ctx, "POST", call.Credential.BaseURL, nil)
					if err != nil {
						return VendorResponse{}, err
					}
					req.Header.Set("X-Api-Key", call.Credential.APIKey)
					resp, err := upstream.Client().Do(req)
					if err != nil {
						return VendorResponse{}, err
					}
					defer resp.Body.Close()
					raw, err := io.ReadAll(resp.Body)
					if err != nil {
						return VendorResponse{}, err
					}
					message := string(raw) + " https://fixture.invalid/path X-Api-Key"
					original = &VendorError{Status: status, Message: message}
					if status == 0 {
						original = errors.New(message)
					}
					return VendorResponse{}, original
				})
				c := card("search", "fixture", 0.001)
				if owner == "native" {
					c.ResaleStatus = ResaleNative
				}
				tc := newTestCore(t, coreSetup{capability: "search", cards: []*Card{c}, managed: []string{"fixture"}, adapters: map[string]Adapter{"fixture": adapter}})
				tc.creds = ConfigCredentials{Operator: map[string]config.ProviderConfig{"fixture": {Managed: true, APIKey: key, BaseURL: upstream.URL}}}
				grant(t, tc.ledger, testOrg, 1_000_000)
				org := orgConfig("search", []string{"fixture"}, true)
				if owner == "byok" {
					org = withBYOK(org, "fixture")
					org.Providers["fixture"].APIKey = key
				}
				rc, _, err := tc.call(t, "req_optb_capability", "search", Request{Org: org})
				if err == nil {
					t.Fatal("fixture did not fail")
				}
				select {
				case got := <-sent:
					if got != key {
						t.Fatal("outbound canary was not used")
					}
				default:
					t.Fatal("fixture did not dispatch")
				}
				Publish(rc)
				attempts := rc.AttemptsSnapshot()
				if len(attempts) != 1 {
					t.Fatalf("attempt count=%d", len(attempts))
				}
				a := attempts[0]
				if a.StatusCode != status {
					t.Errorf("status changed: %d", a.StatusCode)
				}
				if owner == "byok" {
					if a.ErrorMessage != routing.AttemptErrorMessage(original) {
						t.Error("BYOK diagnostic differs from deployed projection")
					}
					if a.BilledCostMicros != 0 {
						t.Error("BYOK was billed")
					}
					return
				}
				raw, _ := json.Marshal(attempts)
				for _, forbidden := range []string{key, "opaque-upstream-value", "https://fixture.invalid", "X-Api-Key"} {
					if strings.Contains(string(raw), forbidden) {
						t.Error("C4: platform outbound value reached exported capability attempt")
					}
				}
				category := map[int]string{401: "authentication_error", 403: "permission_error", 429: "rate_limit_error", 400: "invalid_request_error", 503: "upstream_unavailable", 0: "upstream_unavailable"}[status]
				if !strings.Contains(a.ErrorMessage, category) || !strings.Contains(a.ErrorMessage, a.AttemptID) || !strings.Contains(a.ErrorMessage, "correlation id") {
					t.Error("hidden attempt lacks category, correlation id or action")
				}
				if !strings.Contains(original.Error(), key) {
					t.Error("operator error was mutated")
				}
			})
		}
	}
}
