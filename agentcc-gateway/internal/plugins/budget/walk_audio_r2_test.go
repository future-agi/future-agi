package budget

import (
	"encoding/json"
	"net/http"
	"testing"

	"github.com/futureagi/agentcc-gateway/internal/models"
)

// Admission is bound directly here to exercise quotation without a Redis ledger.
// Reservation/dispatch integration remains covered by the Redis-backed cap suite.
func walkAudioCapContextR2(t *testing.T, managed bool) *models.RequestContext {
	t.Helper()
	rc := models.AcquireRequestContext()
	t.Cleanup(rc.Release)
	rc.Provider, rc.Model, rc.EndpointType = "synthetic", "text", "chat"
	if managed {
		rc.Metadata["billing_mode"] = models.BillingModeManaged
	}
	rc.Request = &models.ChatCompletionRequest{Model: "text", Messages: []models.Message{{Role: "user", Content: json.RawMessage(`"hello"`)}}}
	rc.Billing.EndpointSet = []models.EndpointRef{{Price: models.PriceCard{Request: 0.004, Source: models.PriceSourceCatalog}}}
	BindAdmission(rc, &Admission{org: "org", key: "key", revision: 1})
	return rc
}

func TestWalkStrictCapMessageAudioR2(t *testing.T) {
	for _, managed := range []bool{false, true} {
		for _, message := range []string{
			`{"role":"assistant","content":null,"audio":{"id":"audio_fixture"}}`,
			`{"role":"assistant","content":"previous answer","audio":{"id":"audio_fixture"}}`,
			`{"role":"assistant","content":"previous answer","audio":{}}`,
			`{"role":"assistant","content":"previous answer","images":[{"image_url":{"url":"data:image/png;base64,AA=="}}]}`,
		} {
			t.Run(map[bool]string{false: "byok/", true: "managed/"}[managed]+message, func(t *testing.T) {
				rc := walkAudioCapContextR2(t, managed)
				var msg models.Message
				if err := json.Unmarshal([]byte(message), &msg); err != nil {
					t.Fatal(err)
				}
				rc.Request.Messages = append(rc.Request.Messages, msg)
				err := QualifyCapChat(rc, nil, 500, 256)
				if err == nil || err.Status != http.StatusPaymentRequired || err.Code != "call_cap_unenforceable" {
					t.Fatalf("unpriced message media qualified: %v", err)
				}
				if RequestAdmission(rc).quoteReady {
					t.Fatal("unpriced message media acquired a quote")
				}
			})
		}
	}
}

func TestWalkStrictCapLateAudioAndTextControlR2(t *testing.T) {
	for _, managed := range []bool{false, true} {
		rc := walkAudioCapContextR2(t, managed)
		if err := QualifyCapChat(rc, nil, 500, 256); err != nil {
			t.Fatalf("text-only control refused: %v", err)
		}
		if !RequestAdmission(rc).quoteReady {
			t.Fatal("text-only control has no quote")
		}
		if err := RecheckCapChat(rc); err != nil {
			t.Fatalf("unchanged text refused: %v", err)
		}
		rc.Request.Messages[0].Audio = &models.MessageAudio{ID: "audio_fixture"}
		for _, recheck := range []func(*models.RequestContext) *models.APIError{RecheckCapChat, RecheckForwardedCapChat} {
			if err := recheck(rc); err == nil || err.Code != "call_cap_unenforceable" {
				t.Fatalf("late audio addition accepted: %v", err)
			}
		}
	}
}
