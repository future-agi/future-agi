package restmcp

import (
	"fmt"
	"sort"
)

// fitList keeps a JSON result within max bytes, as JSON: it drops trailing
// items of the result's list (the result itself when it is an array, else
// its largest top-level array member, such as Gmail's threads), keeping as
// many leading items as fit beside a notice that says how many were kept.
// The notice is a second text block, so the first stays valid JSON. ok is
// false when there is no list to shorten or even its first item does not
// fit; the caller then cuts the text.
func fitList(v any, size, max int) (fitted, notice string, ok bool) {
	var items []any
	var where string
	var rebuild func([]any) any
	switch t := v.(type) {
	case []any:
		items, where = t, "the result"
		rebuild = func(keep []any) any { return keep }
	case map[string]any:
		key := largestArray(t)
		if key == "" {
			return "", "", false
		}
		items, where = t[key].([]any), fmt.Sprintf("%q", key)
		rebuild = func(keep []any) any {
			out := make(map[string]any, len(t))
			for k, val := range t {
				out[k] = val
			}
			out[key] = keep
			return out
		}
	default:
		return "", "", false
	}
	noticeFor := func(kept int) string {
		return fmt.Sprintf("[truncated: the result is %d bytes and this tool returns at most %d (response.maxBytes), "+
			"so it keeps the first %d of %d items of %s. Ask for fewer results per page, fewer fields, or the next page.]",
			size, max, kept, len(items), where)
	}
	// The largest k whose encoding fits beside its notice; encodings grow
	// with k, so a binary search finds it.
	k := sort.Search(len(items)+1, func(k int) bool {
		return len(encodeJSON(rebuild(items[:k])))+len(noticeFor(k)) > max
	}) - 1
	if k < 1 {
		return "", "", false
	}
	return encodeJSON(rebuild(items[:k])), noticeFor(k), true
}

// largestArray names the top-level member holding the largest array, by
// encoded size ("" when none holds a non-empty one). Ties go to the first
// name in sorted order, so the choice is stable.
func largestArray(obj map[string]any) string {
	keys := make([]string, 0, len(obj))
	for k := range obj {
		keys = append(keys, k)
	}
	sort.Strings(keys)
	best, bestSize := "", 0
	for _, k := range keys {
		arr, ok := obj[k].([]any)
		if !ok || len(arr) == 0 {
			continue
		}
		if n := len(encodeJSON(arr)); n > bestSize {
			best, bestSize = k, n
		}
	}
	return best
}
