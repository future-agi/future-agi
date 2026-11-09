package protocol

import (
	"encoding/json"
	"errors"
	"time"
)

// Sealer seals and opens the opaque state the gateway hands clients; M1x's
// mcp.StateCodec is the one in use (authenticated encryption, per-purpose
// keys, an expiry inside the token).
type Sealer interface {
	Seal(purpose string, payload []byte, ttl time.Duration) (string, error)
	Open(purpose, token string) ([]byte, error)
}

// CursorPurpose is the state purpose list cursors are sealed under (M1x's
// mcp.StatePurposeCursor).
const CursorPurpose = "cursor"

// CursorTTL is how long a list cursor stays valid (MCA C28, I8).
const CursorTTL = 10 * time.Minute

// ErrInvalidCursor is a cursor that is malformed, altered, expired, or sealed
// for another method or caller. One error for all, so a client learns
// nothing about why.
var ErrInvalidCursor = errors.New("invalid cursor")

// Cursor is the position a sealed list cursor names inside the gateway's
// merged list. Clients see only the sealed token.
type Cursor struct {
	Method string `json:"m"`
	Offset int    `json:"o"`
	// Binding names who may continue the listing (a hash of the principal
	// and virtual server), so a cursor does not travel between callers.
	Binding string `json:"b"`
}

// SealCursor seals c for CursorTTL.
func SealCursor(s Sealer, c Cursor) (string, error) {
	payload, err := json.Marshal(c)
	if err != nil {
		return "", err
	}
	return s.Seal(CursorPurpose, payload, CursorTTL)
}

// OpenCursor opens a cursor a client sent back with a list request: it must
// be authentic, unexpired, and sealed for this method and binding.
func OpenCursor(s Sealer, token, method, binding string) (Cursor, error) {
	payload, err := s.Open(CursorPurpose, token)
	if err != nil {
		return Cursor{}, ErrInvalidCursor
	}
	var c Cursor
	if json.Unmarshal(payload, &c) != nil || c.Method != method || c.Binding != binding || c.Offset < 0 {
		return Cursor{}, ErrInvalidCursor
	}
	return c, nil
}

// Page returns the end of the page of a list of n items that starts at
// offset, and whether items remain after it. size <= 0 means one page.
func Page(n, offset, size int) (end int, more bool) {
	if offset > n {
		offset = n
	}
	if size <= 0 || n-offset <= size {
		return n, false
	}
	return offset + size, true
}
