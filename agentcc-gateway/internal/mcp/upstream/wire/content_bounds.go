package wire

import (
	"context"
	"encoding/json"
	"errors"
	"io"

	"github.com/futureagi/agentcc-gateway/internal/mcp/contentbounds"
	"github.com/futureagi/agentcc-gateway/internal/mcp/contentpolicy"
)

// Keep the transport's existing errors.Is(ErrTooLarge) contract for byte
// overflow, while structural refusals expose only fixed contentbounds codes.
func contentError(err error) error {
	var refusal *contentbounds.Error
	if errors.As(err, &refusal) && refusal.Code() == contentbounds.CodeReadBytes {
		return ErrTooLarge
	}
	return err
}

func readContent(r io.Reader, max int64) ([]byte, error) {
	raw, err := contentpolicy.ReadObject(r, max)
	if err != nil {
		return nil, contentError(err)
	}
	if err := checkContent(raw, max); err != nil {
		return nil, err
	}
	return raw, nil
}

func checkContent(raw []byte, max int64) error {
	return contentError(contentpolicy.Envelope(raw, max))
}

// AllBounded is used by real client discovery. All remains the legacy paging
// helper for callers with its prefix-return contract; discovery must never
// cache an apparently complete prefix after a cycle, overflow or fetch error.
func AllBounded(ctx context.Context, fetch Fetch, inherited int64) (Listing, error) {
	limits := contentpolicy.Limits(inherited)
	items, err := contentbounds.NewAccumulator(limits)
	if err != nil {
		return Listing{}, err
	}
	cursors, err := contentbounds.NewAccumulator(contentpolicy.CursorLimits(inherited))
	if err != nil {
		return Listing{}, err
	}
	var list Listing
	cursor := ""
	for {
		if err := ctx.Err(); err != nil {
			return Listing{}, err
		}
		page, next, err := fetch(ctx, cursor)
		if err != nil {
			return Listing{}, err
		}
		list.Pages++
		for _, raw := range page {
			if err := contentpolicy.AddItem(items, raw, limits); err != nil {
				return Listing{}, err
			}
		}
		// Cursor checks precede staging/publication and the next fetch. The
		// aggregate is local to this refresh and discarded on any failure.
		if next != "" {
			if list.Pages >= MaxPages {
				return Listing{}, contentpolicy.ErrPages
			}
			if err := cursors.ObserveCursor(next); err != nil {
				return Listing{}, err
			}
		}
		for _, raw := range page {
			list.Items = append(list.Items, append(json.RawMessage(nil), raw...))
		}
		if next == "" {
			return list, nil
		}
		cursor = next
	}
}
