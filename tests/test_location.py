"""
Tests for location.py - Geocoding and location snapping logic.

These tests verify:
- Coordinate projection math (_xy_m, _latlon_from_xy)
- Polyline nearest point calculation
- Geocoding (with mocked Nominatim API)
- Location snapping (with mocked Overpass API)
"""

import pytest
import math
from unittest.mock import Mock, patch
from hm.domain.location import (
    _xy_m,
    _latlon_from_xy,
    _nearest_point_on_polyline_m,
    geocode_nominatim,
    geocode_query_worldwide,
    snap_to_public_way
)


class TestCoordinateProjection:
    """Tests for equirectangular projection helpers."""
    
    def test_xy_m_at_origin(self):
        """Point at origin should map to (0, 0)."""
        x, y = _xy_m(52.5, 13.4, 52.5, 13.4)
        assert abs(x) < 0.01
        assert abs(y) < 0.01
    
    def test_xy_m_north(self):
        """Moving north increases y."""
        x, y = _xy_m(52.5, 13.4, 52.51, 13.4)
        assert abs(x) < 1  # No east-west movement
        assert y > 1000  # ~1.1km north
    
    def test_xy_m_east(self):
        """Moving east increases x."""
        x, y = _xy_m(52.5, 13.4, 52.5, 13.41)
        assert x > 500  # Significant east movement
        assert abs(y) < 1  # No north-south movement
    
    def test_latlon_from_xy_roundtrip(self):
        """Converting lat/lon -> xy -> lat/lon should be identity."""
        lat0, lon0 = 52.5200, 13.4050
        lat1, lon1 = 52.5250, 13.4100
        
        x, y = _xy_m(lat0, lon0, lat1, lon1)
        lat2, lon2 = _latlon_from_xy(lat0, lon0, x, y)
        
        assert abs(lat2 - lat1) < 0.0001
        assert abs(lon2 - lon1) < 0.0001


class TestNearestPointOnPolyline:
    """Tests for nearest point projection onto polylines."""
    
    def test_simple_segment_midpoint(self):
        """Query point projects onto segment midpoint."""
        lat0, lon0 = 52.5, 13.4
        pts = [(52.5, 13.4), (52.5, 13.41)]  # East-west line
        qlat, qlon = 52.501, 13.405  # North of midpoint
        
        plat, plon, dist, (ux, uy) = _nearest_point_on_polyline_m(lat0, lon0, pts, qlat, qlon)
        
        # Should project onto the line
        assert abs(plat - 52.5) < 0.0001  # Same latitude as line
        assert 13.4 < plon < 13.41  # Somewhere along line
        assert dist < 200  # Within ~100m
    
    def test_endpoint_projection(self):
        """Query past segment end projects to endpoint."""
        lat0, lon0 = 52.5, 13.4
        pts = [(52.5, 13.4), (52.5, 13.401)]
        qlat, qlon = 52.5, 13.405  # Far east of segment
        
        plat, plon, dist, _ = _nearest_point_on_polyline_m(lat0, lon0, pts, qlat, qlon)
        
        # Should clamp to end of segment
        assert abs(plon - 13.401) < 0.0001
    
    def test_multi_segment_polyline(self):
        """Finds nearest point across multiple segments."""
        lat0, lon0 = 52.5, 13.4
        pts = [
            (52.5, 13.4),
            (52.5, 13.41),
            (52.51, 13.41)
        ]
        qlat, qlon = 52.505, 13.41  # Near second segment
        
        plat, plon, dist, _ = _nearest_point_on_polyline_m(lat0, lon0, pts, qlat, qlon)
        
        # Should project onto second segment
        assert abs(plon - 13.41) < 0.0001
        assert 52.5 < plat < 52.51


class TestGeocodingNominatim:
    """Tests for Nominatim geocoding (mocked API)."""
    
    @patch('hm.domain.location.requests.get')
    def test_successful_geocode(self, mock_get):
        """Successful geocoding returns coordinates."""
        mock_response = Mock()
        mock_response.status_code = 200
        mock_response.json.return_value = [
            {"lat": "52.5200", "lon": "13.4050"}
        ]
        mock_get.return_value = mock_response
        
        result = geocode_nominatim("Berlin, Germany", "TestAgent")
        
        assert result == (52.5200, 13.4050)
        mock_get.assert_called_once()
    
    @patch('hm.domain.location.requests.get')
    def test_no_results_returns_none(self, mock_get):
        """No results from Nominatim returns None."""
        mock_response = Mock()
        mock_response.status_code = 200
        mock_response.json.return_value = []
        mock_get.return_value = mock_response
        
        result = geocode_nominatim("NonexistentPlace123", "TestAgent")
        
        assert result is None
    
    @patch('hm.domain.location.requests.get')
    def test_api_error_returns_none(self, mock_get):
        """API errors return None gracefully."""
        mock_get.side_effect = Exception("Network error")
        
        result = geocode_nominatim("Berlin", "TestAgent")
        
        assert result is None


class TestGeocodeQueryWorldwide:
    """Tests for worldwide geocoding with fallbacks."""
    
    @patch('hm.domain.location.geocode_nominatim')
    def test_uses_nominatim_first(self, mock_nominatim):
        """Tries Nominatim first."""
        mock_nominatim.return_value = (52.5, 13.4)
        
        coords, method = geocode_query_worldwide("Berlin", "TestAgent")
        
        assert coords == (52.5, 13.4)
        assert method == "nominatim"
    
    @patch('hm.domain.location.geocode_nominatim')
    def test_nominatim_failure_returns_none(self, mock_nominatim):
        """If Nominatim fails, returns None."""
        mock_nominatim.return_value = None
        
        coords, method = geocode_query_worldwide("UnknownPlace", "TestAgent")
        
        assert coords is None
        assert method == "none"


class TestSnapToPublicWay:
    """Tests for location snapping to OSM ways (heavily mocked)."""
    
    @patch('hm.domain.location._overpass_post')
    def test_no_ways_returns_original(self, mock_overpass):
        """If no OSM ways found, returns original coordinates."""
        mock_overpass.return_value = {"elements": []}
        
        lat, lon, note = snap_to_public_way(52.5, 13.4, "TestAgent")
        
        assert lat == 52.5
        assert lon == 13.4
        assert note == ""
    
    @patch('hm.domain.location._overpass_post')
    def test_snaps_to_poi(self, mock_overpass):
        """Prefers POI (bench) over ways."""
        # First call: POI query - return a bench
        # Second call: building check - no building
        mock_overpass.side_effect = [
            {
                "elements": [{
                    "type": "node",
                    "lat": 52.5001,
                    "lon": 13.4001,
                    "tags": {"leisure": "bench"}
                }]
            },
            {"elements": []}  # No building nearby
        ]
        
        lat, lon, note = snap_to_public_way(52.5, 13.4, "TestAgent")
        
        assert abs(lat - 52.5001) < 0.0001
        assert abs(lon - 13.4001) < 0.0001
        assert "snap_poi:bench" in note
    
    @patch('hm.domain.location._overpass_post')
    def test_snaps_to_footway(self, mock_overpass):
        """Snaps to footway when no POI found."""
        # Mock responses: no POIs, then highway ways
        mock_overpass.side_effect = [
            {"elements": []},  # No POIs
            {  # Highway query - footway
                "elements": [{
                    "type": "way",
                    "tags": {"highway": "footway"},
                    "geometry": [
                        {"lat": 52.5, "lon": 13.4},
                        {"lat": 52.5, "lon": 13.41}
                    ]
                }]
            },
            {"elements": []}  # Building check
        ]
        
        lat, lon, note = snap_to_public_way(52.50005, 13.405, "TestAgent")
        
        # Should snap close to the footway
        assert abs(lat - 52.5) < 0.001  # Near the footway
        assert 13.4 < lon < 13.41
        assert "snap_walk:footway" in note
    
    @patch('hm.domain.location._overpass_post')
    def test_filters_private_ways(self, mock_overpass):
        """Filters out private ways (access=private)."""
        mock_overpass.side_effect = [
            {"elements": []},  # No POIs
            {  # Highway query - private driveway
                "elements": [{
                    "type": "way",
                    "tags": {
                        "highway": "service",
                        "service": "driveway",
                        "access": "private"
                    },
                    "geometry": [
                        {"lat": 52.5, "lon": 13.4},
                        {"lat": 52.5, "lon": 13.401}
                    ]
                }]
            }
        ]
        
        lat, lon, note = snap_to_public_way(52.5, 13.4, "TestAgent")
        
        # Should not snap to private way, return original
        assert lat == 52.5
        assert lon == 13.4
        assert note == ""


class TestHaversineDistance:
    """Tests for haversine distance calculation (imported from dedup)."""
    
    def test_same_point_zero_distance(self):
        """Same coordinates should have zero distance."""
        from hm.domain.dedup import haversine_m
        dist = haversine_m(52.5, 13.4, 52.5, 13.4)
        assert abs(dist) < 0.01
    
    def test_known_distance(self):
        """Test with known distance (Berlin Brandenburg Gate to Reichstag ~1km)."""
        from hm.domain.dedup import haversine_m
        # Approximate coordinates
        dist = haversine_m(52.5163, 13.3777, 52.5186, 13.3761)
        assert 200 < dist < 400  # ~300m
