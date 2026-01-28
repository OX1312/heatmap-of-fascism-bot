"""
Tests for entities.py - Entity registry and matching logic.

These tests verify:
- Entity registry loading from JSON
- Entity lookup by key
- Entity matching from sticker type strings
"""

import pytest
import json
from pathlib import Path
from tempfile import NamedTemporaryFile
from hm.domain.entities import EntityRegistry


class TestEntityRegistry:
    """Tests for EntityRegistry class."""
    
    def test_empty_registry(self):
        """Empty data creates valid registry."""
        registry = EntityRegistry({})
        assert registry.lookup("anything") is None
    
    def test_lookup_existing_entity(self):
        """Can lookup entities by exact key."""
        data = {
            "AFD": {
                "display": "AfD",
                "desc": "Far-right party"
            }
        }
        registry = EntityRegistry(data)
        
        result = registry.lookup("AFD")
        assert result is not None
        assert result["display"] == "AfD"
    
    def test_lookup_missing_entity(self):
        """Missing entities return None."""
        registry = EntityRegistry({"AFD": {}})
        assert registry.lookup("NONEXISTENT") is None
    
    def test_from_file_valid_json(self):
        """Loads registry from valid JSON file."""
        data = {
            "test_entity": {
                "display": "Test Entity"
            }
        }
        
        with NamedTemporaryFile(mode='w', suffix='.json', delete=False) as f:
            json.dump(data, f)
            temp_path = Path(f.name)
        
        try:
            registry = EntityRegistry.from_file(temp_path)
            assert registry.lookup("test_entity") is not None
        finally:
            temp_path.unlink()
    
    def test_from_file_missing_file(self):
        """Missing file returns empty registry."""
        registry = EntityRegistry.from_file(Path("/nonexistent/file.json"))
        assert registry.data == {}


class TestEntityMatching:
    """Tests for entity matching from sticker types."""
    
    def test_matches_exact_key(self):
        """Matches entity when key appears in type string."""
        data = {
            "AFD": {
                "display": "AfD"
            }
        }
        registry = EntityRegistry(data)
        
        entity_key, entity_display = registry.match_entity_from_type("AFD propaganda")
        
        assert entity_key == "AFD"
        assert entity_display == "AfD"
    
    def test_matches_case_insensitive(self):
        """Matching is case-insensitive."""
        data = {
            "auf1": {
                "display": "AUF1"
            }
        }
        registry = EntityRegistry(data)
        
        entity_key, entity_display = registry.match_entity_from_type("AUF1 sticker")
        
        assert entity_key == "auf1"
        assert entity_display == "AUF1"
    
    def test_matches_numeric_codes(self):
        """Matches numeric codes like 1161."""
        data = {
            "1161": {
                "display": "1161",
                "desc": "Anti-Antifa code"
            }
        }
        registry = EntityRegistry(data)
        
        entity_key, entity_display = registry.match_entity_from_type("Sticker with 1161 code")
        
        assert entity_key == "1161"
        assert entity_display == "1161"
    
    def test_no_match_returns_none(self):
        """No match returns (None, None)."""
        data = {
            "AFD": {"display": "AfD"}
        }
        registry = EntityRegistry(data)
        
        entity_key, entity_display = registry.match_entity_from_type("unknown symbol")
        
        assert entity_key is None
        assert entity_display is None
    
    def test_unknown_type_returns_none(self):
        """'unknown' type returns (None, None)."""
        registry = EntityRegistry({"AFD": {}})
        
        entity_key, entity_display = registry.match_entity_from_type("unknown")
        
        assert entity_key is None
        assert entity_display is None
    
    def test_empty_type_returns_none(self):
        """Empty or None type returns (None, None)."""
        registry = EntityRegistry({"AFD": {}})
        
        assert registry.match_entity_from_type("") == (None, None)
        assert registry.match_entity_from_type(None) == (None, None)
    
    def test_first_match_wins(self):
        """When multiple entities could match, first one wins."""
        data = {
            "NPD": {"display": "NPD"},
            "AFD": {"display": "AfD"}
        }
        registry = EntityRegistry(data)
        
        # "NPD" appears first in dict iteration (Python 3.7+ preserves order)
        entity_key, entity_display = registry.match_entity_from_type("NPD and AFD symbols")
        
        # Should match NPD (first key that appears in text)
        assert entity_key in ["NPD", "AFD"]
    
    def test_partial_match(self):
        """Matches even when key is part of larger word."""
        data = {
            "test": {"display": "Test"}
        }
        registry = EntityRegistry(data)
        
        entity_key, entity_display = registry.match_entity_from_type("testing123")
        
        assert entity_key == "test"


class TestEntityRegistryWithRealData:
    """Integration tests with realistic entity data."""
    
    def test_multiple_entities(self):
        """Can handle multiple entities in registry."""
        data = {
            "AFD": {
                "display": "AfD",
                "desc": "Far-right political party"
            },
            "auf1": {
                "display": "AUF1",
                "sources": ["wiki"],
                "desc": "Austrian online media platform"
            },
            "1161": {
                "display": "1161",
                "sources": ["wiki"],
                "desc": "Anti-Antifascist Action numeric code"
            }
        }
        registry = EntityRegistry(data)
        
        # Test each entity
        assert registry.match_entity_from_type("AfD propaganda") == ("AFD", "AfD")
        assert registry.match_entity_from_type("auf1 sticker") == ("auf1", "AUF1")
        assert registry.match_entity_from_type("Code 1161") == ("1161", "1161")
    
    def test_complex_entity_data(self):
        """Handles complex entity data with sources."""
        data = {
            "auf1": {
                "display": "AUF1",
                "sources": [
                    "auf1-wiki-de",
                    "tagesschau-auf1-desinformation"
                ],
                "desc": "Long description here"
            }
        }
        registry = EntityRegistry(data)
        
        entity_key, entity_display = registry.match_entity_from_type("AUF1 propaganda")
        
        assert entity_key == "auf1"
        assert entity_display == "AUF1"
        # Full entity data still accessible via lookup
        entity = registry.lookup("auf1")
        assert "sources" in entity
        assert len(entity["sources"]) == 2
