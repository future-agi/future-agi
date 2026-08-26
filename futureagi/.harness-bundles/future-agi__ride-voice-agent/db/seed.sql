-- Seed data. Three caller profiles exercise the §6 payment decision ladder:
--   +14155550101 Dana   — known rider, valid default card  -> OTP path
--   +14155550102 Marcus — known rider, expired card only   -> cash / pay-link path
--   +14155550103 Priya  — suspended account                -> must refuse to book
--   anything else       — unknown number -> guest flow

INSERT INTO market_config (market, cash_supported, currency, surge_multiplier, available_products) VALUES
  ('US-SF',  FALSE, 'USD', 1.00, ARRAY['uberx','comfort','uberxl','black','wav']),
  ('US-NYC', FALSE, 'USD', 1.35, ARRAY['uberx','comfort','uberxl','black']),
  ('IN-BLR', TRUE,  'INR', 1.00, ARRAY['uberx','uberxl']);

INSERT INTO products (product_id, display_name, capacity, base_fare, per_mile, per_minute, min_fare, description, is_wav, sort_order) VALUES
  ('uberx',   'UberX',   4, 2.55, 1.75, 0.35,  8.00, 'Affordable everyday rides',        FALSE, 1),
  ('comfort', 'Comfort', 4, 3.50, 2.10, 0.45, 11.00, 'Newer cars, extra legroom',        FALSE, 2),
  ('uberxl',  'UberXL',  6, 4.20, 2.60, 0.50, 14.00, 'Room for up to six',               FALSE, 3),
  ('black',   'Black',   4, 8.00, 4.10, 0.75, 26.00, 'Premium rides, professional drivers', FALSE, 4),
  ('wav',     'UberWAV', 4, 2.55, 1.75, 0.35,  8.00, 'Wheelchair-accessible vehicle',    TRUE,  5);

INSERT INTO places (place_id, formatted_address, city, market, lat, lng, aliases) VALUES
  ('plc_hilton_us',  '333 O''Farrell Street, San Francisco, CA', 'San Francisco', 'US-SF', 37.785980, -122.410140,
     ARRAY['hilton','hilton union square','the hilton on union square','union square hilton']),
  ('plc_sfo_intl',   'SFO International Terminal, San Francisco, CA', 'San Francisco', 'US-SF', 37.615223, -122.389977,
     ARRAY['sfo','sfo international','the airport','san francisco airport','international terminal']),
  ('plc_200_market', '200 Market Street, San Francisco, CA', 'San Francisco', 'US-SF', 37.793750, -122.396990,
     ARRAY['200 market','200 market street','market street']),
  ('plc_15_market',  '15 Market Street, San Francisco, CA', 'San Francisco', 'US-SF', 37.792100, -122.395400,
     ARRAY['15 market','fifteen market street']),
  ('plc_ferry_bldg', 'Ferry Building, San Francisco, CA', 'San Francisco', 'US-SF', 37.795530, -122.393300,
     ARRAY['ferry building','the ferry building','embarcadero']),
  ('plc_home_dana',  '1200 Guerrero Street, San Francisco, CA', 'San Francisco', 'US-SF', 37.751900, -122.422600,
     ARRAY['guerrero','1200 guerrero']),
  ('plc_work_dana',  '1455 Market Street, San Francisco, CA', 'San Francisco', 'US-SF', 37.775600, -122.417100,
     ARRAY['1455 market','the office']),
  ('plc_main_st_sf', 'Main Street, San Francisco, CA', 'San Francisco', 'US-SF', 37.791400, -122.392600,
     ARRAY['main street']),
  ('plc_main_st_la', 'Main Street, Los Angeles, CA', 'Los Angeles', 'US-LA', 34.050400, -118.242000,
     ARRAY['main street']),
  ('plc_oak_air',    'Oakland International Airport, Oakland, CA', 'Oakland', 'US-SF', 37.712600, -122.212000,
     ARRAY['oakland airport','oak']),
  ('plc_maya_home', '1890 Jefferson Street, San Francisco, CA', 'San Francisco', 'US-SF', 37.804600, -122.442300,
     ARRAY['1890 jefferson','jefferson street','marina home']),
  ('plc_mission_dolores', 'Mission Dolores Park, 19th Street, San Francisco, CA', 'San Francisco', 'US-SF', 37.759600, -122.426900,
     ARRAY['mission dolores','dolores park','mission dolores park']),
  ('plc_ucsf_parnassus', 'UCSF Medical Center, 505 Parnassus Avenue, San Francisco, CA', 'San Francisco', 'US-SF', 37.763100, -122.458600,
     ARRAY['ucsf parnassus','ucsf medical center','505 parnassus']),
  ('plc_noor_home', '88 King Street, San Francisco, CA', 'San Francisco', 'US-SF', 37.779200, -122.389100,
     ARRAY['88 king','king street','noor home']),
  ('plc_noor_work', 'Salesforce Tower, 415 Mission Street, San Francisco, CA', 'San Francisco', 'US-SF', 37.789700, -122.396000,
     ARRAY['salesforce tower','415 mission','noor work']),
  ('plc_caltrain', 'San Francisco Caltrain Station, 700 4th Street, San Francisco, CA', 'San Francisco', 'US-SF', 37.776400, -122.394300,
     ARRAY['caltrain','fourth and king','4th and king','700 fourth street']),
  ('plc_chase_center', 'Chase Center, 1 Warriors Way, San Francisco, CA', 'San Francisco', 'US-SF', 37.768000, -122.387700,
     ARRAY['chase center','the arena','warriors way']),
  ('plc_225_mission_sf', '225 Mission Street, San Francisco, CA', 'San Francisco', 'US-SF', 37.790500, -122.396800,
     ARRAY['mission street','225 mission','fremont street office']),
  ('plc_mission_santa_cruz', 'Mission Street, Santa Cruz, CA', 'Santa Cruz', 'US-SF', 36.974100, -122.030800,
     ARRAY['mission street','santa cruz mission']);

-- Dana: valid Visa default -> §6 step 2/4 (OTP before charging the card)
INSERT INTO users (rider_id, phone, first_name, status, phone_verified, rating, default_market, home_city, preferred_language) VALUES
  ('rdr_dana',   '+14155550101', 'Dana',   'active',    TRUE, 4.9, 'US-SF', 'San Francisco', 'en'),
  ('rdr_marcus', '+14155550102', 'Marcus', 'active',    TRUE, 4.6, 'US-SF', 'San Francisco', 'en'),
  ('rdr_priya',  '+14155550103', 'Priya',  'suspended', TRUE, 4.2, 'US-SF', 'San Francisco', 'en'),
  ('rdr_arjun',  '+919845550104','Arjun',  'active',    TRUE, 4.7, 'IN-BLR','Bengaluru',     'en'),
  ('rdr_maya',   '+14155550105', 'Maya',   'active',    TRUE, 4.8, 'US-SF', 'San Francisco', 'en'),
  ('rdr_jorge',  '+14155550106', 'Jorge',  'active',    TRUE, 4.7, 'US-SF', 'San Francisco', 'es'),
  ('rdr_noor',   '+14155550107', 'Noor',   'active',    TRUE, 4.9, 'US-SF', 'San Francisco', 'en'),
  ('rdr_eli',    '+14155550108', 'Eli',    'active',    TRUE, 4.5, 'US-SF', 'Oakland',       'en'),
  ('rdr_kenji',  '+14155550109', 'Kenji',  'active',    TRUE, 4.9, 'US-SF', 'San Francisco', 'ja');

INSERT INTO payment_methods (id, rider_id, type, brand, last4, is_default, is_valid, is_expired) VALUES
  ('pm_dana_visa',   'rdr_dana',   'card', 'Visa',       '4242', TRUE,  TRUE,  FALSE),
  ('pm_dana_amex',   'rdr_dana',   'card', 'Amex',       '9001', FALSE, TRUE,  FALSE),
  ('pm_marcus_mc',   'rdr_marcus', 'card', 'Mastercard', '5544', TRUE,  FALSE, TRUE),
  ('pm_priya_visa',  'rdr_priya',  'card', 'Visa',       '1111', TRUE,  TRUE,  FALSE),
  ('pm_maya_visa',   'rdr_maya',   'card', 'Visa',       '7308', TRUE,  TRUE,  FALSE),
  ('pm_jorge_visa',  'rdr_jorge',  'card', 'Visa',       '1486', TRUE,  TRUE,  FALSE),
  ('pm_noor_mc',     'rdr_noor',   'card', 'Mastercard', '6029', TRUE,  TRUE,  FALSE),
  ('pm_eli_amex',    'rdr_eli',    'card', 'Amex',       '3107', TRUE,  TRUE,  FALSE),
  ('pm_kenji_visa',  'rdr_kenji',  'card', 'Visa',       '8831', TRUE,  TRUE,  FALSE);

INSERT INTO wallets (rider_id, uber_cash_balance) VALUES
  ('rdr_dana', 12.00), ('rdr_marcus', 65.00), ('rdr_priya', 0.00), ('rdr_arjun', 0.00),
  ('rdr_maya', 6.25), ('rdr_jorge', 80.00), ('rdr_noor', 18.40), ('rdr_eli', 3.15), ('rdr_kenji', 0.00);

INSERT INTO saved_places (id, rider_id, label, place_id, formatted_address, lat, lng) VALUES
  ('sp_dana_home', 'rdr_dana', 'home', 'plc_home_dana', '1200 Guerrero Street, San Francisco, CA', 37.751900, -122.422600),
  ('sp_dana_work', 'rdr_dana', 'work', 'plc_work_dana', '1455 Market Street, San Francisco, CA', 37.775600, -122.417100),
  ('sp_noor_home', 'rdr_noor', 'home', 'plc_noor_home', '88 King Street, San Francisco, CA', 37.779200, -122.389100),
  ('sp_noor_work', 'rdr_noor', 'work', 'plc_noor_work', 'Salesforce Tower, 415 Mission Street, San Francisco, CA', 37.789700, -122.396000);

INSERT INTO trips (trip_id, rider_id, dropoff_place_id, dropoff_formatted_address, taken_at, fare_total) VALUES
  ('trp_1', 'rdr_dana',   'plc_sfo_intl',   'SFO International Terminal, San Francisco, CA', now() - interval '3 days', 48.20),
  ('trp_2', 'rdr_dana',   'plc_ferry_bldg', 'Ferry Building, San Francisco, CA',             now() - interval '9 days', 14.75),
  ('trp_3', 'rdr_marcus', 'plc_200_market', '200 Market Street, San Francisco, CA',          now() - interval '2 days', 19.10),
  ('trp_eli_1', 'rdr_eli', 'plc_chase_center', 'Chase Center, 1 Warriors Way, San Francisco, CA', now() - interval '2 days', 22.60);

INSERT INTO promotions (id, rider_id, credit_amount, description) VALUES
  ('promo_dana_5', 'rdr_dana', 5.00, '$5 off your next ride');

-- Deterministic per-rider OTPs keep test runs reproducible without making every caller identical.
INSERT INTO otp_codes (phone, code) VALUES
  ('+14155550101', '638204'), ('+14155550102', '817463'), ('+14155550103', '264819'),
  ('+14155550105', '804271'), ('+14155550106', '349175'), ('+14155550107', '592804'),
  ('+14155550108', '417936'), ('+14155550109', '731905'), ('+919845550104', '286413');
