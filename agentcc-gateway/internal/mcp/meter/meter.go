// Package meter holds what MCP tool-call metering needs outside the call
// chain (plan section 5, M15): the in-process per-tool counters, the
// record's content-mode rule and content capping, and the outcome
// classification of a finished call. It is a leaf package: it takes plain
// values, so package mcp can call it from its hooks without a cycle.
//
// The record finalizer reads the process meter (Default), which the gateway's
// wiring installs once at startup (Install) with the key and redactor
// lookups it can build; tests install their own and put the previous one
// back.
package meter

import (
	"sync/atomic"

	"github.com/futureagi/agentcc-gateway/internal/privacy"
)

// Options are the lookups the record finalizer needs from the rest of the
// gateway. A nil lookup is a source that is not there.
type Options struct {
	// KeyLogContent returns the presented key's log_content metadata (plan
	// section 6.13): by key id when the principal has one, else by the raw
	// key the request carries; "" when the key has none or is unknown.
	KeyLogContent func(keyID, rawKey string) string
	// Redactor returns the redactor for an org's recorded content: the
	// org's own, else the global one; nil when none applies.
	Redactor func(orgID string) *privacy.Redactor
}

// Meter is the process's metering state: its lookups and its counters.
type Meter struct {
	opts     Options
	counters *Counters
}

// New returns a meter with the given lookups and fresh counters.
func New(opts Options) *Meter {
	return &Meter{opts: opts, counters: NewCounters()}
}

// Counters are the meter's per-tool counters. They live as long as the
// meter, so re-registering an upstream never resets them.
func (m *Meter) Counters() *Counters { return m.counters }

// KeyLogContent is the presented key's log_content, or "" without a lookup.
func (m *Meter) KeyLogContent(keyID, rawKey string) string {
	if m.opts.KeyLogContent == nil || (keyID == "" && rawKey == "") {
		return ""
	}
	return m.opts.KeyLogContent(keyID, rawKey)
}

// Redactor is the redactor for orgID's content, or nil.
func (m *Meter) Redactor(orgID string) *privacy.Redactor {
	if m.opts.Redactor == nil {
		return nil
	}
	return m.opts.Redactor(orgID)
}

var current atomic.Pointer[Meter]

func init() { current.Store(New(Options{})) }

// Default is the process meter: the one Install set last, or a meter with no
// lookups until then.
func Default() *Meter { return current.Load() }

// Install makes m the process meter and returns a function that puts the
// previous one back, for tests. A nil m is ignored.
func Install(m *Meter) (restore func()) {
	if m == nil {
		return func() {}
	}
	prev := current.Swap(m)
	return func() { current.Store(prev) }
}
