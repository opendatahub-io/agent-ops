PARCELS = {
    "RC-101": {
        "name": "14 Lantern Lane",
        "bell_route": False,
        "listed_canal": False,
        "protected": True,
        "archived_colour": "rain-blue",
        "habitat": False,
        "existing_footprint_m2": 12,
        "heritage_function": "public evening lantern",
    },
    "RC-102": {
        "name": "6 Bell Yard",
        "bell_route": True,
        "listed_canal": True,
        "protected": False,
        "archived_colour": None,
        "habitat": True,
        "existing_footprint_m2": 20,
        "heritage_function": None,
    },
    "RC-103": {
        "name": "8 Moss Court",
        "bell_route": False,
        "listed_canal": False,
        "protected": False,
        "archived_colour": None,
        "habitat": False,
        "existing_footprint_m2": 18,
        "heritage_function": None,
    },
    "RC-104": {
        "name": "1 Procession Steps",
        "bell_route": True,
        "listed_canal": False,
        "protected": True,
        "archived_colour": "stone-grey",
        "habitat": False,
        "existing_footprint_m2": 24,
        "heritage_function": "ceremonial stair; not everyday access",
    },
}


def lookup(kind, parcel):
    record = PARCELS.get(parcel)
    if not record:
        return {
            "id": f"{kind}:{parcel}",
            "available": False,
            "message": "No authoritative record for this parcel",
        }
    keys = {
        "parcels": ["name", "bell_route", "listed_canal", "existing_footprint_m2"],
        "heritage": ["protected", "archived_colour", "heritage_function"],
        "environment": ["habitat"],
    }
    data = {key: record[key] for key in keys[kind]}
    if kind == "environment":
        data.update(
            habitat_closed_dates=["2027-03-01", "2027-06-30"],
            third_silence_dates=["2027-09-13", "2027-09-19"],
        )
    return {
        "id": f"{kind}:{parcel}",
        "available": True,
        "snapshot": "demo-2026-01",
        **data,
    }
