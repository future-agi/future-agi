def setup(world):
    """Seed one already-matched booking for Marcus (rdr_marcus) so the caller has an
    active ride to cancel. Mirrors exactly what tools-api's own /book_ride handler writes
    (same columns, same 5.00 flat cancellation_fee), so `ready`/`check` below can assume the
    same shape a real booking would have."""
    world.put(
        "bookings",
        {
            "booking_ref": "UBSEED0001",
            "rider_id": "rdr_marcus",
            "pickup_place_id": "plc_ferry_bldg",
            "dropoff_place_id": "plc_200_market",
            "product_id": "uberx",
            "payment_method": "pm_marcus_mc",
            "quoted_fare_low": 15.00,
            "quoted_fare_high": 19.00,
            "status": "matched",
            "driver_name": "Amir",
            "vehicle": "white Toyota Camry",
            "plate": "8XYZ123",
            "eta_pickup_min": 6,
            "cancellation_fee": 5.00,
        },
    )
