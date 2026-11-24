"""
Travel Logistics — Phase 6.

Builds parameterised deep-link URLs for flight search, accommodation search,
and local transport guidance. No live API calls, no scraping, no fares.

All URLs produced are user-controlled search pages on public travel platforms.
Users initiate every booking; this module only constructs search URLs.

Data labels:
  SEARCH_LINK  — parameterised URL to a search results page (not a booking)
  ESTIMATED    — cost guidance from the local destination cost guide
  STATIC       — curated transport guidance (not live)
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Optional
from urllib.parse import urlencode, quote

logger = logging.getLogger(__name__)

# ─── IATA airport code fallback table ────────────────────────────────────────
# Maps the 22 app destinations to their primary IATA airport codes.
# Source: publicly known IATA codes — no commercial data.
DESTINATION_AIRPORTS: dict[str, list[str]] = {
    "goa":        ["GOI"],
    "paris":      ["CDG", "ORY"],
    "tokyo":      ["NRT", "HND"],
    "bali":       ["DPS"],
    "manali":     ["BHU", "KUU"],   # closest: Bhuntar / Chandigarh
    "new york":   ["JFK", "LGA", "EWR"],
    "rome":       ["FCO", "CIA"],
    "bangkok":    ["BKK", "DMK"],
    "santorini":  ["JTR"],
    "amsterdam":  ["AMS"],
    "maldives":   ["MLE"],
    "dubai":      ["DXB"],
    "kerala":     ["COK", "TRV"],
    "prague":     ["PRG"],
    "singapore":  ["SIN"],
    "barcelona":  ["BCN"],
    "kyoto":      ["KIX", "ITM"],   # Osaka airports serve Kyoto
    "istanbul":   ["IST", "SAW"],
    "jaipur":     ["JAI"],
    "queenstown": ["ZQN"],
    "marrakech":  ["RAK"],
    "phuket":     ["HKT"],
}

# ─── Transport guidance ───────────────────────────────────────────────────────
# Static, curated guidance per destination.
# Data type: STATIC — not live schedules or prices.
TRANSPORT_GUIDANCE: dict[str, dict] = {
    "goa": {
        "airport_transfer": "Goa International Airport (GOI) is 30 km from Panaji. Pre-paid taxis from the airport are available (approx. 30–45 min). No rail link to central Goa.",
        "local_transport": "Renting a scooter (₹300–500/day) is the most practical option. App taxis (Goa Miles, Rapido) are available in urban areas. Auto-rickshaws for short trips.",
        "public_transport": "Limited government bus service connects major towns. Not recommended for tight itineraries.",
        "notes": "Illustrative — fares and availability change seasonally.",
    },
    "paris": {
        "airport_transfer": "CDG is 35 km from central Paris. RER B train (€11.80, 35 min) or Le Bus Direct coach. ORY is served by OrlyBus or Orlyval rail.",
        "local_transport": "Metro (14 lines), RER, and bus network. Navigo weekly pass or Paris Visite card offer unlimited travel. Vélib' bike-share widely available.",
        "public_transport": "Excellent metro coverage. Single ticket €2.15 or carnet of 10. Zones 1–2 cover central Paris.",
        "notes": "Illustrative — fares and timetables subject to change.",
    },
    "tokyo": {
        "airport_transfer": "Narita (NRT): Narita Express (N'EX) to Shinjuku ~90 min, ¥3,250. Haneda (HND): Tokyo Monorail or Keikyu line, 30–40 min, ¥500–600.",
        "local_transport": "Suica or Pasmo IC card covers all JR, Metro, and most private lines. Day passes available. Avoid driving.",
        "public_transport": "Tokyo Metro 24-hour pass ¥600. IC card top-up works everywhere including convenience stores.",
        "notes": "Illustrative — fares set by operators and subject to change.",
    },
    "bali": {
        "airport_transfer": "Ngurah Rai (DPS) is 15 km from Seminyak/Kuta. Metered taxi ~IDR 150,000–200,000. Ride-hailing (Gojek, Grab) often cheaper from the designated app-taxi lane.",
        "local_transport": "Private driver hire (IDR 400,000–600,000/day) is the most practical option. Ride-hailing apps work in tourist areas. Scooter rental possible but road conditions vary.",
        "public_transport": "Limited and unreliable. Not recommended for intercity travel.",
        "notes": "Illustrative — prices vary by season and negotiation.",
    },
    "manali": {
        "airport_transfer": "Nearest airport: Bhuntar (KUU), 50 km south. Taxi to Manali ~1.5–2 hours (₹800–1,200). Chandigarh airport (IXC) is ~310 km; Volvo bus or taxi required.",
        "local_transport": "Local taxis and shared jeeps for Rohtang Pass and Solang Valley. Permit required for Rohtang (booked online).",
        "public_transport": "HRTC buses connect Manali to Delhi (overnight), Chandigarh, and Kullu. Local buses to Old Manali and Vashisht.",
        "notes": "Illustrative — road conditions and availability vary significantly by season.",
    },
    "new york": {
        "airport_transfer": "JFK: AirTrain + Subway/LIRR (~$10–15, 60–75 min). LaGuardia: M60 bus or taxi (~$35–50). Newark: NJ Transit + PATH (~$18, 55 min).",
        "local_transport": "Subway (24-hour service, $2.90/ride). OMNY contactless payment. Yellow cabs metered; Uber/Lyft widely available.",
        "public_transport": "7-day unlimited MetroCard $34. OMNY contactless tap works on buses and subway.",
        "notes": "Illustrative — fares subject to MTA updates.",
    },
    "rome": {
        "airport_transfer": "Fiumicino (FCO): Leonardo Express train to Termini €14, 32 min. Ciampino (CIA): Terravision or SIT bus to Termini, €6–7, 40 min.",
        "local_transport": "Walking is best in the centro storico. Metro (2 lines), trams and buses cover wider areas. 48-hour pass €12.50.",
        "public_transport": "Single ticket €1.50, valid 100 minutes. BIT, 24h, 48h, 72h and weekly passes available.",
        "notes": "Illustrative — fares set by ATAC and subject to change.",
    },
    "bangkok": {
        "airport_transfer": "Suvarnabhumi (BKK): Airport Rail Link to Phaya Thai ฿45, 30 min. Don Mueang (DMK): Bus A1/A2 or taxi. Metered taxis from BKK ~฿250–350 + expressway.",
        "local_transport": "BTS Skytrain (฿17–59), MRT (฿17–42), and river boat express. Metered taxis cheap; insist on meter. Grab available.",
        "public_transport": "One-day BTS pass ฿140. Single journey paid by card or token. Chao Phraya Express tourist boat ฿150/day.",
        "notes": "Illustrative — fares set by operators and subject to change.",
    },
    "santorini": {
        "airport_transfer": "Santorini airport (JTR) is 7 km from Fira. Taxis (€15–25), KTEL bus (€2), or hotel transfer. Ferry from Piraeus 5–8 hours.",
        "local_transport": "KTEL bus between main villages (€1.80–2.60). Renting an ATV (€25–40/day) or car recommended for independent travel. Taxis expensive and scarce in peak season.",
        "public_transport": "KTEL blue buses connect Fira, Oia, Perissa, Kamari, and Akrotiri.",
        "notes": "Illustrative — taxis very limited July–August; book ahead.",
    },
    "amsterdam": {
        "airport_transfer": "Schiphol (AMS) has a direct train to Amsterdam Centraal (€6.10, 15 min, runs 24h). Taxis ~€40–50.",
        "local_transport": "Trams (GVB), metro and buses. OV-chipkaart or disposable card. Renting a bike (€12–15/day) is the authentic and practical choice.",
        "public_transport": "1-hour ticket €3.40 from driver; GVB day tickets €9. OV-chipkaart pre-loaded.",
        "notes": "Illustrative — GVB fares subject to change.",
    },
    "maldives": {
        "airport_transfer": "Velana International (MLE): Speedboat transfers to resort islands (30–90 min, $20–80). Seaplane transfers (15–45 min, $200–500 return) for outer atolls.",
        "local_transport": "Inter-island travel by speedboat or ferry. Dhoni (traditional boat) for short local hops. No road between islands.",
        "public_transport": "Local ferry services connect inhabited islands; very cheap but infrequent.",
        "notes": "Illustrative — seaplane prices vary by season and operator.",
    },
    "dubai": {
        "airport_transfer": "Dubai International (DXB): Metro Red/Green Line to central Dubai ~30 min, AED 3–9. Terminal 1 and 3 have direct Metro access. Taxis metered from AED 25 base.",
        "local_transport": "Dubai Metro (Red and Green Lines), trams, and water bus. Nol Card covers all public transport. Uber available.",
        "public_transport": "Single Metro fare AED 3–9.50 depending on zones. Daily Nol Silver card AED 20.",
        "notes": "Illustrative — fares set by RTA and subject to change.",
    },
    "kerala": {
        "airport_transfer": "Cochin (COK) to Kochi city ~30 min by taxi (₹400–600). Trivandrum (TRV) to city centre ~20 min. Pre-paid taxis available at both airports.",
        "local_transport": "Auto-rickshaws in cities (app-based metering in Kochi). Ferries across Vembanad Lake. Houseboats booked from Alleppey jetty.",
        "public_transport": "KSRTC buses connect major towns. Kochi Metro covers the city centre.",
        "notes": "Illustrative — fares vary by season and operator.",
    },
    "prague": {
        "airport_transfer": "Václav Havel (PRG): Bus 119 to Nádraží Veleslavín metro, then Metro A to centre (~30 min, Kč 40). Airport Express bus to Hlavní nádraží ~35 min, Kč 100.",
        "local_transport": "Metro (3 lines), trams (24h service), and buses. 24-hour pass Kč 120. DPP public transport.",
        "public_transport": "Single ticket Kč 30 (30 min) or Kč 40 (90 min). Day passes from Kč 120.",
        "notes": "Illustrative — DPP fares subject to change.",
    },
    "singapore": {
        "airport_transfer": "Changi (SIN): MRT to Tanah Merah then to City Hall ~30 min, SGD 2–3. No cab needed from one of the world's best-connected airports.",
        "local_transport": "MRT and LRT (EZ-Link or Singapore Tourist Pass). Grab available. Walking distances are manageable in the CBD.",
        "public_transport": "EZ-Link card SGD 10 initial (SGD 5 usable credit). Singapore Tourist Pass from SGD 10/day.",
        "notes": "Illustrative — LTA fares subject to change.",
    },
    "barcelona": {
        "airport_transfer": "El Prat (BCN): Aerobus to Plaça Catalunya ~35 min, €6.75 (€11.50 return). Metro L9 Sud to centre ~40 min, €5.15. Taxi ~€35–45.",
        "local_transport": "Metro, bus, and FGC trains. T-Casual 10-trip card ~€12. Barcelona Card includes unlimited transport.",
        "public_transport": "Single fare €2.55 (or more for airport Metro L9). T-Casual 10 trips €12 for zones 1.",
        "notes": "Illustrative — TMB fares subject to change.",
    },
    "kyoto": {
        "airport_transfer": "Nearest: Osaka Kansai (KIX) ~75 min by Haruka express to Kyoto, ¥2,850. Osaka Itami (ITM) ~55 min by Limousine Bus, ¥1,310.",
        "local_transport": "City buses (¥230/ride, day pass ¥700). Kyoto City Bus covers most sights. Cycling recommended. Taxis available but expensive.",
        "public_transport": "1-day city bus pass ¥700, 2-day ¥1,000. IC card (Suica/Icoca) works on all transport.",
        "notes": "Illustrative — fares set by operators and subject to change.",
    },
    "istanbul": {
        "airport_transfer": "Istanbul Airport (IST): Havaist bus to Taksim ~45 min, ₺120–150. Metro M11 to Gayrettepe ~35 min, Istanbulkart fare. Sabiha (SAW): Bus to Taksim ~90 min.",
        "local_transport": "Metro, tram (T1 covers tourist areas), and ferry across the Bosphorus. Istanbulkart covers all modes.",
        "public_transport": "Single fare ₺17 with Istanbulkart (less with the card). Marmaray rail link connects European and Asian sides.",
        "notes": "Illustrative — fares set by IETT/Metro Istanbul and subject to change.",
    },
    "jaipur": {
        "airport_transfer": "Jaipur International (JAI) is 13 km from the city. Pre-paid taxis (~₹300–500), Ola/Uber available. No rail link to airport.",
        "local_transport": "Auto-rickshaws (negotiate fare), Ola/Uber, and cycle-rickshaws in the walled city. Local buses crowded.",
        "public_transport": "JCT city buses cover main routes but are crowded. Tempo (shared auto) for short hops.",
        "notes": "Illustrative — prices vary by negotiation and app surge pricing.",
    },
    "queenstown": {
        "airport_transfer": "Queenstown Airport (ZQN) is 7 km from town. Connectabus Route 1 runs every 30 min (NZD 10). Taxis/Uber ~NZD 25–35. Rental car desks at airport.",
        "local_transport": "Orbus town services. Rental car strongly recommended for Milford Sound and Arrowtown. Cycling trails on the Queenstown Trail (free).",
        "public_transport": "Connectabus covers the town zone and Frankton. Very limited beyond that — a rental car is recommended.",
        "notes": "Illustrative — Orbus fares subject to change.",
    },
    "marrakech": {
        "airport_transfer": "Marrakech Menara (RAK) is 6 km from the medina. Petit taxis (metered, MAD 50–80) or free hotel transfer. Bus 19 runs every 30 min (MAD 30).",
        "local_transport": "Petit taxis (metered, ~MAD 10–30 within city) are plentiful. Agree on fare before longer trips. The medina is best explored on foot.",
        "public_transport": "ALSA city buses cover main routes for MAD 4 per journey.",
        "notes": "Illustrative — always agree taxi fare or use the meter.",
    },
    "phuket": {
        "airport_transfer": "Phuket International (HKT) is 32 km from Patong. No direct rail. Metered taxi ~THB 500–700. Grab available. Smart Bus to Phuket Town.",
        "local_transport": "Grab ride-hailing is reliable and cheaper than taxis. Songthaew (shared truck) between beaches ~THB 30–50. Scooter rental ~THB 200–300/day.",
        "public_transport": "Smart Bus service connects the airport, Phuket Town, and some beaches.",
        "notes": "Illustrative — always negotiate tuk-tuk fares in advance.",
    },
}


# ─── URL builders ─────────────────────────────────────────────────────────────

def _google_flights_url(
    origin: str,
    destination: str,
    depart_date: str,
    return_date: str | None,
    adults: int,
) -> str:
    """
    Build a Google Flights search URL.
    Format: /flights/search with standard query params.
    No API key — this is a public search page.
    """
    # Google Flights uses a URL path format
    dep = depart_date.replace("-", "")
    base = "https://www.google.com/travel/flights"
    if return_date:
        ret = return_date.replace("-", "")
        params = {
            "q": f"Flights from {origin} to {destination}",
            "hl": "en",
        }
    else:
        params = {
            "q": f"Flights from {origin} to {destination}",
            "hl": "en",
        }
    # Use the deep link format that Google Flights actually honours
    dest_code = _get_airport_code(destination)
    orig_code = _get_airport_code(origin)
    if dest_code and orig_code:
        # Structured deep link
        trip_type = "1" if return_date else "2"  # 1=roundtrip, 2=oneway
        path_parts = [orig_code, dest_code, dep]
        if return_date:
            path_parts.append(return_date.replace("-", ""))
        url = f"https://www.google.com/travel/flights/search?q=Flights+from+{quote(origin)}+to+{quote(destination)}&hl=en&curr=USD"
    else:
        url = f"https://www.google.com/travel/flights?q=Flights+from+{quote(origin)}+to+{quote(destination)}&hl=en"
    return url


def _skyscanner_url(
    origin: str,
    destination: str,
    depart_date: str,
    return_date: str | None,
    adults: int,
) -> str:
    """
    Build a Skyscanner search URL using their public URL schema.
    No API key required — this links to public search results.
    """
    dest_code = _get_airport_code(destination) or destination.replace(" ", "")
    orig_code = _get_airport_code(origin) or origin.replace(" ", "")
    dep = depart_date.replace("-", "")

    if return_date:
        ret = return_date.replace("-", "")
        path = f"{orig_code}/{dest_code}/{dep}/{ret}"
        url = f"https://www.skyscanner.net/transport/flights/{orig_code.lower()}/{dest_code.lower()}/{dep}/{ret}/?adults={adults}&cabinclass=economy"
    else:
        url = f"https://www.skyscanner.net/transport/flights/{orig_code.lower()}/{dest_code.lower()}/{dep}/?adults={adults}&cabinclass=economy"
    return url


def _booking_com_url(
    destination: str,
    checkin: str,
    checkout: str,
    adults: int,
    budget_style: str = "moderate",
) -> str:
    """
    Build a Booking.com search URL.
    No API key — links to public search results.
    """
    params = {
        "ss": destination,
        "checkin": checkin,
        "checkout": checkout,
        "group_adults": adults,
        "no_rooms": max(1, adults // 2),
        "nflt": _budget_style_to_booking_filter(budget_style),
    }
    return "https://www.booking.com/searchresults.html?" + urlencode(
        {k: v for k, v in params.items() if v}
    )


def _hostelworld_url(destination: str, checkin: str, checkout: str, adults: int) -> str:
    params = {
        "search[destination]": destination,
        "search[from_day]": checkin.split("-")[2],
        "search[from_mon]": checkin.split("-")[1],
        "search[from_year]": checkin.split("-")[0],
        "search[to_day]": checkout.split("-")[2],
        "search[to_mon]": checkout.split("-")[1],
        "search[to_year]": checkout.split("-")[0],
        "search[number_of_guests]": adults,
    }
    return "https://www.hostelworld.com/findabed.php?" + urlencode(params)


def _airbnb_url(destination: str, checkin: str, checkout: str, adults: int) -> str:
    params = {
        "query": destination,
        "checkin": checkin,
        "checkout": checkout,
        "adults": adults,
    }
    return "https://www.airbnb.com/s/" + quote(destination) + "/homes?" + urlencode(params)


def _rome2rio_url(origin: str, destination: str) -> str:
    return (
        f"https://www.rome2rio.com/s/{quote(origin.replace(' ', '-'))}"
        f"/{quote(destination.replace(' ', '-'))}"
    )


def _budget_style_to_booking_filter(style: str) -> str:
    """Map budget style to Booking.com star filter."""
    return {"budget": "class%3D1%3Bclass%3D2", "luxury": "class%3D4%3Bclass%3D5"}.get(style, "class%3D3")


def _get_airport_code(destination: str) -> str | None:
    """Return the primary IATA code for a destination, or None."""
    key = destination.strip().casefold()
    codes = DESTINATION_AIRPORTS.get(key)
    if codes:
        return codes[0]
    # Partial match
    for k, v in DESTINATION_AIRPORTS.items():
        if k in key or key in k:
            return v[0]
    return None


# ─── Public API ───────────────────────────────────────────────────────────────

@dataclass
class FlightLinks:
    google_flights: str
    skyscanner: str
    rome2rio: str
    origin: str
    destination: str
    depart_date: str
    return_date: str | None
    adults: int
    airport_code: str | None
    notes: str


@dataclass
class AccommodationLinks:
    booking_com: str
    airbnb: str
    hostelworld: str | None
    destination: str
    checkin: str
    checkout: str
    adults: int
    budget_style: str
    notes: str


@dataclass
class TransportGuidance:
    destination: str
    airport_transfer: str
    local_transport: str
    public_transport: str
    rome2rio_url: str
    notes: str
    source: str = "STATIC"


@dataclass
class LogisticsResponse:
    destination: str
    flights: FlightLinks
    accommodation: AccommodationLinks
    transport: TransportGuidance
    disclaimer: str


def build_logistics(
    destination: str,
    origin: str,
    start_date: str,
    end_date: str,
    travelers: int,
    budget_style: str = "moderate",
    accommodation_preference: str = "hotel",
) -> LogisticsResponse:
    """
    Build a LogisticsResponse with parameterised search links and static guidance.
    No live API calls. Never fabricates prices or availability.
    """
    airport_code = _get_airport_code(destination)
    dest_key = destination.strip().casefold()
    transport = TRANSPORT_GUIDANCE.get(dest_key)

    # Partial match for transport guidance
    if transport is None:
        for k, v in TRANSPORT_GUIDANCE.items():
            if k in dest_key or dest_key in k:
                transport = v
                break

    if transport is None:
        transport = {
            "airport_transfer": "Transfer options vary by destination. Check the airport website or your accommodation for guidance.",
            "local_transport": "Local transport options depend on the destination. Research ahead of travel.",
            "public_transport": "Public transport availability and pricing varies. Check local transit authorities.",
            "notes": "No specific guidance available for this destination.",
        }

    hostelworld = None
    if accommodation_preference in ("hostel", "budget") or budget_style == "budget":
        hostelworld = _hostelworld_url(destination, start_date, end_date, travelers)

    return LogisticsResponse(
        destination=destination,
        flights=FlightLinks(
            google_flights=_google_flights_url(origin or "your city", destination, start_date, end_date, travelers),
            skyscanner=_skyscanner_url(origin or destination, destination, start_date, end_date, travelers),
            rome2rio=_rome2rio_url(origin or "your city", destination),
            origin=origin or "not specified",
            destination=destination,
            depart_date=start_date,
            return_date=end_date,
            adults=travelers,
            airport_code=airport_code,
            notes="These are search links — not bookings. Fares shown are live results from each platform.",
        ),
        accommodation=AccommodationLinks(
            booking_com=_booking_com_url(destination, start_date, end_date, travelers, budget_style),
            airbnb=_airbnb_url(destination, start_date, end_date, travelers),
            hostelworld=hostelworld,
            destination=destination,
            checkin=start_date,
            checkout=end_date,
            adults=travelers,
            budget_style=budget_style,
            notes="These are search links — not reservations. Availability and prices are determined by each platform.",
        ),
        transport=TransportGuidance(
            destination=destination,
            airport_transfer=transport["airport_transfer"],
            local_transport=transport["local_transport"],
            public_transport=transport["public_transport"],
            rome2rio_url=_rome2rio_url(origin or "your city", destination),
            notes=transport["notes"],
            source="STATIC",
        ),
        disclaimer=(
            "All links open public search pages on third-party platforms. "
            "No booking is made automatically. Fares, availability and prices are "
            "determined entirely by those platforms. Transport guidance is "
            "illustrative and does not reflect live schedules or current fares."
        ),
    )
