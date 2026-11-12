package routebinding

import "errors"

var (
	ErrOwner       = errors.New("invalid publication owner")
	ErrReservation = errors.New("invalid publication reservation")
)

// Owner binds reservations to one installed owner on one gate. It contains only
// token bookkeeping, not an owner registry, configuration, or readiness state.
// Keep it private to that canonical owner. Do not copy it or recreate it to
// bypass an owner's closed state; readiness belongs to the canonical owner.
type Owner struct {
	self *Owner
	gate *Gate
	last uint64 // Last committed ordinal; protected by gate's write fence.
}

// NewOwner is installation-time bookkeeping, not readiness or source authority.
// The caller installs one Owner on its canonical object before serving.
func (g *Gate) NewOwner() (*Owner, error) {
	var o *Owner
	err := g.Read(func(r *Read) error {
		return r.Use(g, func() error {
			o = &Owner{gate: g}
			o.self = o
			return nil
		})
	})
	return o, err
}

func (o *Owner) valid() bool { return o != nil && o.self == o && o.gate.valid() }

// Reservation is an opaque owner-bound, single-commit reservation. Unlike a
// callback token it intentionally survives a scope, so required persistence can
// run outside Gate. A copied value is invalid; dropping an unused reservation
// burns its ordinal. No on-disk artifact or config fingerprint is stored here.
type Reservation struct {
	self  *Reservation
	owner *Owner
	stamp Stamp
	used  bool // Protected by owner's gate write fence.
}

// Reserve must precede mandatory disk work and source changes. The canonical
// owner must separately serialize its own source/destination/precondition
// checks and disk operations. This allocator does not replace that sequencer.
func (o *Owner) Reserve(w *Write) (*Reservation, error) {
	if !o.valid() {
		return nil, ErrOwner
	}
	var reservation *Reservation
	err := w.Use(o.gate, func() error {
		stamp, err := o.gate.next()
		if err != nil {
			return err
		}
		reservation = &Reservation{owner: o, stamp: stamp}
		reservation.self = reservation
		return nil
	})
	return reservation, err
}

// Commit installs prepared owner state without asking for a fresh ordinal.
// Unrelated owners' later allocations and exhaustion cannot invalidate it.
// A later committed reservation on THIS owner does invalidate older ones.
// Keep the owner's sequencer through preflight, required disk work, this commit,
// and any ordered persistence tail. Drain that sequencer before Gate.Close.
//
// install must be an infallible bounded memory commit under the owner's mutex,
// acquired INSIDE install (gate -> scope -> owner). It must not reenter Gate,
// Write.Use, Next, Reserve, Commit, Close, perform I/O or call arbitrary observers.
// A panic consumes the reservation and is not a rollback. Check all ordinary
// error conditions before required disk replacement; this primitive cannot
// certify source/destination stability or repair a violated owner protocol.
func (o *Owner) Commit(w *Write, r *Reservation, install func(Stamp)) error {
	if !o.valid() {
		return ErrOwner
	}
	if install == nil {
		return ErrCallback
	}
	return w.Use(o.gate, func() error {
		if r == nil || r.self != r || r.owner != o || r.used || !r.stamp.Valid() || r.stamp.epoch != o.gate.epoch || r.stamp.ordinal <= o.last {
			return ErrReservation
		}
		r.used = true
		o.last = r.stamp.ordinal
		install(r.stamp)
		return nil
	})
}
