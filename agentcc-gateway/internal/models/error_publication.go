package models

import "encoding/json"

// MarkClientPublished records that attribution, projection and redaction have
// finished. This is not an origin grant. Only the client publication boundary
// calls it; provider-controlled metadata cannot impersonate this marker.
func (e *APIError) MarkClientPublished() {
	if e == nil {
		return
	}
	e.clientPublication = ""
	if raw, err := json.Marshal(e); err == nil {
		e.clientPublication = string(raw)
	}
}

// IsClientPublished permits a second attribution pass to preserve pointer and
// wrapper identity, but never permits changed text/metadata to skip publication.
func (e *APIError) IsClientPublished() bool {
	if e == nil || e.clientPublication == "" {
		return false
	}
	raw, err := json.Marshal(e)
	return err == nil && string(raw) == e.clientPublication
}
