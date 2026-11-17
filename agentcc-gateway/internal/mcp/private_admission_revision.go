package mcp

import "encoding/json"

// MembershipRevision is immutable opaque bytes, including non-UTF8 bytes and
// present-but-empty values. Its zero value alone means null. Never parse a MAC,
// fabricate a generation, or expose this binding to clients/logs.
type MembershipRevision struct {
	value   string
	present bool
}

func NewMembershipRevision(raw []byte) MembershipRevision {
	if raw == nil {
		return MembershipRevision{}
	}
	return MembershipRevision{value: string(raw), present: true}
}

// Bytes returns an independent copy for the future typed adapter only.
func (r MembershipRevision) Bytes() []byte {
	if !r.present {
		return nil
	}
	return []byte(r.value)
}

// JSON's byte encoding preserves opaque bytes across the private session store.
func (r MembershipRevision) MarshalJSON() ([]byte, error) { return json.Marshal(r.Bytes()) }
func (r *MembershipRevision) UnmarshalJSON(data []byte) error {
	var raw []byte
	if err := json.Unmarshal(data, &raw); err != nil {
		return errAdmissionUnavailable
	}
	*r = NewMembershipRevision(raw)
	return nil
}
