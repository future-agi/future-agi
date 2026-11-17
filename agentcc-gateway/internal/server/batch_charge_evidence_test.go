package server

import (
	"context"
	"encoding/json"
	"errors"
	"net/http"
	"testing"
	"time"

	"github.com/futureagi/agentcc-gateway/internal/batch"
	"github.com/futureagi/agentcc-gateway/internal/models"
	costplugin "github.com/futureagi/agentcc-gateway/internal/plugins/cost"
)

// Exercise finalized attempt accounting, the success/error HTTP boundary, the
// real batch adapter, processor and JSON projection, not success-only metadata.
func TestBatchFinalChargeEvidence(t *testing.T) {
	for _, tc := range []struct {
		name                            string
		items                           []string
		unknown, failed, billableFailed int
		total                           float64
	}{
		{"unpriced primary zero fallback", []string{"partial-zero"}, 1, 0, 0, 0},
		{"unpriced primary positive fallback", []string{"partial-positive"}, 1, 0, 0, 0},
		{"usage bearing unpriced error", []string{"unpriced-error"}, 1, 1, 1, 0},
		{"priced billable error", []string{"priced-error"}, 0, 1, 1, .125},
		{"mixed success error", []string{"priced", "unpriced-error"}, 1, 1, 1, 0},
		{"mixed priced success error", []string{"priced", "priced-error"}, 0, 1, 1, .25},
		{"all cache", []string{"cache", "cache"}, 0, 0, 0, 0},
		{"mixed cache priced", []string{"cache", "priced"}, 0, 0, 0, .125},
		{"trusted predispatch refusal", []string{"refused"}, 0, 1, 0, 0},
		// The ledger bills a dispatched attempt without usage nothing, but no
		// evidence says its provider charged nothing (a 200 cut short, say).
		{"dispatched failure without charge evidence", []string{"nonbillable-error"}, 1, 1, 0, 0},
		{"never dispatched failure", []string{"skipped-error"}, 0, 1, 0, 0},
		{"measured zero failure", []string{"measured-zero-error"}, 0, 1, 0, 0},
		// A measured-zero answer is complete although the log's compatibility
		// cost_source calls the mixed request unpriced.
		{"priced failure measured zero fallback", []string{"zero-fallback"}, 0, 0, 0, .125},
		{"unsettled dispatch", []string{"unsettled"}, 1, 1, 0, 0},
	} {
		t.Run(tc.name, func(t *testing.T) {
			h := &Handlers{}
			cost := costplugin.New(true, testModelDBPtr().Load, nil, nil)
			handler := http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
				var request models.ChatCompletionRequest
				if err := json.NewDecoder(r.Body).Decode(&request); err != nil {
					t.Error(err)
					return
				}
				rc := models.AcquireRequestContext()
				defer rc.Release()
				rc.RequestID = "batch-charge-item"
				rc.Model, rc.Provider = "unknown-charge-model", "synthetic"
				rc.Request = &models.ChatCompletionRequest{Model: rc.Model}
				rc.Metadata["billing_mode"] = models.BillingModeBYOK
				kind := request.Model
				failed := kind == "unpriced-error" || kind == "priced-error" || kind == "nonbillable-error" || kind == "skipped-error" || kind == "measured-zero-error" || kind == "refused" || kind == "unsettled"
				zero := 0
				if kind == "cache" {
					rc.Flags.CacheHit = true
				} else if kind != "refused" {
					usage := &models.Usage{PromptTokens: 10, CompletionTokens: 5}
					if kind == "priced-error" || kind == "zero-fallback" {
						amount := .125
						usage.Cost = &amount
					}
					outcome := models.OutcomeError
					switch kind {
					case "nonbillable-error":
						usage = nil
					case "skipped-error":
						usage, outcome = nil, models.OutcomeSkipped
					case "measured-zero-error":
						usage = models.NewUsage(&zero, &zero)
					}
					if failed || kind == "partial-zero" || kind == "partial-positive" || kind == "zero-fallback" {
						rc.AddAttempt(models.Attempt{Provider: rc.Provider, Model: rc.Model, CredentialType: models.CredentialBYOK, Outcome: outcome, Usage: usage, InFlight: kind == "unsettled"})
					}
					if !failed {
						rc.AddAttempt(models.Attempt{Provider: rc.Provider, Model: rc.Model, CredentialType: models.CredentialBYOK, Outcome: models.OutcomeSuccess})
					}
				}
				if !failed {
					amount := .125
					if kind == "partial-zero" || kind == "cache" {
						amount = 0
					}
					rc.Response = &models.ChatCompletionResponse{Model: rc.Model, Usage: &models.Usage{PromptTokens: 10, CompletionTokens: 5, Cost: &amount}}
					if kind == "zero-fallback" {
						// Explicitly measured zero tokens and no reported cost
						// on a model with no price.
						rc.Response.Usage = models.NewUsage(&zero, &zero)
					}
				}
				if kind != "unsettled" && kind != "refused" {
					cost.FinalizeRequest(rc)
				}
				if failed {
					h.failChat(w, rc, errors.New("synthetic failure"))
				} else {
					h.writeChatResponse(w, rc, true, nil)
				}
			})
			p := batch.NewProcessor(batch.RunAsChatRequest(func() http.Handler { return handler }))
			defer p.Stop()
			requests := make([]*models.ChatCompletionRequest, len(tc.items))
			for i, item := range tc.items {
				requests[i] = &models.ChatCompletionRequest{Model: item}
			}
			b := p.Submit(requests, 1)
			deadline := time.Now().Add(5 * time.Second)
			for b.GetCompletedAt() == nil && time.Now().Before(deadline) {
				time.Sleep(time.Millisecond)
			}
			if b.GetCompletedAt() == nil {
				t.Fatal("batch did not finish")
			}
			data, err := json.Marshal(batchGetResponse(b))
			if err != nil {
				t.Fatal(err)
			}
			var got struct {
				Summary struct {
					Cost           *float64 `json:"total_cost"`
					Known          bool     `json:"cost_known"`
					Unpriced       int      `json:"unpriced_requests"`
					Failed         int      `json:"failed"`
					BillableFailed int      `json:"billable_failed_requests"`
				} `json:"summary"`
				Results []struct {
					Cost  *float64 `json:"cost"`
					Known bool     `json:"cost_known"`
				} `json:"results"`
			}
			if err := json.Unmarshal(data, &got); err != nil {
				t.Fatal(err)
			}
			if got.Summary.Unpriced != tc.unknown || got.Summary.Failed != tc.failed || got.Summary.BillableFailed != tc.billableFailed {
				t.Errorf("wrong counts: %s", data)
			}
			if tc.unknown > 0 {
				if got.Summary.Known || got.Summary.Cost != nil {
					t.Errorf("incomplete total must be null: %s", data)
				}
			} else if !got.Summary.Known || got.Summary.Cost == nil || *got.Summary.Cost != tc.total {
				t.Errorf("complete charge lost: %s", data)
			}
			for i, item := range tc.items {
				unknown := item == "partial-zero" || item == "partial-positive" || item == "unpriced-error" || item == "nonbillable-error" || item == "unsettled"
				if got.Results[i].Known == unknown || (got.Results[i].Cost == nil) != unknown {
					t.Errorf("item %s amount/completeness: %s", item, data)
				}
			}
		})
	}
}

func TestBatchCancelledChargePending(t *testing.T) {
	started, release := make(chan struct{}), make(chan struct{})
	p := batch.NewProcessor(func(ctx context.Context, _ *models.ChatCompletionRequest) (*models.ChatCompletionResponse, map[string]string, error) {
		close(started)
		<-release
		return nil, nil, ctx.Err()
	})
	defer p.Stop()
	b := p.Submit([]*models.ChatCompletionRequest{{Model: "one"}, {Model: "two"}}, 1)
	<-started
	p.Cancel(b.ID)
	data, err := json.Marshal(batchGetResponse(b))
	if err != nil {
		t.Fatal(err)
	}
	var snapshot struct {
		Summary struct {
			Cost *float64 `json:"total_cost"`
		} `json:"summary"`
	}
	if err := json.Unmarshal(data, &snapshot); err != nil {
		t.Fatal(err)
	}
	close(release)
	if snapshot.Summary.Cost != nil {
		t.Errorf("cancelled in-flight amount must not be final: %s", data)
	}
	deadline := time.Now().Add(5 * time.Second)
	for b.GetCompletedAt() == nil && time.Now().Before(deadline) {
		time.Sleep(time.Millisecond)
	}
	if b.GetCompletedAt() == nil {
		t.Fatal("cancelled batch did not settle")
	}
	for _, result := range b.GetResults() {
		if result.Status == "cancelled" && (!result.CostKnown || result.Cost != 0) {
			t.Errorf("queued cancellation must be known zero: %+v", result)
		}
	}
}
