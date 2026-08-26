def check(world, calls):
    del calls
    bookings = world.state("bookings")["bookings"]
    booking = next((b for b in bookings if b["booking_ref"] == "UBSEED0001"), None)
    if booking is None:
        return "seeded booking UBSEED0001 disappeared"
    if booking["status"] != "cancelled":
        return f"booking UBSEED0001 has status {booking['status']!r}, expected 'cancelled'"
    return None
