package billing

import (
	"context"
	"encoding/json"
)

// ReadOwnedJournal returns detached durable evidence, including attempts no
// longer in the public request log. Memory implements the Redis contract for
// tests; it is not an alternative to persistence in shipped profiles.
func (l *MemoryLedger) ReadOwnedJournal(_ context.Context, org, request string) (OwnedRequest, error) {
	l.mu.Lock()
	defer l.mu.Unlock()
	owned := l.org(org).owned[request]
	if owned == nil {
		return OwnedRequest{}, ErrOwnership
	}
	data, err := json.Marshal(owned)
	if err != nil {
		return OwnedRequest{}, err
	}
	var out OwnedRequest
	if err = json.Unmarshal(data, &out); err != nil {
		return OwnedRequest{}, err
	}
	// Memory retains Record directly; Redis retains it in record_json.
	data, err = json.Marshal(owned.Record)
	if err != nil {
		return OwnedRequest{}, err
	}
	err = json.Unmarshal(data, &out.Record)
	return out, err
}
