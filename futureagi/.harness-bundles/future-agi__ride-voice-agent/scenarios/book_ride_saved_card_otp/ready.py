def ready(world):
    users = world.state("users")["users"]
    dana = next((u for u in users if u["rider_id"] == "rdr_dana"), None)
    if dana is None:
        return "seed rider rdr_dana is missing"
    if dana["status"] != "active":
        return f"rdr_dana status is {dana['status']!r}, expected 'active'"

    methods = world.state("payment_methods")["payment_methods"]
    default_card = next(
        (m for m in methods if m["rider_id"] == "rdr_dana" and m["is_default"]), None
    )
    if default_card is None:
        return "rdr_dana has no default payment method"
    if not default_card["is_valid"] or default_card["is_expired"]:
        return "rdr_dana's default payment method is not valid/unexpired"

    otps = world.state("otp_codes")["otp_codes"]
    otp = next((o for o in otps if o["phone"] == "+14155550101"), None)
    if otp is not None and otp["verified"]:
        return "the OTP for +14155550101 is already verified before the call starts"
    return None
