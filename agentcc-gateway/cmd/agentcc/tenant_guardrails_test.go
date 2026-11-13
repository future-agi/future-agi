package main

import (
	"context"
	"encoding/json"
	"strconv"
	"strings"
	"testing"
	"time"

	"github.com/futureagi/agentcc-gateway/internal/config"
	"github.com/futureagi/agentcc-gateway/internal/guardrails"
	"github.com/futureagi/agentcc-gateway/internal/models"
)

func TestTenantGuardrailEgress(t *testing.T) {
	for _, allowPrivate := range []bool{false, true} {
		if allowPrivate {
			t.Setenv(config.EnvAllowPrivateProviderURLs, "true")
		} else {
			t.Setenv(config.EnvAllowPrivateProviderURLs, "false")
		}
		gatewayCfg, err := config.Load("")
		if err != nil {
			t.Fatal(err)
		}
		for _, host := range []string{"127.0.0.1", "10.1.2.3", "169.254.169.254", "localhost", "[::1]"} {
			// FutureAGI checks are not egress-guarded yet; see futureagi.NewTenant.
			for _, kind := range []string{"webhook", "external", "crowdstrike"} {
				t.Run(kind+"/"+host+"/private="+strconv.FormatBool(allowPrivate), func(t *testing.T) {
					url := "http://" + host + ":80"
					var cfg map[string]interface{}
					switch kind {
					case "webhook":
						cfg = map[string]interface{}{"url": url}
					case "external":
						cfg = map[string]interface{}{"provider": "lakera", "api_key": "tenant", "endpoint": url}
					case "crowdstrike":
						cfg = map[string]interface{}{"provider": "crowdstrike", "client_id": "tenant", "client_secret": "tenant-secret", "base_url": url}
					}
					g := newTenantGuardrail("tenant-check", cfg, gatewayCfg.OrgProviders.AllowPrivateURLs)
					if g == nil {
						t.Fatal("tenant guardrail was not constructed")
					}
					ctx, cancel := context.WithTimeout(context.Background(), time.Second)
					defer cancel()
					result := g.Check(ctx, &guardrails.CheckInput{
						Request: &models.ChatCompletionRequest{Messages: []models.Message{
							{Role: "user", Content: json.RawMessage(`"hello"`)},
						}},
					})
					if result.Pass {
						t.Fatalf("outbound failure returned a pass: %+v", result)
					}
					wantBlocked := host != "10.1.2.3" || !allowPrivate
					if blocked := strings.Contains(result.Message, "destination address is not allowed"); blocked != wantBlocked {
						t.Fatalf("allowPrivate=%v, result=%+v, want egress refusal=%v", allowPrivate, result, wantBlocked)
					}
				})
			}
		}
	}
}
