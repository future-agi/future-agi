package protocol

import (
	"bytes"
	"encoding/json"
	"errors"
	"io"
)

var errNotJSON = errors.New("not a JSON value")

// Strings returns every string of a JSON value in document order: member
// names and values, at any depth, each duplicate and case variant included.
// A guard that checks them all has checked everything a client's JSON parser
// can read from those bytes, whichever duplicate it keeps and however it
// matches names. It fails on bytes that are not one JSON value.
func Strings(raw json.RawMessage) ([]string, error) {
	if !json.Valid(raw) {
		return nil, errNotJSON // the decoder alone reads a truncated value as EOF
	}
	dec := json.NewDecoder(bytes.NewReader(raw))
	dec.UseNumber()
	var out []string
	for {
		tok, err := dec.Token()
		if err == io.EOF {
			return out, nil
		}
		if err != nil {
			return nil, err
		}
		if s, ok := tok.(string); ok {
			out = append(out, s)
		}
	}
}
