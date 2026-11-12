package routebinding

import "math"

// SeedPenultimateOrdinalForTest raises a fixture gate to its last allocatable
// ordinal. It never lowers a counter and is absent from production builds.
func SeedPenultimateOrdinalForTest(g *Gate) error {
	return g.Publish(func(w *Write) error {
		return w.Use(g, func() error {
			if g.ordinal >= math.MaxUint64-1 {
				return ErrExhausted
			}
			g.ordinal = math.MaxUint64 - 1
			return nil
		})
	})
}

func OrdinalAtLimitForTest(g *Gate) (bool, error) {
	var atLimit bool
	err := g.Read(func(r *Read) error {
		return r.Use(g, func() error { atLimit = g.ordinal == math.MaxUint64; return nil })
	})
	return atLimit, err
}
