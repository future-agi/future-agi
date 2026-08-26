def ready(world):
    users = world.state("users")["users"]
    priya = next((u for u in users if u["rider_id"] == "rdr_priya"), None)
    if priya is None:
        return "seed rider rdr_priya is missing"
    if priya["status"] != "suspended":
        return f"rdr_priya status is {priya['status']!r}, expected 'suspended'"

    bookings = world.state("bookings")["bookings"]
    if any(b["rider_id"] == "rdr_priya" for b in bookings):
        return "rdr_priya already has a booking before the call starts"
    return None
