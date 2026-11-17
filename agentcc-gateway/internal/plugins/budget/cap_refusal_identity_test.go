package budget

import (
	"context"
	"encoding/json"
	"net/http/httptest"
	"reflect"
	"testing"

	"github.com/futureagi/agentcc-gateway/internal/models"
)

func TestCapRefusalRunOnlyMissingStore(t *testing.T) {
	p := New(nil, nil, false, nil)
	rc := capMoneyRequest(t, "org-a", "key-a", false)
	a := RequestAdmission(rc)
	a.agentLimit, a.keyLimit, a.runLimit = -1, -1, 5000
	err := p.reserveCaps(context.Background(), rc)
	if err == nil || err.Status != 503 {
		t.Fatalf("expected unavailable: %v", err)
	}
	w := httptest.NewRecorder()
	models.WriteError(w, err)
	var body struct {
		Error struct {
			Metadata map[string]any `json:"metadata"`
		} `json:"error"`
	}
	if err := json.Unmarshal(w.Body.Bytes(), &body); err != nil {
		t.Fatal(err)
	}
	want := map[string]any{"cap_scope": "agent", "cap_ref": "worker", "cap_kind": "run", "policy_revision": float64(1)}
	if !reflect.DeepEqual(body.Error.Metadata, want) {
		t.Fatalf("identity=%#v want=%#v", body.Error.Metadata, want)
	}
}

func TestCapRefusalIdentity(t *testing.T) {
	for _, scope := range []string{"agent", "key"} {
		for _, failure := range []string{"exhausted", "uncertain", "store_missing", "activation_missing", "stale"} {
			t.Run(scope+"/"+failure, func(t *testing.T) {
				p := capMoneyPlugin(t)
				rc := capMoneyRequest(t, "org-a", "key-a", false)
				a := RequestAdmission(rc)
				a.revision = 7
				ref := "worker"
				if scope == "key" {
					a.keyLimit, a.keyActivation = a.agentLimit, a.agentActivation
					a.agentLimit = -1
					ref = "key-a"
				}
				// Caller metadata and friendly names are never authoritative identity.
				rc.Metadata["agent_id"], rc.Metadata["auth_key_name"] = "spoofed", "renamed"
				rc.Metadata["cap_scope"], rc.Metadata["cap_ref"] = "key", "spoofed"
				switch failure {
				case "exhausted":
					if scope == "agent" {
						a.agentLimit = 1
					} else {
						a.keyLimit = 1
					}
				case "store_missing":
					p.SetCapStore(nil)
				case "activation_missing":
					if scope == "agent" {
						a.agentActivation = ""
					} else {
						a.keyActivation = ""
					}
				case "uncertain", "stale":
					if err := p.reserveCaps(context.Background(), rc); err != nil {
						t.Fatal(err)
					}
					if failure == "uncertain" {
						if !a.ClaimAttempt() {
							t.Fatal("attempt refused")
						}
						p.finishCaps(rc) // No authoritative usage: fence, never invent zero.
					} else {
						p.finishCaps(rc)
					}
					next := capMoneyRequest(t, "org-a", "key-a", false)
					b := RequestAdmission(next)
					b.revision = 7
					if failure == "stale" {
						b.revision = 6
					}
					if scope == "key" {
						b.keyLimit, b.keyActivation, b.agentLimit = a.keyLimit, a.keyActivation, -1
					}
					rc, a = next, b
				}
				err := p.reserveCaps(context.Background(), rc)
				if err == nil {
					t.Fatal("expected refusal")
				}
				status, code := 503, "budget_state_unavailable"
				if failure == "exhausted" {
					status, code = 429, "org_budget_exceeded"
				}
				if err.Status != status || err.Code != code {
					t.Fatalf("unexpected refusal: %+v", err)
				}
				w := httptest.NewRecorder()
				models.WriteError(w, err)
				var body struct {
					Error map[string]any `json:"error"`
				}
				if err := json.Unmarshal(w.Body.Bytes(), &body); err != nil {
					t.Fatal(err)
				}
				want := map[string]any{"cap_scope": scope, "cap_ref": ref, "cap_kind": "daily", "policy_revision": float64(a.revision)}
				if !reflect.DeepEqual(body.Error["metadata"], want) {
					t.Fatalf("identity = %#v, want %#v", body.Error["metadata"], want)
				}
				// A strict allowlist pins the entire wire body: no monetary numbers,
				// hidden extra fields, or numbers interpolated into the message.
				for k, v := range body.Error {
					switch k {
					case "metadata", "type", "code", "param":
					case "message":
						for _, r := range v.(string) {
							if r >= '0' && r <= '9' {
								t.Fatalf("numeric message: %s", v)
							}
						}
					default:
						t.Fatalf("extra refusal field %s", k)
					}
				}
				if len(w.Header()) != 1 {
					t.Fatalf("unexpected refusal headers: %v", w.Header())
				}
			})
		}
	}
}
