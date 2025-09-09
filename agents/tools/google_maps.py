"""
Google Maps Web Services tool (API key based).

Provides a unified `google_maps` tool and a few specific aliases for common tasks
using Google Maps Platform HTTP APIs (no OAuth required):

- geocode: address → {lat,lng,formatted_address}
- reverse_geocode: lat,lng → address components
- directions: origin → destination (modes: driving, walking, bicycling, transit)
- distance_matrix: origins × destinations → distances and durations
- timezone: lat,lng,timestamp → timezone info
- nearby: find nearby places of a given type/keyword around lat,lng (uses Places Nearby Search)

Environment:
- GOOGLE_MAPS_API_KEY (or MAPS_API_KEY)
- MAPS_TIMEOUT (seconds, default 20)
"""

from __future__ import annotations

import os
import time
from typing import Optional, List, Dict, Any, Type

import requests

try:
    from pydantic import BaseModel, Field
except Exception:  # pragma: no cover
    from pydantic.v1 import BaseModel, Field  # type: ignore

try:
    from langchain.tools import BaseTool
except Exception:  # pragma: no cover
    from langchain.agents import Tool as BaseTool  # type: ignore


API_KEY_ENV_KEYS = ["GOOGLE_MAPS_API_KEY", "MAPS_API_KEY"]
# Minimal set of common Google Places types we support explicitly. If a requested
# type isn't one of these, we will treat it as a keyword and fall back to the
# generic 'store' type for better relevance (e.g., "musical instrument shop").
VALID_PLACE_TYPES: set[str] = {
    "pharmacy",
    "store",
    "electronics_store",
    "book_store",
    "clothing_store",
    "shoe_store",
    "department_store",
    "shopping_mall",
    "home_goods_store",
    "hardware_store",
    "furniture_store",
    "bicycle_store",
    "pet_store",
    "grocery_or_supermarket",
    "convenience_store",
    "supermarket",
}
BASES = {
    "geocode": "https://maps.googleapis.com/maps/api/geocode/json",
    "directions": "https://maps.googleapis.com/maps/api/directions/json",
    "distance_matrix": "https://maps.googleapis.com/maps/api/distancematrix/json",
    "timezone": "https://maps.googleapis.com/maps/api/timezone/json",
    "places_nearby": "https://maps.googleapis.com/maps/api/place/nearbysearch/json",
    "place_details": "https://maps.googleapis.com/maps/api/place/details/json",
}


def _get_api_key() -> Optional[str]:
    for k in API_KEY_ENV_KEYS:
        v = os.getenv(k)
        if v:
            return v
    return None


def _timeout() -> float:
    try:
        return float(os.getenv("MAPS_TIMEOUT", "20"))
    except Exception:
        return 20.0


def _http_get(url: str, params: Dict[str, Any]) -> Dict[str, Any]:
    r = requests.get(url, params=params, timeout=_timeout())
    r.raise_for_status()
    return r.json()


class MapsUnifiedArgs(BaseModel):
    action: str = Field(description="geocode | reverse_geocode | directions | distance_matrix | timezone | nearby")
    # geocode | reverse_geocode
    address: Optional[str] = None
    lat: Optional[float] = Field(default=None, description="Latitude for reverse_geocode/timezone")
    lng: Optional[float] = Field(default=None, description="Longitude for reverse_geocode/timezone")
    # directions
    origin: Optional[str] = None
    destination: Optional[str] = None
    mode: Optional[str] = Field(default="driving", description="driving|walking|bicycling|transit")
    alternatives: Optional[bool] = Field(default=False, description="Return multiple routes if true")
    units: Optional[str] = Field(default="metric", description="metric|imperial")
    language: Optional[str] = None
    departure_time: Optional[int] = Field(default=None, description="Unix timestamp for departure")
    arrival_time: Optional[int] = Field(default=None, description="Unix timestamp for arrival (transit)")
    # distance matrix
    origins: Optional[List[str]] = None
    destinations: Optional[List[str]] = None
    # timezone
    timestamp: Optional[int] = Field(default=None, description="Unix timestamp, default now()")
    # nearby
    nearby_type: Optional[str] = Field(default=None, description="Place type (e.g., pharmacy, restaurant)")
    keyword: Optional[str] = Field(default=None, description="Keyword to match in nearby search")
    radius: Optional[int] = Field(default=None, description="Search radius in meters (required unless rankby=distance)")
    rankby: Optional[str] = Field(default=None, description="Rank results. 'distance' or omit to use radius")
    opennow: Optional[bool] = Field(default=False, description="Filter to places currently open")
    results_limit: Optional[int] = Field(default=5, ge=1, le=20, description="Max places to return (1-20)")


def _fmt(obj: Any) -> str:
    import json
    try:
        return json.dumps(obj, ensure_ascii=False, indent=2)
    except Exception:
        return str(obj)


def _is_latlng_text(s: str) -> bool:
    try:
        parts = [p.strip() for p in s.split(",")]
        if len(parts) != 2:
            return False
        float(parts[0]); float(parts[1])
        return True
    except Exception:
        return False


class GoogleMapsUnifiedTool(BaseTool):
    name: str = "google_maps"
    description: str = (
        "Google Maps: geocode, reverse_geocode, directions, distance_matrix, timezone. "
        "Provide JSON with action and fields. Requires GOOGLE_MAPS_API_KEY."
    )
    args_schema: Type[BaseModel] = MapsUnifiedArgs

    def _run(
        self,
        action: str,
        address: Optional[str] = None,
        lat: Optional[float] = None,
        lng: Optional[float] = None,
        origin: Optional[str] = None,
        destination: Optional[str] = None,
        mode: Optional[str] = "driving",
        alternatives: Optional[bool] = False,
        units: Optional[str] = "metric",
        language: Optional[str] = None,
        departure_time: Optional[int] = None,
        arrival_time: Optional[int] = None,
        origins: Optional[List[str]] = None,
        destinations: Optional[List[str]] = None,
        timestamp: Optional[int] = None,
        nearby_type: Optional[str] = None,
        keyword: Optional[str] = None,
        radius: Optional[int] = None,
        rankby: Optional[str] = None,
        opennow: Optional[bool] = False,
        results_limit: Optional[int] = 5,
    ) -> str:
        key = _get_api_key()
        if not key:
            return (
                "Google Maps tool unavailable: set GOOGLE_MAPS_API_KEY (or MAPS_API_KEY) in env."
            )

        a = (action or "").strip().lower()
        try:
            if a == "geocode":
                if not address:
                    return "Maps geocode failed: missing address"
                data = _http_get(BASES["geocode"], {"address": address, "key": key})
                if (data.get("status") == "OK") and data.get("results"):
                    r = data["results"][0]
                    loc = r["geometry"]["location"]
                    return _fmt(
                        {
                            "formatted_address": r.get("formatted_address"),
                            "lat": loc.get("lat"),
                            "lng": loc.get("lng"),
                            "place_id": r.get("place_id"),
                        }
                    )
                return _fmt({"status": data.get("status"), "error_message": data.get("error_message")})

            if a == "reverse_geocode":
                if lat is None or lng is None:
                    return "Maps reverse_geocode failed: missing lat/lng"
                data = _http_get(BASES["geocode"], {"latlng": f"{lat},{lng}", "key": key})
                if data.get("status") == "OK" and data.get("results"):
                    r = data["results"][0]
                    return _fmt(
                        {
                            "formatted_address": r.get("formatted_address"),
                            "place_id": r.get("place_id"),
                            "types": r.get("types"),
                        }
                    )
                return _fmt({"status": data.get("status"), "error_message": data.get("error_message")})

            if a == "directions":
                if not (origin and destination):
                    return "Maps directions failed: missing origin/destination"
                params: Dict[str, Any] = {
                    "origin": origin,
                    "destination": destination,
                    "mode": mode or "driving",
                    "alternatives": str(bool(alternatives)).lower(),
                    "units": units or "metric",
                    "key": key,
                }
                if language:
                    params["language"] = language
                if departure_time:
                    params["departure_time"] = departure_time
                if arrival_time:
                    params["arrival_time"] = arrival_time
                data = _http_get(BASES["directions"], params)
                if data.get("status") == "OK" and data.get("routes"):
                    route = data["routes"][0]
                    leg = (route.get("legs") or [{}])[0]
                    summary = {
                        "summary": route.get("summary"),
                        "distance": leg.get("distance", {}).get("text"),
                        "duration": leg.get("duration", {}).get("text"),
                        "start_address": leg.get("start_address"),
                        "end_address": leg.get("end_address"),
                    }
                    # include polyline if present
                    overview_poly = route.get("overview_polyline", {}).get("points")
                    if overview_poly:
                        summary["polyline"] = overview_poly
                    return _fmt(summary)
                return _fmt({"status": data.get("status"), "error_message": data.get("error_message")})

            if a == "distance_matrix":
                if not (origins and destinations):
                    return "Maps distance_matrix failed: missing origins/destinations"
                params = {
                    "origins": "|".join(origins),
                    "destinations": "|".join(destinations),
                    "mode": mode or "driving",
                    "units": units or "metric",
                    "key": key,
                }
                data = _http_get(BASES["distance_matrix"], params)
                return _fmt(data)

            if a == "timezone":
                if lat is None or lng is None:
                    return "Maps timezone failed: missing lat/lng"
                ts = timestamp or int(time.time())
                data = _http_get(BASES["timezone"], {"location": f"{lat},{lng}", "timestamp": ts, "key": key})
                return _fmt(data)

            if a == "nearby":
                # Determine coordinates: lat/lng or geocode address
                _lat, _lng = lat, lng
                if _lat is None or _lng is None:
                    if address:
                        g = _http_get(BASES["geocode"], {"address": address, "key": key})
                        if not (g.get("status") == "OK" and g.get("results")):
                            return _fmt({"status": g.get("status"), "error_message": g.get("error_message"), "tip": "Failed to geocode address for nearby search"})
                        loc = g["results"][0]["geometry"]["location"]
                        _lat, _lng = loc.get("lat"), loc.get("lng")
                    else:
                        return "Maps nearby failed: provide lat/lng or address"
                params: Dict[str, Any] = {
                    "location": f"{_lat},{_lng}",
                    "key": key,
                }
                # Rank and radius rules per Google API
                rb = (rankby or "").lower().strip()
                # If provided type isn't a valid Places type, treat it as a keyword and narrow by generic 'store'
                if nearby_type and (nearby_type not in VALID_PLACE_TYPES):
                    # carry over any explicit keyword too
                    keyword = f"{nearby_type} {keyword}".strip() if keyword else nearby_type
                    nearby_type = "store"
                if rb == "distance":
                    params["rankby"] = "distance"
                    # When rankby=distance, radius must be omitted and one of keyword|type must be present
                    if keyword:
                        params["keyword"] = keyword
                    if nearby_type:
                        params["type"] = nearby_type
                    if not (keyword or nearby_type):
                        return "Maps nearby failed: when rankby=distance, provide keyword or nearby_type"
                else:
                    # Use radius search (default 1500m if not provided)
                    params["radius"] = int(radius or 1500)
                    if keyword:
                        params["keyword"] = keyword
                    if nearby_type:
                        params["type"] = nearby_type
                if opennow:
                    params["opennow"] = "true"
                if language:
                    params["language"] = language
                data = _http_get(BASES["places_nearby"], params)
                if data.get("status") != "OK":
                    return _fmt({"status": data.get("status"), "error_message": data.get("error_message")})
                results = data.get("results", [])[: int(results_limit or 5)]
                slim = []
                for r in results:
                    slim.append(
                        {
                            "name": r.get("name"),
                            "place_id": r.get("place_id"),
                            "rating": r.get("rating"),
                            "user_ratings_total": r.get("user_ratings_total"),
                            "vicinity": r.get("vicinity") or r.get("formatted_address"),
                            "types": r.get("types"),
                            "open_now": r.get("opening_hours", {}).get("open_now") if r.get("opening_hours") else None,
                            "lat": r.get("geometry", {}).get("location", {}).get("lat"),
                            "lng": r.get("geometry", {}).get("location", {}).get("lng"),
                        }
                    )
                return _fmt({"count": len(slim), "results": slim})

            return "Maps tool failed: unknown action (use geocode|reverse_geocode|directions|distance_matrix|timezone|nearby)"
        except requests.HTTPError as e:
            try:
                return _fmt({"http_error": str(e), "response": e.response.json()})
            except Exception:
                return f"HTTP error: {e}"
        except Exception as e:  # pragma: no cover
            return f"Maps error: {e}"


def create_google_maps_tools() -> List[BaseTool]:
    """Return tools including a unified 'google_maps' tool plus convenience aliases."""
    unified = GoogleMapsUnifiedTool()
    tools: List[BaseTool] = [unified]
    try:
        # Build light wrappers as alias Tools if langchain.agents.Tool available
        from langchain.agents import Tool as CoreTool  # type: ignore

        tools.extend(
            [
                CoreTool(
                    name="maps_geocode",
                    description="Geocode an address via Google Maps (returns lat/lng)",
                    func=lambda addr: unified._run(action="geocode", address=str(addr)),
                ),
                CoreTool(
                    name="maps_directions",
                    description="Get directions summary (distance/duration) between two places",
                    func=lambda s: unified._run(action="directions", **_parse_pipe_args(s)),
                ),
                CoreTool(
                    name="maps_distance_matrix",
                    description="Distance matrix between origins and destinations",
                    func=lambda s: unified._run(action="distance_matrix", **_parse_pipe_args(s)),
                ),
                CoreTool(
                    name="maps_nearby",
                    description="Find nearby places around a location. Input: 'address|type|radius?' or 'lat,lng|type|radius?'.",
                    func=lambda s: unified._run(action="nearby", **_parse_nearby_args(s)),
                ),
            ]
        )
    except Exception:
        pass
    return tools


def _parse_pipe_args(s: str) -> Dict[str, Any]:
    """Parse simple 'a|b' or 'origin|destination|mode' strings into kwargs for convenience aliases."""
    if not isinstance(s, str):
        return {}
    parts = [p.strip() for p in s.split("|")]
    out: Dict[str, Any] = {}
    if len(parts) >= 2:
        out["origin"] = parts[0]
        out["destination"] = parts[1]
    if len(parts) >= 3:
        out["mode"] = parts[2]
    return out


def _parse_nearby_args(s: str) -> Dict[str, Any]:
    """Parse 'address|type|radius?' or 'lat,lng|type|radius?' for maps_nearby."""
    if not isinstance(s, str):
        return {}
    parts = [p.strip() for p in s.split("|")]
    out: Dict[str, Any] = {"rankby": "distance"}
    if not parts:
        return out
    loc = parts[0]
    if _is_latlng_text(loc):
        try:
            lat_s, lng_s = [p.strip() for p in loc.split(",")]
            out["lat"] = float(lat_s)
            out["lng"] = float(lng_s)
        except Exception:
            pass
    else:
        out["address"] = loc
    if len(parts) >= 2 and parts[1]:
        out["nearby_type"] = parts[1]
    if len(parts) >= 3 and parts[2]:
        try:
            out["radius"] = int(parts[2])
            out["rankby"] = None  # radius search when radius provided
        except Exception:
            pass
    return out


# Export
maps_tools = create_google_maps_tools()
google_maps_tool = maps_tools[0]

__all__ = ["google_maps_tool", "maps_tools", "create_google_maps_tools"]
