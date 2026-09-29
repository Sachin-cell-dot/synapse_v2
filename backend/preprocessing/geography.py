from __future__ import annotations

import json
import math
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class District:
    district_id: str
    name: str
    division: str | None
    geometry: dict


def _polygons(geometry: dict) -> list:
    geometry_type = geometry.get("type")
    if geometry_type == "Polygon":
        return [geometry["coordinates"]]
    if geometry_type == "MultiPolygon":
        return geometry["coordinates"]
    raise ValueError(f"Unsupported district geometry: {geometry_type}")


def _inside_ring(longitude: float, latitude: float, ring: list) -> bool:
    inside = False
    previous = len(ring) - 1
    for current in range(len(ring)):
        x1, y1 = ring[current][:2]
        x2, y2 = ring[previous][:2]
        if ((y1 > latitude) != (y2 > latitude)) and longitude < (x2 - x1) * (latitude - y1) / ((y2 - y1) or 1e-15) + x1:
            inside = not inside
        previous = current
    return inside


def point_in_geometry(longitude: float, latitude: float, geometry: dict) -> bool:
    for polygon in _polygons(geometry):
        if polygon and _inside_ring(longitude, latitude, polygon[0]) and not any(_inside_ring(longitude, latitude, hole) for hole in polygon[1:]):
            return True
    return False


def _coordinates(geometry: dict) -> list:
    return [point for polygon in _polygons(geometry) for ring in polygon for point in ring]


def geometry_bounds(geometry: dict) -> tuple[float, float, float, float]:
    coordinates = _coordinates(geometry)
    return (
        min(point[0] for point in coordinates),
        min(point[1] for point in coordinates),
        max(point[0] for point in coordinates),
        max(point[1] for point in coordinates),
    )


def load_districts(boundary_path: Path, geography: dict) -> tuple[District, ...]:
    payload = json.loads(boundary_path.read_text(encoding="utf-8"))
    if payload.get("type") != "FeatureCollection" or not payload.get("features"):
        raise ValueError("District boundary must be a non-empty GeoJSON FeatureCollection")
    districts = []
    seen = set()
    for feature in payload["features"]:
        properties = feature.get("properties", {})
        district_id = str(properties[geography["district_id_property"]])
        if district_id in seen:
            raise ValueError(f"Duplicate district id in boundary: {district_id}")
        seen.add(district_id)
        division_property = geography.get("division_property")
        districts.append(District(district_id, str(properties[geography["district_name_property"]]), None if not division_property else properties.get(division_property), feature["geometry"]))
    return tuple(sorted(districts, key=lambda district: district.district_id))


def sample_district(district: District, sampling: dict) -> tuple[tuple[float, float], ...]:
    spacing = float(sampling["grid_spacing_degrees"])
    minimum = int(sampling["minimum_points"])
    maximum = int(sampling["maximum_points"])
    if spacing <= 0 or minimum <= 0 or maximum < minimum:
        raise ValueError("Invalid district sampling configuration")
    coordinates = _coordinates(district.geometry)
    min_lon, max_lon = min(point[0] for point in coordinates), max(point[0] for point in coordinates)
    min_lat, max_lat = min(point[1] for point in coordinates), max(point[1] for point in coordinates)
    points: list[tuple[float, float]] = []
    latitude = math.floor(min_lat / spacing) * spacing + spacing / 2
    while latitude <= max_lat:
        longitude = math.floor(min_lon / spacing) * spacing + spacing / 2
        while longitude <= max_lon:
            if point_in_geometry(longitude, latitude, district.geometry):
                points.append((round(latitude, 6), round(longitude, 6)))
            longitude += spacing
        latitude += spacing
    for divisor in (2, 4, 8, 16):
        if len(points) >= minimum:
            break
        fine = spacing / divisor
        latitude = min_lat + fine / 2
        while latitude <= max_lat and len(points) < minimum:
            longitude = min_lon + fine / 2
            while longitude <= max_lon and len(points) < minimum:
                point = (round(latitude, 6), round(longitude, 6))
                if point not in points and point_in_geometry(longitude, latitude, district.geometry):
                    points.append(point)
                longitude += fine
            latitude += fine
    points = sorted(set(points))
    if len(points) < minimum:
        raise ValueError(f"Could not generate minimum sampling points for district {district.name}")
    if len(points) > maximum:
        indices = [round(index * (len(points) - 1) / (maximum - 1)) for index in range(maximum)] if maximum > 1 else [0]
        points = [points[index] for index in sorted(set(indices))]
    return tuple(points)
