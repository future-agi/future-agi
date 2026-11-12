package main

import (
	"log/slog"

	ks "github.com/futureagi/agentcc-gateway/internal/keyscope"
	keyscopeplugin "github.com/futureagi/agentcc-gateway/internal/plugins/keyscope"
	"github.com/futureagi/agentcc-gateway/internal/redisstate"
)

// initKeyScope wires scoped keys' pipeline half (CN22c): the keyscope plugin
// (pre 21, right after auth; post 2, right after the cost plugin), which
// refuses a scoped key the key gate did not admit and holds each budgeted
// key's spend budget. With Redis the spend is one counter per key and period
// that every replica shares (<prefix>budget:<org>:agent_key:<key id>:<period
// start>); without it each replica counts its own. Once the server is built
// the plugin reports its refusals through the server's reporter, as the key
// gate does. Request auth off: no key is ever scoped, so nothing is wired.
func initKeyScope(d *wiringDeps) error {
	if !d.cfg.Auth.Enabled || d.keyStore == nil {
		return nil
	}
	var shared ks.SharedSpend
	if d.redis != nil {
		prefix := d.cfg.Redis.Prefix
		if prefix == "" {
			prefix = "agentcc:"
		}
		shared = redisstate.NewBudgetStore(d.redis, prefix+"budget:")
	} else {
		slog.Warn("scoped key budgets have no Redis: each replica counts a key's spend on its own")
	}
	plugin := keyscopeplugin.New(d.keyStore, ks.NewSpend(shared))
	d.plugins = append(d.plugins, plugin)
	d.afterServer(func() error {
		plugin.SetReporter(d.server.KeyScopeReporter())
		return nil
	})
	return nil
}
