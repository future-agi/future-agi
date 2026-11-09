// Package httppath defines the one canonical form of a request path that the
// router and every path-keyed gate agree on.
//
// The router serves only canonical paths and rejects every other spelling, so
// every request that reaches a handler satisfies Canonical(p) == p. A gate
// that decides on Canonical(p) therefore decides exactly as it would on the
// path the handler sees, however the client spelled it.
package httppath

import "path"

// Canonical returns the canonical form of a request path: rooted, no empty
// segments ("//"), no "." or ".." segments, and no trailing slash except for
// the root itself. "//v1/videos/x/" and "/zz/../v1/./videos/x" both become
// "/v1/videos/x".
func Canonical(p string) string {
	if p == "" || p[0] != '/' {
		p = "/" + p
	}
	return path.Clean(p)
}

// IsCanonical reports whether p is already in canonical form. It does not
// allocate for a canonical path.
func IsCanonical(p string) bool {
	return p != "" && p[0] == '/' && Canonical(p) == p
}
