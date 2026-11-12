package budget

import (
	"context"
	"encoding/json"
	"reflect"
	"testing"

	"github.com/futureagi/agentcc-gateway/internal/models"
)

func capDetailsUsage(t *testing.T) *models.Usage {
	t.Helper()
	var u models.Usage
	if err := json.Unmarshal([]byte(`{"prompt_tokens":20,"completion_tokens":10,"total_tokens":30,"prompt_tokens_details":{ "cached_tokens":3, "opaque":7 },"completion_tokens_details":{"reasoning_tokens":2,"opaque":9},"opaque_usage":{"n":1}}`), &u); err != nil {
		t.Fatal(err)
	}
	return &u
}

func capDetailsAttempt(t *testing.T, stream bool) (*models.RequestContext, *nativeControl, NativeAttempt, *int) {
	t.Helper()
	rc, c, h, calls := capNativeAttemptFixture(t, stream)
	finish := c.attempt.finish
	c.attempt.finish = func(u *models.Usage, g models.GenerationEvidence, failure *models.APIError) {
		// Match attemptRun.finish's ParseDetails-before-recording behavior without
		// importing server or claiming a real transport/provider qualification.
		if u != nil {
			u.ParseDetails()
		}
		finish(u, g, failure)
	}
	return rc, c, h, calls
}

func capDetailsEnd(t *testing.T, h NativeAttempt, u *models.Usage) {
	t.Helper()
	e := capNativeEvent(h, 20, true)
	e.Usage = u
	if err := ObserveNative(h, e); err != nil {
		t.Fatal(err.Code)
	}
	if err := EndNative(h, NativeCompleted); err != nil {
		t.Fatal(err.Code)
	}
}

func TestNativeC1DetailsFinishSettlement(t *testing.T) {
	for _, mode := range []string{"sync-unparsed", "sync-parsed", "stream-unparsed", "stream-parsed"} {
		for _, details := range []string{"prompt", "completion", "both"} {
			t.Run(mode+"/"+details, func(t *testing.T) {
				rc, c, h, calls := capDetailsAttempt(t, mode == "stream-unparsed" || mode == "stream-parsed")
				u := capDetailsUsage(t)
				if details == "prompt" {
					u.CompletionTokensDetails = nil
				} else if details == "completion" {
					u.PromptTokensDetails = nil
				}
				if mode == "sync-parsed" || mode == "stream-parsed" {
					u.ParseDetails()
				}
				original := nativeCopyUsage(u)
				capDetailsEnd(t, h, u)
				if !reflect.DeepEqual(u, original) {
					t.Fatal("finish mutated caller evidence")
				}
				if _, known := c.admission.ActualSpend(rc); known {
					t.Fatal("settled before billing")
				}
				sealed := nativeCopyUsage(c.attempt.usage)
				at := rc.AttemptsSnapshot()[0].Usage
				recorded := nativeCopyUsage(at)
				for i := 0; i < 2; i++ {
					if r := (&Plugin{}).FinishNativeCaps(context.Background(), rc); r.State != NativeSettled || r.Error != nil {
						t.Fatal("valid finish details fenced", r.State)
					}
					if amount, known := c.admission.ActualSpend(rc); !known || amount != 10 {
						t.Fatal("lost priced terminal", amount, known)
					}
					if err := EndNative(h, NativeCompleted); err != nil {
						t.Fatal(err.Code)
					}
				}
				if *calls != 1 || !reflect.DeepEqual(c.attempt.usage, sealed) || !reflect.DeepEqual(at, recorded) || rc.AttemptsSnapshot()[0].Usage != at {
					t.Fatal("duplicate finish or settlement mutated retained evidence")
				}
			})
		}
	}
}

func TestNativeC1DetailsValidity(t *testing.T) {
	for _, field := range []string{"prompt", "completion"} {
		for _, tc := range []struct {
			name, raw string
			sidecar   int
			valid     bool
		}{
			{"missing", "", 0, true}, {"missing-sidecar", "", 3, true},
			{"empty-object", "{}", 0, true}, {"unknown-object", `{"opaque":7}`, 0, true},
			{"unparsed", "3", 0, true}, {"parsed", "3", 3, true},
			{"mismatch", "3", 4, false}, {"negative-sidecar", "3", -1, false},
			{"empty-raw", "", 0, false},
			{"zero-raw-mismatch", "0", 3, false}, {"empty-object-mismatch", "{}", 3, false},
			{"negative-raw", "-1", 0, false}, {"unsafe-integer", "9007199254740992", 0, false},
			{"wrong-counter-type", `"3"`, 0, false},
			{"null", "null", 0, false}, {"array", "[]", 0, false},
			{"scalar", "true", 0, false}, {"malformed", "{", 0, false},
		} {
			t.Run(field+"/"+tc.name, func(t *testing.T) {
				rc, c, h, _ := capDetailsAttempt(t, false)
				u := models.MeasuredUsage(20, 10)
				key := "cached_tokens"
				if field == "completion" {
					key = "reasoning_tokens"
				}
				var raw *json.RawMessage
				if tc.raw != "" || tc.name == "empty-raw" {
					s := tc.raw
					if s != "" && s != "null" && s != "[]" && s != "true" && s[0] != '{' {
						s = `{"` + key + `":` + s + `}`
					}
					r := json.RawMessage(s)
					raw = &r
				}
				if field == "prompt" {
					u.PromptTokensDetails, u.PromptDetails.CachedTokens = raw, tc.sidecar
				} else {
					u.CompletionTokensDetails, u.CompletionDetails.ReasoningTokens = raw, tc.sidecar
				}
				before := nativeCopyUsage(u)
				if nativeUsageValid(u) != tc.valid || !reflect.DeepEqual(u, before) {
					t.Fatal("invalid evidence accepted, valid evidence refused, or caller mutated")
				}
				e := capNativeEvent(h, 20, true)
				e.Usage = u
				if err := ObserveNative(h, e); (err == nil) != tc.valid {
					t.Fatal("observation validity mismatch")
				}
				_ = EndNative(h, NativeCompleted)
				r := (&Plugin{}).FinishNativeCaps(context.Background(), rc)
				if (r.State == NativeSettled) != tc.valid {
					t.Fatal("settlement validity mismatch", r.State)
				}
				if _, known := c.admission.ActualSpend(rc); known != tc.valid {
					t.Fatal("actual validity mismatch")
				}
			})
		}
	}
}

func TestNativeC1DetailsDetachedPresence(t *testing.T) {
	for _, payload := range []string{`{}`, `{"prompt_tokens":0}`, `{"prompt_tokens":0,"completion_tokens":0}`, `{"prompt_tokens":null,"completion_tokens":0,"prompt_tokens_details":null}`} {
		var u models.Usage
		if err := json.Unmarshal([]byte(payload), &u); err != nil {
			t.Fatal(err)
		}
		v := nativeCopyUsage(&u)
		if !reflect.DeepEqual(v, &u) || v.HasPromptTokens() != u.HasPromptTokens() || v.HasCompletionTokens() != u.HasCompletionTokens() || !nativeUsageValid(v) {
			t.Fatal("decoded presence or absent/null raw changed")
		}
	}
	rc, c, h, _ := capDetailsAttempt(t, false)
	u := capDetailsUsage(t)
	before := nativeCopyUsage(u)
	e := capNativeEvent(h, 20, true)
	e.Usage = u
	if err := ObserveNative(h, e); err != nil {
		t.Fatal(err.Code)
	}
	(*u.PromptTokensDetails)[0] = '['
	(*u.CompletionTokensDetails)[0] = '['
	u.Extra["opaque_usage"][0] = '['
	if !reflect.DeepEqual(c.attempt.usage, before) || !reflect.DeepEqual(c.attempt.last.Usage, before) {
		t.Fatal("caller owns retained raw bytes")
	}
	if err := EndNative(h, NativeCompleted); err != nil {
		t.Fatal(err.Code)
	}
	if r := (&Plugin{}).FinishNativeCaps(context.Background(), rc); r.State != NativeSettled {
		t.Fatal("detached copy lost valid actual", r.State)
	}
	at := rc.AttemptsSnapshot()[0].Usage
	(*at.PromptTokensDetails)[0] = '['
	(*at.CompletionTokensDetails)[0] = '['
	at.Extra["opaque_usage"][0] = '['
	if !reflect.DeepEqual(c.attempt.usage, before) || !reflect.DeepEqual(c.attempt.last.Usage, before) {
		t.Fatal("finish owns retained raw bytes")
	}
	if _, known := c.admission.ActualSpend(rc); known {
		t.Fatal("mutated finish evidence accepted")
	}
}

func TestNativeC1DetailsEvidenceRefusals(t *testing.T) {
	for _, mutation := range []string{"raw-bytes", "raw-unknown", "raw-counter", "raw-missing", "sidecar-zero", "sidecar-positive", "reasoning-zero", "extra", "presence"} {
		t.Run(mutation, func(t *testing.T) {
			rc, c, h, _ := capDetailsAttempt(t, false)
			u := capDetailsUsage(t)
			u.ParseDetails() // This control must already pass settlement on the base.
			capDetailsEnd(t, h, u)
			id := rc.AttemptsSnapshot()[0].AttemptID
			rc.UpdateAttempt(id, func(a *models.Attempt) {
				switch mutation {
				case "raw-bytes":
					*a.Usage.PromptTokensDetails = json.RawMessage(`{"cached_tokens":3,"opaque":7}`)
				case "raw-unknown":
					*a.Usage.PromptTokensDetails = json.RawMessage(`{ "cached_tokens":3, "opaque":8 }`)
				case "raw-counter":
					*a.Usage.CompletionTokensDetails = json.RawMessage(`{"reasoning_tokens":3,"opaque":9}`)
					a.Usage.ParseDetails()
				case "raw-missing":
					a.Usage.CompletionTokensDetails = nil
				case "sidecar-zero":
					a.Usage.PromptDetails = models.PromptTokensDetails{}
				case "sidecar-positive":
					a.Usage.PromptDetails.CachedTokens++
				case "reasoning-zero":
					a.Usage.CompletionDetails = models.CompletionTokensDetails{}
				case "extra":
					a.Usage.Extra["opaque_usage"] = json.RawMessage(`{"n":2}`)
				case "presence":
					v := models.MeasuredUsage(20, 10)
					v.TotalTokens, v.PromptTokensDetails, v.CompletionTokensDetails = a.Usage.TotalTokens, a.Usage.PromptTokensDetails, a.Usage.CompletionTokensDetails
					v.PromptDetails, v.CompletionDetails, v.Extra = a.Usage.PromptDetails, a.Usage.CompletionDetails, a.Usage.Extra
					a.Usage = v // Same public values, different decoded provenance.
				}
			})
			if r := (&Plugin{}).FinishNativeCaps(context.Background(), rc); r.State != NativeFenced {
				t.Fatal("changed evidence settled", r.State)
			}
			if _, known := c.admission.ActualSpend(rc); known {
				t.Fatal("changed evidence priced")
			}
		})
	}
}

func TestNativeC1DetailsReplay(t *testing.T) {
	for _, variant := range []string{"same", "same-parsed", "parsed-replay", "raw-bytes", "raw-unknown", "generation", "financial-id", "sequence-gap"} {
		t.Run(variant, func(t *testing.T) {
			rc, c, h, _ := capDetailsAttempt(t, true)
			e := capNativeEvent(h, 20, false)
			e.Usage = capDetailsUsage(t)
			if variant == "same-parsed" {
				e.Usage.ParseDetails()
			}
			if err := ObserveNative(h, e); err != nil {
				t.Fatal(err.Code)
			}
			switch variant {
			case "parsed-replay":
				e.Usage.ParseDetails()
			case "raw-bytes":
				*e.Usage.PromptTokensDetails = json.RawMessage(`{"cached_tokens":3,"opaque":7}`)
			case "raw-unknown":
				*e.Usage.CompletionTokensDetails = json.RawMessage(`{"reasoning_tokens":2,"opaque":8}`)
			case "generation":
				e.Generation.ID = "different-generation"
			case "financial-id":
				e.FinancialID = "different-attempt"
			case "sequence-gap":
				e.Sequence = 3
			}
			valid := variant == "same" || variant == "same-parsed"
			if err := ObserveNative(h, e); (err == nil) != valid {
				t.Fatal("replay guard changed")
			}
			e.Sequence, e.Complete = 2, true
			_ = ObserveNative(h, e)
			_ = EndNative(h, NativeCompleted)
			r := (&Plugin{}).FinishNativeCaps(context.Background(), rc)
			if (r.State == NativeSettled) != valid {
				t.Fatal("replay settlement mismatch", r.State)
			}
			if amount, known := c.admission.ActualSpend(rc); known != valid || (known && amount != 10) {
				t.Fatal("replayed counters changed actual")
			}
		})
	}
}

func TestNativeC1DetailsSettlementGuards(t *testing.T) {
	for _, guard := range []string{"at-quote", "zero", "over-quote", "negative", "cache", "billing-estimated", "attempt-estimated", "usage-estimated", "cost-source", "generation", "financial-id", "in-flight", "not-ended", "managed-over-quote"} {
		t.Run(guard, func(t *testing.T) {
			rc, c, h, _ := capDetailsAttempt(t, false)
			u := capDetailsUsage(t)
			u.ParseDetails()
			capDetailsEnd(t, h, u)
			want := int64(10)
			rc.UpdateAttempt(rc.AttemptsSnapshot()[0].AttemptID, func(a *models.Attempt) {
				switch guard {
				case "at-quote":
					a.UpstreamCostMicros, want = 100, 100
				case "zero":
					a.UpstreamCostMicros, want = 0, 0
				case "over-quote":
					a.UpstreamCostMicros = 101
				case "negative":
					a.UpstreamCostMicros = -1
				case "cache":
					rc.Flags.CacheHit = true
				case "billing-estimated":
					rc.Billing.UsageEstimated = true
				case "attempt-estimated":
					a.UsageEstimated = true
				case "usage-estimated":
					a.Usage.Estimated = true
				case "cost-source":
					a.CostSource = ""
				case "generation":
					a.Generation.ID = "different-generation"
				case "financial-id":
					a.FinancialID = "different-attempt"
				case "in-flight":
					a.InFlight = true
				case "not-ended":
					c.attempt.ended = false
				case "managed-over-quote":
					c.admission.managed, a.BilledCostMicros = true, 101
				}
			})
			valid := guard == "at-quote" || guard == "zero"
			r := (&Plugin{}).FinishNativeCaps(context.Background(), rc)
			if (r.State == NativeSettled) != valid {
				t.Fatal("actual guard changed", r.State)
			}
			if amount, known := c.admission.ActualSpend(rc); known != valid || (known && amount != want) {
				t.Fatal("actual amount/guard changed", amount, known)
			}
		})
	}
}
