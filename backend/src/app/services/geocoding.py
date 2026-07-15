"""Non-fatal venue geocoding via Nominatim."""

import asyncio
import logging

from geopy.geocoders import Nominatim


logger = logging.getLogger(__name__)

_geocoder = Nominatim(user_agent="bourdain-brief/1.0")


async def geocode_venue(name: str, city_name: str) -> tuple[float, float] | None:
    """Return coordinates for a venue, or ``None`` when geocoding fails."""

    query = f"{name}, {city_name}"
    try:
        location = await asyncio.to_thread(_geocoder.geocode, query)
        if location is None:
            logger.debug(
                "No geocoding result for venue",
                extra={"venue_name": name, "city_name": city_name},
            )
            return None
        return (location.latitude, location.longitude)
    except Exception as exc:
        logger.debug(
            "Venue geocoding failed",
            extra={
                "venue_name": name,
                "city_name": city_name,
                "error_type": type(exc).__name__,
            },
            exc_info=True,
        )
        return None
