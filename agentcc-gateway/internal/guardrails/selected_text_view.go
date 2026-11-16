package guardrails

import (
	"context"
	"crypto/sha256"
	"encoding/json"
	"fmt"
	"reflect"
	"strings"

	"github.com/futureagi/agentcc-gateway/internal/models"
)

// These built-in checks consume JSON strings, unlike evaluators and custom
// checks which consume the original structured request. Adapt only their
// selected-policy invocation; org/static execution and the shared leaf instances
// deliberately retain their legacy input contract.
func usesSelectedTextView(g Guardrail) bool {
	// Leaves import this package, so importing their concrete types here would
	// create a cycle. Exact package/type identity confines this compatibility
	// adapter to these installed implementations, never a custom override or
	// an unavailable/invalid-config wrapper with the same registry name.
	typ := reflect.TypeOf(g)
	if typ == nil || typ.Kind() != reflect.Pointer {
		return false
	}
	typ = typ.Elem()
	const base = "github.com/futureagi/agentcc-gateway/internal/guardrails/"
	switch typ.PkgPath() + "." + typ.Name() {
	case base + "topic.TopicGuardrail", base + "language.LanguageGuardrail",
		base + "sysprompt.SystemPromptGuardrail", base + "leakage.LeakageGuardrail",
		base + "hallucination.HallucinationGuardrail", base + "validation.ValidationGuardrail":
		return true
	}
	return false
}

// Selected inspection projections must not change the legacy view consumed by
// org/static guardrails. They are immutable projections, never wire remediation.
func selectedCheckInput(rc *models.RequestContext, withResponse bool) *CheckInput {
	input := checkInput(rc, withResponse)
	input.InspectionOnly = rc.GuardrailInspectionOnly || rc.GuardrailRequest != nil || rc.Request == nil || (withResponse && rc.GuardrailResponse != nil)
	if rc.SelectedGuardrailRequest != nil {
		input.Request = rc.SelectedGuardrailRequest
		input.InspectionOnly = true
	} else if rc.GuardrailRequest != nil {
		// Match inspectionPayload's projection precedence, not the legacy
		// carrier-first working view inherited from checkInput.
		input.Request = rc.GuardrailRequest
	}
	if withResponse && rc.SelectedGuardrailResponse != nil {
		input.Response = rc.SelectedGuardrailResponse
		input.InspectionOnly = true
	}
	return input
}

// Capture native inspection content before legacy org/static remediators update
// their working projection. Those rewrites never change the native wire body.
// Copy even an explicit selected view: its parameters may alias the legacy view.
// Rebind existing completeness evidence to the owned copy, but never create it
// for an incomplete, stale or unsupported payload.
func preserveSelectedProjection(rc *models.RequestContext, stage Stage) {
	input := selectedCheckInput(rc, stage == StagePost)
	if !input.InspectionOnly {
		return
	}
	payload := inspectionPayload(rc, stage)
	complete := rc.Inspection.IsComplete(inspectionStage(stage), payload)
	snapshot := &CheckInput{}
	if stage == StagePost {
		snapshot.Response = input.Response
	} else {
		snapshot.Request = input.Request
	}
	owned, err := cloneCheckInput(snapshot)
	if err != nil {
		if stage == StagePost {
			rc.GuardrailOutputUnsupported = "native output inspection snapshot is unavailable"
		} else {
			rc.GuardrailInputUnsupported = "native input inspection snapshot is unavailable"
		}
		return
	}
	if stage == StagePost {
		rc.SelectedGuardrailResponse = owned.Response
	} else {
		rc.SelectedGuardrailRequest = owned.Request
	}
	if complete {
		rc.Inspection.Complete(inspectionStage(stage), inspectionPayload(rc, stage))
	}
}

type selectedTextCheck struct{ Guardrail }

func (g selectedTextCheck) Check(ctx context.Context, input *CheckInput) *CheckResult {
	view, err := selectedTextView(input, g.Stage())
	if err != nil {
		return &CheckResult{Err: err}
	}
	return g.Guardrail.Check(ctx, view)
}

// selectedTextView owns every byte it changes. It never replaces Request,
// GuardrailRequest, Response, or GuardrailResponse on the request context. The
// usual checker snapshot isolation also applies to parallel and timed-out work.
// Only content is projected: roles, names, evaluator/user/context fields, choice
// indices and usage survive unchanged. Non-text shapes are never partially read.
func selectedTextView(input *CheckInput, stage Stage) (*CheckInput, error) {
	view, err := cloneCheckInput(input)
	if err != nil {
		return nil, err
	}
	if stage == StagePre && view.Request != nil {
		for i := range view.Request.Messages {
			content, err := selectedTextContent(view.Request.Messages[i].Content)
			if err != nil {
				return nil, err
			}
			view.Request.Messages[i].Content = content
		}
	}
	if stage == StagePost && view.Response != nil {
		for i := range view.Response.Choices {
			content, err := selectedTextContent(view.Response.Choices[i].Message.Content)
			if err != nil {
				return nil, err
			}
			view.Response.Choices[i].Message.Content = content
		}
	}
	view.InspectionOnly = true
	return view, nil
}

func selectedTextContent(raw json.RawMessage) (json.RawMessage, error) {
	var text string
	if strings.HasPrefix(strings.TrimSpace(string(raw)), `"`) && json.Unmarshal(raw, &text) == nil {
		return raw, nil
	}
	var parts []map[string]json.RawMessage
	if json.Unmarshal(raw, &parts) != nil || parts == nil {
		return nil, fmt.Errorf("selected text view requires complete text content")
	}
	texts := make([]string, 0, len(parts))
	for _, part := range parts {
		var kind string
		if len(part) != 2 || json.Unmarshal(part["type"], &kind) != nil || kind != "text" || !strings.HasPrefix(strings.TrimSpace(string(part["text"])), `"`) || json.Unmarshal(part["text"], &text) != nil {
			return nil, fmt.Errorf("selected text view requires exclusively text parts")
		}
		texts = append(texts, text)
	}
	return json.Marshal(strings.Join(texts, "\n"))
}

// The existing receipt continues to retain and fingerprint the ORIGINAL payload
// generation, operation and authenticated principal. Its revision additionally
// binds the deterministic checked projection (including a format version), not
// merely the policy config. No normalized copy can certify different wire data.
// Recompute after successful wire-backed remediation, just as Complete is
// refreshed there; inspection-only rewrites never alter either source or view.
func selectedTextPolicyRevision(resolved *resolvedKeyPolicies, rc *models.RequestContext, stage Stage) string {
	revision := selectedPolicyRevision(resolved)
	if revision == "" {
		return ""
	}
	needed := false
	for _, entry := range resolved.policies {
		for _, c := range entry.checks {
			if selectedCheckStage(rc, c.g) == stage && usesSelectedTextView(c.g) {
				needed = true
			}
		}
	}
	if !needed {
		return revision
	}
	view, err := selectedTextView(selectedCheckInput(rc, stage == StagePost), stage)
	if err != nil {
		return ""
	}
	var payload interface{} = view.Request
	if stage == StagePost {
		payload = view.Response
	}
	body, err := json.Marshal(struct {
		Version string
		Policy  []byte
		Checked interface{}
	}{"selected-text-v1", []byte(revision), payload})
	if err != nil {
		return ""
	}
	digest := sha256.Sum256(body)
	return string(digest[:])
}
