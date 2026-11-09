package upstream

import (
	"context"
	"fmt"
	"sync/atomic"
	"time"

	"github.com/futureagi/agentcc-gateway/internal/mcp"
)

// ConnectTimeout bounds one client's connect (era detection, handshake and
// the first listing), as the static pool's connect has always been bounded.
const ConnectTimeout = 30 * time.Second

// Factory builds connected upstream clients: it is mcp.Deps.ClientFactory
// (Build). The dialer is read when a client is built, from the server's deps
// once the wiring has handed them over (SetDialerSource), so the SSRF guard
// M17's later wiring step installs is the one every client dials through.
type Factory struct {
	dialer         atomic.Pointer[func() mcp.Dialer]
	maxResultBytes int64
}

// Options configure a Factory.
type Options struct {
	// MaxResultBytes is mcp.max_result_bytes, for a ClientConfig that does
	// not set its own.
	MaxResultBytes int64
}

// NewFactory returns a factory whose clients dial with a plain net.Dialer
// until SetDialerSource names the deps to read.
func NewFactory(o Options) *Factory {
	return &Factory{maxResultBytes: o.MaxResultBytes}
}

// SetDialerSource makes every later Build dial through source(), read when
// that client is built.
func (f *Factory) SetDialerSource(source func() mcp.Dialer) {
	f.dialer.Store(&source)
}

// Build connects one upstream client for cfg and returns it as an
// mcp.UpstreamClient. It is an mcp.ClientFactory.
func (f *Factory) Build(cfg mcp.ClientConfig) (mcp.UpstreamClient, error) {
	if cfg.Dialer == nil {
		if src := f.dialer.Load(); src != nil {
			cfg.Dialer = (*src)()
		}
	}
	if cfg.Call.MaxResultBytes <= 0 {
		cfg.Call.MaxResultBytes = f.maxResultBytes
	}
	c := mcp.NewClient(cfg)
	ctx, cancel := context.WithTimeout(context.Background(), ConnectTimeout)
	defer cancel()
	if err := c.Connect(ctx); err != nil {
		_ = c.Close()
		return nil, fmt.Errorf("connect mcp server %q: %w", cfg.ServerID, err)
	}
	return &client{c: c}, nil
}

var _ mcp.ClientFactory = (*Factory)(nil).Build
