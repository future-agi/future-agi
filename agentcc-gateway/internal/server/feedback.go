package server

import (
	"encoding/json"
	"errors"
	"io"
	"net/http"
	"sync"

	"github.com/futureagi/agentcc-gateway/internal/models"
	"github.com/futureagi/agentcc-gateway/internal/routing/outcomes"
	"github.com/futureagi/agentcc-gateway/internal/tenant"
)

// maxFeedbackBody bounds a POST /v1/feedback body.
const maxFeedbackBody = 4 << 10

const (
	errGenerationNotFound = "generation_not_found"
	errInvalidFeedback    = "invalid_feedback"
)

// outcomeStores holds each server's outcome store. The route is registered
// when the server is built, and the store only exists once the wiring runs
// (SetOutcomeStore), so the handler looks it up per call. Keyed by server so
// servers built side by side (tests) never share one.
var outcomeStores sync.Map // *Server -> *outcomes.Store

// SetOutcomeStore installs the store POST /v1/feedback attributes verdicts
// through: the outcomes plugin's, which remembers agent-tagged and managed
// generations for an hour (outcomes.LastTTL).
func (s *Server) SetOutcomeStore(st *outcomes.Store) { outcomeStores.Store(s, st) }

func (s *Server) outcomeStore() *outcomes.Store {
	v, _ := outcomeStores.Load(s)
	st, _ := v.(*outcomes.Store)
	return st
}

// feedbackRequest is the body of POST /v1/feedback.
type feedbackRequest struct {
	GenerationID string   `json:"generation_id"`
	Outcome      string   `json:"outcome"`
	Score        *float64 `json:"score,omitempty"`
}

type feedbackResponse struct {
	Data feedbackRecorded `json:"data"`
}

type feedbackRecorded struct {
	GenerationID string `json:"generation_id"`
	Outcome      string `json:"outcome"`
	EventID      string `json:"event_id"`
}

// handleFeedback serves POST /v1/feedback {generation_id, outcome: ok | bad
// | verify_failed, score?}: the caller's verdict on one of its org's
// generations (the x-agentcc-request-id of the call). bad and verify_failed
// count against the generation's model in outcome routing, once per
// generation; every call emits one outcome event to the control plane. An
// unknown generation, another org's, one older than an hour, or one the
// gateway did not keep (an untagged BYOK request) is 404
// generation_not_found. The request and response shapes and these errors are
// a frozen contract (feedback_contract_test.go): change them
// only additively.
func (s *Server) handleFeedback(w http.ResponseWriter, r *http.Request) {
	req, apiErr := decodeFeedback(w, r)
	if apiErr != nil {
		models.WriteError(w, apiErr)
		return
	}
	org := s.feedbackOrg(r)
	st := s.outcomeStore()
	if org == "" || st == nil {
		models.WriteError(w, generationNotFound())
		return
	}
	ctx := r.Context()
	last, ok := st.Last(ctx, org, req.GenerationID)
	if !ok {
		models.WriteError(w, generationNotFound())
		return
	}
	if set := s.feedbackOrgConfig(org).AutoOutcomes(); set.Enabled {
		st.Judge(ctx, org, req.GenerationID, last, req.Outcome, outcomes.HalfLife(set.WindowSeconds))
	}
	ev := outcomes.NewEvent(org, req.GenerationID, req.Outcome, outcomes.SourceFeedback, req.Score, last, st.Now())
	s.handlers.Deps().Events(models.EventKindOutcome, ev)
	writeJSON(w, http.StatusOK, feedbackResponse{Data: feedbackRecorded{GenerationID: req.GenerationID, Outcome: req.Outcome, EventID: ev.EventID}})
}

// feedbackOrgConfig is org's stored config, nil when there is none.
func (s *Server) feedbackOrgConfig(org string) *tenant.OrgConfig {
	if s.TenantStore == nil {
		return nil
	}
	return s.TenantStore.Get(org)
}

// feedbackOrg is the org of the virtual key the request presented, "" when
// it presented none the gateway knows.
func (s *Server) feedbackOrg(r *http.Request) string {
	rc := models.AcquireRequestContext()
	defer rc.Release()
	setAuthMetadataFromRequest(rc, r)
	if key := s.handlers.lookupRequestKey(rc); key != nil {
		return key.Metadata["org_id"]
	}
	return ""
}

func decodeFeedback(w http.ResponseWriter, r *http.Request) (feedbackRequest, *models.APIError) {
	var req feedbackRequest
	dec := json.NewDecoder(http.MaxBytesReader(w, r.Body, maxFeedbackBody))
	dec.DisallowUnknownFields()
	if err := dec.Decode(&req); err != nil {
		var tooLarge *http.MaxBytesError
		if errors.As(err, &tooLarge) {
			return req, invalidFeedback("", "the feedback body is larger than 4 KB")
		}
		return req, invalidFeedback("", "the feedback body must be a JSON object {generation_id, outcome, score?}: "+err.Error())
	}
	if err := dec.Decode(&struct{}{}); !errors.Is(err, io.EOF) {
		return req, invalidFeedback("", "the feedback body must hold one JSON object")
	}
	if req.GenerationID == "" || len(req.GenerationID) > 256 {
		return req, invalidFeedback("generation_id", "generation_id is required: the x-agentcc-request-id of the call")
	}
	switch req.Outcome {
	case outcomes.VerdictOK, outcomes.VerdictBad, outcomes.VerdictVerifyFailed:
	default:
		return req, invalidFeedback("outcome", "outcome must be one of ok, bad, verify_failed")
	}
	return req, nil
}

func invalidFeedback(param, msg string) *models.APIError {
	e := models.ErrBadRequest(errInvalidFeedback, msg)
	if param != "" {
		e.Param = &param
	}
	return e
}

func generationNotFound() *models.APIError {
	return models.ErrNotFound(errGenerationNotFound, "no generation with this id belongs to your organization")
}
