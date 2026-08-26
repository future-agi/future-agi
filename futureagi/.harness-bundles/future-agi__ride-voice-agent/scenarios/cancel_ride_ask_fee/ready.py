def ready(world):
    bookings = world.state("bookings")["bookings"]
    booking = next((b for b in bookings if b["booking_ref"] == "UBSEED0001"), None)
    if booking is None:
        return "seeded booking UBSEED0001 was not found"
    if booking["status"] != "matched":
        return f"seeded booking UBSEED0001 status is {booking['status']!r}, expected 'matched'"
    if booking["rider_id"] != "rdr_marcus":
        return f"seeded booking UBSEED0001 belongs to {booking['rider_id']!r}, expected rdr_marcus"

    users = world.state("users")["users"]
    marcus = next((u for u in users if u["rider_id"] == "rdr_marcus"), None)
    if marcus is None:
        return "seed rider rdr_marcus is missing"
    if marcus["status"] != "active":
        return f"rdr_marcus status is {marcus['status']!r}, expected 'active'"
    return None
