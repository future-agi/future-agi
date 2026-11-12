package auth

import (
	"context"
	"fmt"
	"reflect"
	"strings"
	"time"
)

// These test-only adapters keep the tests-first commit valid against the
// composed baseline, which has no cached-eligibility or read-budget types.
// They never implement authentication: they delegate to the real new seam,
// or to the exact existing baseline path when that seam is absent. The baseline
// then fails actual query-count, refusal or PRIMARY-entry-barrier assertions.
func (a *Authenticator) portCachedCredentialCurrent(ctx context.Context, api, secret, key string, prior *ResolveResult) (bool, error) {
	if current, ok := any(a).(interface {
		cachedCredentialCurrent(context.Context, string, string, string, *ResolveResult) (bool, error)
	}); ok {
		return current.cachedCredentialCurrent(ctx, api, secret, key, prior)
	}
	return a.cache.currentResult(ctx, key, prior)
}

func (c *cache) portPublishValidatedWithBudget(ctx context.Context, key string, result *ResolveResult, epoch uint64, stamp cacheStamp, now func() time.Time) (bool, error) {
	if publisher, ok := any(c).(interface {
		publishValidatedWithBudget(context.Context, string, *ResolveResult, uint64, cacheStamp, func() time.Time) (bool, error)
	}); ok {
		return publisher.publishValidatedWithBudget(ctx, key, result, epoch, stamp, now)
	}
	return c.publishValidated(ctx, key, result, epoch, stamp)
}

func (e *cacheEntry) portEligibilityCurrent(now time.Time, generation, epoch uint64) bool {
	current, ok := any(e).(interface {
		eligibilityCurrent(time.Time, uint64, uint64) bool
	})
	return ok && current.eligibilityCurrent(now, generation, epoch)
}

// Observation only, under cache.mu. Reflection avoids referring to types/fields
// that do not exist in the tests-first baseline. No unsafe, mutation, fabricated
// budget, timer reset, replacement authority or production bypass is involved.
// Capture every value field and pointer identity so the original complete-value
// supersession assertions are retained, not reduced to just pointer equality.
type portBudgetObservation struct {
	id      uintptr
	value   string
	started reflect.Value
}

func portBudgetLocked(c *cache, key string) portBudgetObservation {
	m := reflect.ValueOf(c).Elem().FieldByName("primaryReads")
	if !m.IsValid() || m.IsNil() {
		return portBudgetObservation{}
	}
	p := m.MapIndex(reflect.ValueOf(key))
	if !p.IsValid() || p.IsNil() {
		return portBudgetObservation{}
	}
	var b strings.Builder
	portValueSnapshot(&b, p.Elem())
	return portBudgetObservation{p.Pointer(), b.String(), p.Elem().FieldByName("started")}
}

func portBudgetCountLocked(c *cache) int {
	m := reflect.ValueOf(c).Elem().FieldByName("primaryReads")
	if !m.IsValid() {
		return 0
	}
	return m.Len()
}

func portBudgetMapNilLocked(c *cache) bool {
	m := reflect.ValueOf(c).Elem().FieldByName("primaryReads")
	return !m.IsValid() || m.IsNil()
}

func (b portBudgetObservation) startedAt(at time.Time) bool {
	return b.started.IsValid() && b.started.Equal(reflect.ValueOf(at))
}

func (b portBudgetObservation) matchesEntryStart(entry *cacheEntry) bool {
	started := reflect.ValueOf(entry).Elem().FieldByName("revalidatedAt")
	return b.started.IsValid() && started.IsValid() && started.Equal(b.started)
}

func portValueSnapshot(out *strings.Builder, v reflect.Value) {
	fmt.Fprintf(out, "%s:", v.Type())
	switch v.Kind() {
	case reflect.Struct:
		out.WriteByte('{')
		for i := 0; i < v.NumField(); i++ {
			fmt.Fprintf(out, "%s=", v.Type().Field(i).Name)
			portValueSnapshot(out, v.Field(i))
		}
		out.WriteByte('}')
	case reflect.Array:
		out.WriteByte('[')
		for i := 0; i < v.Len(); i++ {
			portValueSnapshot(out, v.Index(i))
		}
		out.WriteByte(']')
	case reflect.Pointer:
		// Includes the timer and time-zone identities, not their mutable internals.
		fmt.Fprintf(out, "%x;", v.Pointer())
	case reflect.String:
		fmt.Fprintf(out, "%q;", v.String())
	case reflect.Bool:
		fmt.Fprintf(out, "%t;", v.Bool())
	case reflect.Int, reflect.Int8, reflect.Int16, reflect.Int32, reflect.Int64:
		fmt.Fprintf(out, "%d;", v.Int())
	case reflect.Uint, reflect.Uint8, reflect.Uint16, reflect.Uint32, reflect.Uint64, reflect.Uintptr:
		fmt.Fprintf(out, "%d;", v.Uint())
	default:
		panic("unsupported budget observation type: " + v.Type().String())
	}
}
