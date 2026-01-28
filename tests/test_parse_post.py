"""
Tests for parse_post.py - Parsing logic for Mastodon posts.

These tests verify:
- Location parsing (coordinates, streets, intersections)
- Type parsing (sticker vs graffiti, conflict detection)
- Note extraction
- HTML stripping
"""

import pytest
from hm.domain.parse_post import (
    strip_html,
    parse_location,
    parse_type_and_medium,
    parse_note,
    has_image,
    normalize_location_line
)
from hm.core.models import Kind


class TestStripHtml:
    """Tests for HTML stripping from Mastodon content."""
    
    def test_strips_basic_html(self):
        html = "<p>Hello</p><p>World</p>"
        assert strip_html(html) == "Hello\nWorld"
    
    def test_strips_br_tags(self):
        html = "Line1<br>Line2<br />Line3"
        assert strip_html(html) == "Line1\nLine2\nLine3"
    
    def test_removes_links(self):
        html = '<p><a href="https://example.com">Link</a></p>'
        assert strip_html(html) == "Link"
    
    def test_normalizes_whitespace(self):
        html = "<p>Too    many     spaces</p>"
        assert strip_html(html) == "Too many spaces"
    
    def test_empty_string(self):
        assert strip_html("") == ""
        assert strip_html(None) == ""


class TestParseLocation:
    """Tests for location parsing from post content."""
    
    def test_parses_decimal_coordinates(self):
        text = "Found at 52.5200, 13.4050"
        coords, query = parse_location(text)
        assert coords == (52.5200, 13.4050)
        assert query is None
    
    def test_parses_negative_coordinates(self):
        text = "Location: -34.6037, -58.3816"
        coords, query = parse_location(text)
        assert coords == (-34.6037, -58.3816)
    
    def test_parses_street_number_city(self):
        text = "Hauptstraße 42, Berlin"
        coords, query = parse_location(text)
        assert coords is None
        assert "Hauptstraße 42, Berlin" in query
    
    def test_parses_street_city(self):
        text = "Linienstraße, Berlin"
        coords, query = parse_location(text)
        assert coords is None
        assert "Linienstraße, Berlin" in query
    
    def test_parses_intersection(self):
        text = "Kantstraße / Wilmersdorfer Straße, Berlin"
        coords, query = parse_location(text)
        assert coords is None
        assert "intersection of" in query.lower()
        assert "Kantstraße" in query
        assert "Wilmersdorfer" in query
    
    def test_ignores_hashtag_lines(self):
        text = "#sticker_report\nPotsdamer Platz, Berlin"
        coords, query = parse_location(text)
        assert coords is None
        assert "Potsdamer Platz, Berlin" in query
    
    def test_no_location_found(self):
        text = "#sticker_report\n@HeatmapofFascism"
        coords, query = parse_location(text)
        assert coords is None
        assert query is None


class TestParseTypeAndMedium:
    """Tests for sticker/graffiti type parsing."""
    
    def test_parses_sticker_type(self):
        text = "#sticker_type: NPD propaganda"
        medium, type_val, error = parse_type_and_medium(text)
        assert medium == Kind.STICKER
        assert type_val == "NPD propaganda"
        assert error is None
    
    def test_parses_graffiti_type(self):
        text = "#graffiti_type: Anti-Antifa 1161"
        medium, type_val, error = parse_type_and_medium(text)
        assert medium == Kind.GRAFFITI
        assert type_val == "Anti-Antifa 1161"
        assert error is None
    
    def test_handles_typo_grafitti(self):
        """'grafitti' (with 3 t's) should be normalized to 'graffiti'."""
        text = "#grafitti_type: Some text"
        medium, type_val, error = parse_type_and_medium(text)
        assert medium == Kind.GRAFFITI
        assert type_val == "Some text"
    
    def test_detects_conflict(self):
        """Both sticker_type and graffiti_type in same post = conflict."""
        text = "#sticker_type: A\n#graffiti_type: B"
        medium, type_val, error = parse_type_and_medium(text)
        assert medium is None
        assert type_val == "unknown"
        assert error == "conflict"
    
    def test_no_type_returns_unknown(self):
        text = "Just a normal post"
        medium, type_val, error = parse_type_and_medium(text)
        assert medium is None
        assert type_val == "unknown"
        assert error is None
    
    def test_type_with_colon_separator(self):
        text = "#sticker_typ: III. Weg"  # German variant
        medium, type_val, error = parse_type_and_medium(text)
        assert medium == Kind.STICKER
        assert type_val == "III. Weg"


class TestParseNote:
    """Tests for note extraction from posts."""
    
    def test_extracts_note(self):
        text = "#note: Found near school"
        note = parse_note(text)
        assert note == "Found near school"
    
    def test_stops_at_next_hashtag(self):
        text = "#note: Some text #sticker_report"
        note = parse_note(text)
        assert note == "Some text"
    
    def test_truncates_long_notes(self):
        long_note = "x" * 600
        text = f"#note: {long_note}"
        note = parse_note(text)
        assert len(note) == 500
    
    def test_no_note_returns_empty(self):
        text = "#sticker_report"
        note = parse_note(text)
        assert note == ""


class TestHasImage:
    """Tests for image attachment detection."""
    
    def test_detects_image_attachment(self):
        attachments = [
            {"type": "image", "url": "https://example.com/image.jpg"}
        ]
        assert has_image(attachments) is True
    
    def test_ignores_video_attachment(self):
        attachments = [
            {"type": "video", "url": "https://example.com/video.mp4"}
        ]
        assert has_image(attachments) is False
    
    def test_empty_attachments(self):
        assert has_image([]) is False
        assert has_image(None) is False


class TestNormalizeLocationLine:
    """Tests for location string normalization."""
    
    def test_strips_location_prefix(self):
        assert normalize_location_line("Ort: Hauptstraße") == "Hauptstraße"
        assert normalize_location_line("location: Main St") == "Main St"
        assert normalize_location_line("place: Center") == "Center"
    
    def test_expands_str_abbreviation(self):
        assert "straße" in normalize_location_line("Hauptstr. 5").lower()
        assert "straße" in normalize_location_line("Kantstr 10").lower()
    
    def test_normalizes_whitespace(self):
        assert normalize_location_line("Too    many    spaces") == "Too many spaces"
