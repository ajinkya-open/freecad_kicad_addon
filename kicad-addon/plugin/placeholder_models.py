from __future__ import annotations

import math
import os
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Mapping, Optional

from kicad_compat import KICAD10_COMPAT, get_kicad_compat
from kicad_paths import (
    find_kikakuka_3d_model,
    path_variables as _shared_path_variables,
    resolve_model_path,
    to_kicad_model_uri,
)


# Retained as the public KiCad 10 constant used by existing tests and boards.
# Runtime code selects the value from the connected system KiCad version.
PLACEHOLDER_MODEL = KICAD10_COMPAT.placeholder_model_uri

_LENGTH_RE = re.compile(
    r"\s*([+-]?(?:\d+(?:\.\d*)?|\.\d+)(?:[eE][+-]?\d+)?)"
    r"(?:\s*(mm|cm|in|mil|um|µm))?\s*",
    re.IGNORECASE,
)


@dataclass
class ScanResult:
    scanned: int = 0
    already_valid: int = 0
    added: int = 0
    updated: int = 0
    missing_size: int = 0
    invalid_size: int = 0
    details: list[str] = field(default_factory=list)


def parse_length_mm(value, quantity_name="length") -> float:
    """Parse a positive length value, treating missing suffix as mm."""
    if value is None or not str(value).strip():
        raise ValueError(f"missing {quantity_name}; expected mm, cm, in, mil, or um")

    match = _LENGTH_RE.fullmatch(str(value).strip())
    if match is None:
        raise ValueError(
            f"invalid {quantity_name} value {value!r}; "
            "expected mm, cm, in, mil, or um"
        )

    result = float(match.group(1))
    unit = (match.group(2) or "mm").lower()
    if unit == "cm":
        result *= 10.0
    elif unit == "in":
        result *= 25.4
    elif unit == "mil":
        result *= 0.0254
    elif unit in ("um", "µm"):
        result *= 0.001

    if not math.isfinite(result) or result <= 0:
        raise ValueError(f"{quantity_name} must be greater than zero")
    return result


def parse_signed_length_mm(value, quantity_name="offset") -> float:
    """Parse an optional signed length, default 0.0."""
    if value is None or not str(value).strip():
        return 0.0
    match = _LENGTH_RE.fullmatch(str(value).strip())
    if match is None:
        raise ValueError(
            f"invalid {quantity_name} value {value!r}; "
            "expected mm, cm, in, mil, or um"
        )
    result = float(match.group(1))
    unit = (match.group(2) or "mm").lower()
    if unit == "cm":
        result *= 10.0
    elif unit == "in":
        result *= 25.4
    elif unit == "mil":
        result *= 0.0254
    elif unit in ("um", "µm"):
        result *= 0.001
    if not math.isfinite(result):
        raise ValueError(f"{quantity_name} must be finite")
    return result


def _field_text(field) -> Optional[str]:
    try:
        return field.text.value
    except Exception:
        try:
            return field.text.text.value
        except Exception:
            return None


def footprint_properties(footprint) -> dict[str, str]:
    """Return the footprint's named fields as a property dictionary."""
    try:
        fields = footprint.texts_and_fields
    except Exception:
        try:
            fields = footprint.definition.texts
        except Exception:
            fields = []

    result = {}
    for item in fields:
        try:
            name = item.name
        except Exception:
            continue
        value = _field_text(item)
        if name and value is not None:
            result[name] = value
    return result


def get_normalized_property(properties: Mapping[str, str], *keys: str) -> Optional[str]:
    """Look up a property ignoring case, underscores, spaces, and hyphens."""
    normalized_map = {
        re.sub(r"[\s_\-]+", "", k).lower(): v
        for k, v in properties.items()
        if v is not None and str(v).strip()
    }
    for key in keys:
        norm_key = re.sub(r"[\s_\-]+", "", key).lower()
        if norm_key in normalized_map:
            return normalized_map[norm_key]
    return None


def footprint_label(footprint) -> str:
    ref = ""
    val = ""
    try:
        ref = footprint.reference_field.text.value.strip()
    except Exception:
        pass
    try:
        val = footprint.definition.id.name.strip()
    except Exception:
        try:
            val = footprint.value_field.text.value.strip()
        except Exception:
            pass

    if ref and ref not in ("REF**", ""):
        return f"{ref} ({val})" if val else ref
    if val:
        return f"{val} ({ref})" if ref else val
    return "<unnamed footprint>"


def path_variables(kicad, board) -> dict[str, str]:
    return _shared_path_variables(kicad, board, source_path=__file__)


def is_placeholder_model_filename(filename: str) -> bool:
    """Check if a filename references Kikakuka's unit cube placeholder."""
    norm = str(filename or "").strip().replace("\\", "/").lower()
    return (
        norm.endswith("kikakuka.3dshapes/unit-cube.step")
        or norm.endswith("kikakuka.3dshapes/unit-cube.stp")
        or norm.endswith("/unit-cube.step")
        or norm.endswith("/unit-cube.stp")
        or norm == "unit-cube.step"
        or norm == "unit-cube.stp"
        or norm.endswith("unit_cube.step")
        or norm.endswith("unit_cube.stp")
    )


def has_valid_model(footprint, board, variables: Mapping[str, str]) -> bool:
    """Return True if the footprint has a valid NON-PLACEHOLDER 3D model."""
    for model in footprint.definition.models:
        filename = str(model.filename or "").strip()
        if not filename:
            continue
        # Kikakuka placeholders are managed by this plugin, not real models
        if is_placeholder_model_filename(filename):
            continue
        if filename.lower().startswith("kicad-embed://"):
            return True
        if resolve_model_path(filename, board, variables) is not None:
            return True
    return False


def _new_placeholder_model(
    size_x: float,
    size_y: float,
    size_z: float,
    compatibility=KICAD10_COMPAT,
    model_uri: Optional[str] = None,
    offset_x: float = 0.0,
    offset_y: float = 0.0,
    offset_z: float = 0.0,
):
    from kipy.board_types import Footprint3DModel
    from kipy.geometry import Vector3D

    model = Footprint3DModel()
    model.filename = model_uri or compatibility.placeholder_model_uri
    model.scale = Vector3D.from_xyz(size_x, size_y, size_z)
    model.offset = Vector3D.from_xyz(offset_x, offset_y, offset_z)
    model.visible = True
    compatibility.set_model_opacity(model, 1.0)
    return model


def _replace_models_with_placeholder(
    footprint,
    size_x: float,
    size_y: float,
    size_z: float,
    compatibility=KICAD10_COMPAT,
    model_uri: Optional[str] = None,
    offset_x: float = 0.0,
    offset_y: float = 0.0,
    offset_z: float = 0.0,
):
    from kipy.board_types import Footprint3DModel

    footprint.definition.items = [
        item for item in footprint.definition.items
        if not (
            isinstance(item, Footprint3DModel)
            and is_placeholder_model_filename(getattr(item, "filename", ""))
        )
    ]
    new_model = _new_placeholder_model(
        size_x,
        size_y,
        size_z,
        compatibility=compatibility,
        model_uri=model_uri,
        offset_x=offset_x,
        offset_y=offset_y,
        offset_z=offset_z,
    )
    footprint.definition.add_item(new_model)


def add_placeholder_models(kicad, board) -> ScanResult:
    """Scan the board and add/update scaled unit cubes in one undoable operation."""
    compatibility = get_kicad_compat(kicad.get_version())
    variables = path_variables(kicad, board)

    # 1. Resolve unit-cube.step location
    placeholder_model = compatibility.placeholder_model_uri
    placeholder_path = resolve_model_path(placeholder_model, board, variables)
    if placeholder_path is None:
        placeholder_path = find_kikakuka_3d_model("unit-cube.step", board=board, variables=variables)

    if placeholder_path is None:
        raise FileNotFoundError(
            "Kikakuka unit-cube.step was not found. Please ensure the Kikakuka 3D model "
            "library (com.github.buganini.kikakuka-footprints) is installed."
        )

    # Clean KiCad URI for storing in footprint
    placeholder_uri = to_kicad_model_uri(placeholder_path, variables, compatibility)

    result = ScanResult()
    updates = []
    for footprint in board.get_footprints():
        result.scanned += 1
        label = footprint_label(footprint)

        # Skip footprints that have a valid non-placeholder 3D model
        if has_valid_model(footprint, board, variables):
            result.already_valid += 1
            continue

        properties = footprint_properties(footprint)
        val_x = get_normalized_property(properties, "SizeX", "Size_X", "Size-X", "XSize", "Width")
        val_y = get_normalized_property(properties, "SizeY", "Size_Y", "Size-Y", "YSize", "Length")
        val_z = get_normalized_property(properties, "SizeZ", "Size_Z", "Size-Z", "ZSize", "Height")

        if not val_x or not val_y or not val_z:
            result.missing_size += 1
            continue

        try:
            size_x = parse_length_mm(val_x, "SizeX")
            size_y = parse_length_mm(val_y, "SizeY")
            size_z = parse_length_mm(val_z, "SizeZ")
        except ValueError as error:
            result.invalid_size += 1
            result.details.append(f"{label}: {error}")
            continue

        # Look for explicit offsets or preserve existing placeholder offset
        off_x = parse_signed_length_mm(get_normalized_property(properties, "OffsetX", "SizeOffsetX"), "OffsetX")
        off_y = parse_signed_length_mm(get_normalized_property(properties, "OffsetY", "SizeOffsetY"), "OffsetY")
        off_z = parse_signed_length_mm(get_normalized_property(properties, "OffsetZ", "SizeOffsetZ", "Z"), "OffsetZ")

        # Check existing placeholder models on this footprint
        existing_placeholders = [
            m for m in footprint.definition.models
            if is_placeholder_model_filename(getattr(m, "filename", ""))
        ]

        if existing_placeholders and not any(
            get_normalized_property(properties, k) for k in ("OffsetX", "OffsetY", "OffsetZ", "Z")
        ):
            # Preserve existing model offset if no explicit offset property
            first_m = existing_placeholders[0]
            off_x = getattr(first_m.offset, "x", 0.0)
            off_y = getattr(first_m.offset, "y", 0.0)
            off_z = getattr(first_m.offset, "z", 0.0)

        # Check if existing placeholder already matches desired scale and is visible
        if len(existing_placeholders) == 1:
            m = existing_placeholders[0]
            scale_matches = (
                math.isclose(m.scale.x, size_x, abs_tol=1e-5)
                and math.isclose(m.scale.y, size_y, abs_tol=1e-5)
                and math.isclose(m.scale.z, size_z, abs_tol=1e-5)
            )
            offset_matches = (
                math.isclose(m.offset.x, off_x, abs_tol=1e-5)
                and math.isclose(m.offset.y, off_y, abs_tol=1e-5)
                and math.isclose(m.offset.z, off_z, abs_tol=1e-5)
            )
            if scale_matches and offset_matches and bool(m.visible):
                result.already_valid += 1
                continue
            result.updated += 1
        else:
            result.added += 1

        _replace_models_with_placeholder(
            footprint,
            size_x,
            size_y,
            size_z,
            compatibility=compatibility,
            model_uri=placeholder_uri,
            offset_x=off_x,
            offset_y=off_y,
            offset_z=off_z,
        )
        updates.append(footprint)

    if not updates:
        return result

    commit = board.begin_commit()
    try:
        updated = board.update_items(updates)
        if len(updated) != len(updates):
            raise RuntimeError(
                f"KiCad updated {len(updated)} of {len(updates)} footprints")
        board.push_commit(commit, "Add placeholder 3D models")
    except Exception:
        board.drop_commit(commit)
        raise

    result.added = len(updates)
    return result



