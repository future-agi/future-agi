package wire

import (
	"context"
	"encoding/json"
)

// List caps: a listing stops after this many pages or items.
const (
	MaxPages = 50
	MaxItems = 5000
)

// Fetch gets one page of a list: its items and the next cursor ("" at the
// end).
type Fetch func(ctx context.Context, cursor string) (items []json.RawMessage, next string, err error)

// Listing is a whole list as All read it.
type Listing struct {
	Items []json.RawMessage
	Pages int
	// Capped is set when MaxPages or MaxItems stopped the listing (the
	// items are the first MaxItems), or the upstream sent a cursor twice.
	Capped bool
}

// All follows nextCursor from the first page to the last, up to the caps.
func All(ctx context.Context, fetch Fetch) (Listing, error) {
	var l Listing
	seen := map[string]bool{}
	cursor := ""
	for {
		items, next, err := fetch(ctx, cursor)
		if err != nil {
			return l, err
		}
		l.Pages++
		l.Items = append(l.Items, items...)
		if len(l.Items) >= MaxItems {
			l.Capped = len(l.Items) > MaxItems || next != ""
			l.Items = l.Items[:MaxItems]
			return l, nil
		}
		if next == "" {
			return l, nil
		}
		if l.Pages >= MaxPages || seen[next] {
			l.Capped = true
			return l, nil
		}
		seen[next], cursor = true, next
	}
}
