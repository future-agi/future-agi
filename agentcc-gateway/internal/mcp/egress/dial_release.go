//go:build !cataloguepreflighttest

package egress

import (
	"context"
	"net"
)

// The release build has no resolver injection API, state or branch.
func (d *Dialer) dial(ctx context.Context, network, address string, plaintext bool) (net.Conn, error) {
	return d.dialDefault(ctx, network, address, plaintext)
}
