def check(world, calls):
    del calls
    otps = world.state("otp_codes")["otp_codes"]
    otp = next((o for o in otps if o["phone"] == "+14155550101"), None)
    if otp is None:
        return "no OTP record found for +14155550101"
    if not otp["verified"]:
        return "the OTP for +14155550101 was never verified (tools-api marks it verified " \
            "only after /verify_otp accepts the code the caller read back)"
    return None
