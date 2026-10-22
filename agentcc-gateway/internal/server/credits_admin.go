package server

import (
	"encoding/json"
	"errors"
	"net/http"

	"github.com/futureagi/agentcc-gateway/internal/billing"
	"github.com/futureagi/agentcc-gateway/internal/models"
	creditsplugin "github.com/futureagi/agentcc-gateway/internal/plugins/credits"
)

// The org's managed-credit hot-path account, for the control plane. Django
// is the system of record: it pushes the granted total
// (credits-granted-push-v1) and reads the account back (credits-v1).

// creditsAdminView is the admin GET's body (credits-v1 Admin). balance_usd
// and markup_percent are what Django's managed-models status view reads.
type creditsAdminView struct {
	// OrgID is the org the path names.
	OrgID              string  `json:"org_id"`
	GrantedTotalMicros int64   `json:"granted_total_micros"`
	GrantedVersion     int64   `json:"granted_version"`
	SpentMicros        int64   `json:"spent_micros"`
	HeldMicros         int64   `json:"held_micros"`
	AvailableMicros    int64   `json:"available_micros"`
	BalanceUSD         float64 `json:"balance_usd"`
	MarkupPercent      float64 `json:"markup_percent"`
	OutboxPending      int64   `json:"outbox_pending"`
	Ledger             string  `json:"ledger"`
}

// grantedPushView is the granted push's response (credits-granted-push-v1).
type grantedPushView struct {
	Applied            bool  `json:"applied"`
	GrantedTotalMicros int64 `json:"granted_total_micros"`
	GrantedVersion     int64 `json:"granted_version"`
	SpentMicros        int64 `json:"spent_micros"`
	HeldMicros         int64 `json:"held_micros"`
	AvailableMicros    int64 `json:"available_micros"`
}

// maxGrantedPushBody bounds a granted push's body.
const maxGrantedPushBody = 4 << 10

// creditsOrgID is the admin route's org id, or "" after writing the 400 for
// a missing one. It returns "" after the 401 for a bad admin token too.
func (s *Server) creditsOrgID(w http.ResponseWriter, r *http.Request) string {
	if !s.checkAdminAuth(w, r) {
		return ""
	}
	orgID := r.URL.Query().Get("org_id")
	if orgID == "" {
		models.WriteError(w, models.ErrBadRequest("missing_org_id", "org_id is required"))
	}
	return orgID
}

// writeLedgerError writes a ledger failure: 503 managed_billing_unavailable
// when the store is unreachable, else 400 for an org id the store refuses.
func writeLedgerError(w http.ResponseWriter, err error) {
	if errors.Is(err, billing.ErrUnavailable) {
		models.WriteErrorFromError(w, creditsplugin.ErrBillingUnavailable())
		return
	}
	models.WriteError(w, models.ErrBadRequest("invalid_org_id", err.Error()))
}

// handleOrgCredits serves GET /-/orgs/{org_id}/credits: the org's account,
// its undrained outbox and the ledger behind it. An org never pushed reads
// as zeros.
func (s *Server) handleOrgCredits(w http.ResponseWriter, r *http.Request) {
	orgID := s.creditsOrgID(w, r)
	if orgID == "" {
		return
	}
	ledger := s.managed.Ledger
	acct, err := ledger.Account(r.Context(), orgID)
	if err != nil {
		writeLedgerError(w, err)
		return
	}
	pending, err := ledger.OutboxPending(r.Context(), orgID)
	if err != nil {
		writeLedgerError(w, err)
		return
	}
	available := acct.AvailableMicros()
	writeJSON(w, http.StatusOK, creditsAdminView{
		OrgID:              orgID,
		GrantedTotalMicros: acct.GrantedMicros,
		GrantedVersion:     acct.GrantedVersion,
		SpentMicros:        acct.SpentMicros,
		HeldMicros:         acct.HeldMicros,
		AvailableMicros:    available,
		BalanceUSD:         billing.MicrosToUSD(available),
		MarkupPercent:      s.managed.MarkupPercent,
		OutboxPending:      pending,
		Ledger:             ledger.Kind(),
	})
}

// handleOrgCreditsGranted serves PUT /-/orgs/{org_id}/credits/granted
// (credits-granted-push-v1): granted is applied only when the version is
// newer than the stored one, and spent is raised to the settled-spend floor
// always. Every field is required and non-negative.
func (s *Server) handleOrgCreditsGranted(w http.ResponseWriter, r *http.Request) {
	orgID := s.creditsOrgID(w, r)
	if orgID == "" {
		return
	}
	var req struct {
		GrantedTotalMicros      *int64 `json:"granted_total_micros"`
		Version                 *int64 `json:"version"`
		SettledSpendFloorMicros *int64 `json:"settled_spend_floor_micros"`
	}
	dec := json.NewDecoder(http.MaxBytesReader(w, r.Body, maxGrantedPushBody))
	dec.DisallowUnknownFields()
	if err := dec.Decode(&req); err != nil || req.GrantedTotalMicros == nil || req.Version == nil ||
		req.SettledSpendFloorMicros == nil || *req.GrantedTotalMicros < 0 || *req.Version < 0 || *req.SettledSpendFloorMicros < 0 {
		models.WriteError(w, models.ErrBadRequest("invalid_body",
			`body must be {"granted_total_micros": <int >= 0>, "version": <int >= 0>, "settled_spend_floor_micros": <int >= 0>}`))
		return
	}
	res, err := s.managed.Ledger.Grant(r.Context(), orgID, billing.GrantPush{
		GrantedTotalMicros:      *req.GrantedTotalMicros,
		Version:                 *req.Version,
		SettledSpendFloorMicros: *req.SettledSpendFloorMicros,
	})
	if err != nil {
		writeLedgerError(w, err)
		return
	}
	a := res.Account
	writeJSON(w, http.StatusOK, grantedPushView{
		Applied:            res.Applied,
		GrantedTotalMicros: a.GrantedMicros,
		GrantedVersion:     a.GrantedVersion,
		SpentMicros:        a.SpentMicros,
		HeldMicros:         a.HeldMicros,
		AvailableMicros:    a.AvailableMicros(),
	})
}

// handleRetiredOrgCredits answers POST and PUT /-/orgs/{org_id}/credits with
// 410 endpoint_retired: an org's balance is set only by the versioned
// granted push.
func (s *Server) handleRetiredOrgCredits(w http.ResponseWriter, r *http.Request) {
	if s.creditsOrgID(w, r) == "" {
		return
	}
	models.WriteError(w, &models.APIError{
		Status:  http.StatusGone,
		Type:    models.ErrTypeInvalidRequest,
		Code:    "endpoint_retired",
		Message: "this endpoint is retired: push the org's granted total with PUT /-/orgs/{org_id}/credits/granted (credits-granted-push-v1)",
	})
}
