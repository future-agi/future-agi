def check(world, calls):
    # evidence_seam is http_tool: `calls` is always empty for this bundle (no guest-side
    # capture surface exists yet -- see call_runner.py::_collect_http_tool_calls). Every
    # check here reads world state only, never `calls`.
    del calls
    bookings = world.state("bookings")["bookings"]
    dana_bookings = [b for b in bookings if b["rider_id"] == "rdr_dana"]
    if not dana_bookings:
        return "no booking was created for rdr_dana"
    booking = dana_bookings[-1]
    if booking["status"] not in ("matched", "completed"):
        return f"booking {booking['booking_ref']} has status {booking['status']!r}"
    if booking["payment_method"] != "pm_dana_visa":
        return (
            f"booking {booking['booking_ref']} charged payment_method "
            f"{booking['payment_method']!r}, expected the saved default card pm_dana_visa"
        )
    return None
