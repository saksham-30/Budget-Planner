"""
Static helpers that mirror frontend destination data for use in backend prompts.
Only includes attraction names — not live data, prices, or availability.
"""

from __future__ import annotations

_ATTRACTION_MAP: dict[str, list[str]] = {
    "goa": [
        "Calangute Beach", "Basilica of Bom Jesus", "Dudhsagar Falls",
        "Anjuna Flea Market", "Palolem Beach", "Fort Aguada",
    ],
    "paris": [
        "Eiffel Tower", "Louvre Museum", "Notre-Dame Cathedral",
        "Montmartre & Sacré-Cœur", "Musée d'Orsay", "Palace of Versailles",
    ],
    "tokyo": [
        "Senso-ji Temple", "Shibuya Crossing", "Tsukiji Outer Market",
        "teamLab Borderless", "Meiji Shrine", "Shinjuku Gyoen",
    ],
    "bali": [
        "Tegallalang Rice Terraces", "Tanah Lot Temple", "Mount Batur",
        "Uluwatu Temple", "Sacred Monkey Forest", "Seminyak Beach",
    ],
    "manali": [
        "Rohtang Pass", "Solang Valley", "Hadimba Temple",
        "Beas River Rafting", "Old Manali", "Vashisht Hot Springs",
    ],
    "new york": [
        "Central Park", "Metropolitan Museum of Art", "Brooklyn Bridge",
        "High Line", "Statue of Liberty", "Times Square",
    ],
    "rome": [
        "Colosseum & Roman Forum", "Vatican Museums & Sistine Chapel",
        "Trevi Fountain", "Pantheon", "Borghese Gallery", "Trastevere",
    ],
    "bangkok": [
        "Grand Palace", "Wat Pho", "Chatuchak Weekend Market",
        "Khao San Road", "Chao Phraya River", "Floating Markets",
    ],
    "santorini": [
        "Oia Sunset", "Caldera Views", "Red Beach",
        "Ancient Akrotiri", "Wine Tasting", "Fira Old Town",
    ],
    "amsterdam": [
        "Rijksmuseum", "Van Gogh Museum", "Anne Frank House",
        "Canal Ring", "Jordaan", "Keukenhof Gardens",
    ],
    "maldives": [
        "Overwater Bungalows", "Coral Reef Snorkelling",
        "Whale Shark Diving", "Bioluminescent Beach",
        "Local Island Hopping", "Sunset Dolphin Cruise",
    ],
    "dubai": [
        "Burj Khalifa", "Dubai Mall & Fountain", "Desert Safari",
        "Al Fahidi Historic District", "Palm Jumeirah", "Dubai Creek Souks",
    ],
    "kerala": [
        "Alleppey Backwaters", "Munnar Tea Gardens", "Periyar Wildlife Sanctuary",
        "Varkala Cliff Beach", "Fort Kochi", "Wayanad Waterfalls",
    ],
    "prague": [
        "Prague Castle", "Charles Bridge", "Old Town Square & Astronomical Clock",
        "Josefov Jewish Quarter", "Vinohrady Neighbourhood", "Czech Beer Culture",
    ],
    "singapore": [
        "Gardens by the Bay", "Marina Bay Sands SkyPark", "Hawker Centre Food Trail",
        "Sentosa Island", "Chinatown & Little India", "Singapore Zoo",
    ],
    "barcelona": [
        "Sagrada Família", "Park Güell", "Gothic Quarter",
        "La Boqueria Market", "Barceloneta Beach", "Casa Batlló",
    ],
    "kyoto": [
        "Fushimi Inari Shrine", "Arashiyama Bamboo Grove", "Kinkaku-ji (Golden Pavilion)",
        "Gion District", "Nishiki Market", "Philosopher's Path",
    ],
    "istanbul": [
        "Hagia Sophia", "Blue Mosque", "Grand Bazaar",
        "Topkapı Palace", "Bosphorus Cruise", "Spice Bazaar",
    ],
    "jaipur": [
        "Amber Fort", "Hawa Mahal", "City Palace",
        "Nahargarh Fort", "Jantar Mantar", "Johari Bazaar",
    ],
    "queenstown": [
        "AJ Hackett Bungy", "Milford Sound", "The Remarkables",
        "Shotover Jet", "Lake Wakatipu", "Arrowtown",
    ],
    "marrakech": [
        "Djemaa el-Fna", "Medina Souks", "Bahia Palace",
        "Majorelle Garden", "Saadian Tombs", "Atlas Mountains",
    ],
    "phuket": [
        "Phang Nga Bay", "Phi Phi Islands", "Phuket Old Town",
        "Patong Beach", "Big Buddha", "Surin and Kamala Beaches",
    ],
}


def destination_attractions(destination: str) -> list[str]:
    """Return known attraction names for a destination, or [] if unknown."""
    key = destination.strip().casefold()
    # Exact match
    if key in _ATTRACTION_MAP:
        return list(_ATTRACTION_MAP[key])
    # Partial match — e.g. "Goa, India" → "goa"
    for map_key, attractions in _ATTRACTION_MAP.items():
        if map_key in key or key in map_key:
            return list(attractions)
    return []
