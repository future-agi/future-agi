package protocol

import (
	"errors"
	"testing"
	"time"
)

// plainSealer is a Sealer without cryptography, enough to test the cursor
// rules (the mcp package tests the real codec).
type plainSealer struct{ expired bool }

func (p plainSealer) Seal(purpose string, payload []byte, _ time.Duration) (string, error) {
	return purpose + "|" + string(payload), nil
}

func (p plainSealer) Open(purpose, token string) ([]byte, error) {
	if p.expired || len(token) <= len(purpose)+1 || token[:len(purpose)+1] != purpose+"|" {
		return nil, errors.New("no")
	}
	return []byte(token[len(purpose)+1:]), nil
}

func TestCursorRules(t *testing.T) {
	s := plainSealer{}
	tok, err := SealCursor(s, Cursor{Method: "tools/list", Offset: 5, Binding: "b1"})
	if err != nil {
		t.Fatal(err)
	}
	if c, err := OpenCursor(s, tok, "tools/list", "b1"); err != nil || c.Offset != 5 {
		t.Fatalf("open = %+v, %v", c, err)
	}
	for name, open := range map[string]func() error{
		"other method":  func() error { _, err := OpenCursor(s, tok, "prompts/list", "b1"); return err },
		"other binding": func() error { _, err := OpenCursor(s, tok, "tools/list", "b2"); return err },
		"expired":       func() error { _, err := OpenCursor(plainSealer{expired: true}, tok, "tools/list", "b1"); return err },
	} {
		if err := open(); !errors.Is(err, ErrInvalidCursor) {
			t.Errorf("%s: %v", name, err)
		}
	}
	for _, tc := range []struct {
		n, off, size, end int
		more              bool
	}{
		{10, 0, 3, 3, true}, {10, 9, 3, 10, false}, {10, 12, 3, 10, false}, {10, 0, 0, 10, false}, {3, 0, 3, 3, false},
	} {
		if end, more := Page(tc.n, tc.off, tc.size); end != tc.end || more != tc.more {
			t.Errorf("Page(%d, %d, %d) = %d %v", tc.n, tc.off, tc.size, end, more)
		}
	}
	if Binding("a", "b") == Binding("ab", "") {
		t.Error("binding parts run together")
	}
}
