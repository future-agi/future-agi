package ext

import (
	"bytes"
	"encoding/json"
	"errors"
	"fmt"
	"io"
	"math"
)

// keySettings are a key's writable fields, as POST and PATCH /api/v1/keys take them.
type keySettings struct {
	Name        string
	Disabled    bool
	Limit       *float64 // USD; nil: no limit
	LimitReset  *string  // "daily", "weekly" or "monthly"; nil: the limit never resets
	IncludeBYOK bool
}

var nullJSON = []byte("null")

// apply decodes a create (POST) or update (PATCH) body over s: each field the
// body sends replaces s's, and limit or limit_reset sent as null clears it.
// A create must name the key. On an error s is unchanged.
func (s *keySettings) apply(body io.Reader, create bool) error {
	var in struct {
		Name        *string         `json:"name"`
		Disabled    *bool           `json:"disabled"`
		Limit       json.RawMessage `json:"limit"`
		LimitReset  json.RawMessage `json:"limit_reset"`
		IncludeBYOK *bool           `json:"include_byok_in_limit"`
	}
	if err := json.NewDecoder(body).Decode(&in); err != nil {
		return fmt.Errorf("the body must be a JSON object of key fields: %v", err)
	}
	next := *s
	switch {
	case in.Name != nil && *in.Name == "":
		return errors.New("name must not be empty")
	case in.Name != nil:
		next.Name = *in.Name
	case create:
		return errors.New("name is required")
	}
	if in.Disabled != nil {
		next.Disabled = *in.Disabled
	}
	if in.IncludeBYOK != nil {
		next.IncludeBYOK = *in.IncludeBYOK
	}
	if in.Limit != nil {
		limit, err := decodeLimit(in.Limit)
		if err != nil {
			return err
		}
		next.Limit = limit
	}
	if in.LimitReset != nil {
		reset, err := decodeLimitReset(in.LimitReset)
		if err != nil {
			return err
		}
		next.LimitReset = reset
	}
	*s = next
	return nil
}

func decodeLimit(raw json.RawMessage) (*float64, error) {
	if bytes.Equal(bytes.TrimSpace(raw), nullJSON) {
		return nil, nil
	}
	var limit float64
	if err := json.Unmarshal(raw, &limit); err != nil || limit < 0 || math.IsInf(limit, 0) {
		return nil, fmt.Errorf("limit must be a non-negative USD amount or null, got %s", raw)
	}
	return &limit, nil
}

func decodeLimitReset(raw json.RawMessage) (*string, error) {
	if bytes.Equal(bytes.TrimSpace(raw), nullJSON) {
		return nil, nil
	}
	var reset string
	if err := json.Unmarshal(raw, &reset); err != nil || (reset != "daily" && reset != "weekly" && reset != "monthly") {
		return nil, fmt.Errorf(`limit_reset must be "daily", "weekly", "monthly" or null, got %s`, raw)
	}
	return &reset, nil
}
