from __future__ import annotations

import math
import os
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

from kicad_compat import KICAD10_COMPAT, get_kicad_compat
from kicad_paths import (
    find_kikakuka_3d_model,
    path_variables,
    resolve_model_path,
    to_kicad_model_uri,
)
from placeholder_models import (
    footprint_label,
    footprint_properties,
    get_normalized_property,
)


# Retained as the public KiCad 10 mapping used by existing tests and boards.
# Runtime code selects the mapping from the connected system KiCad version.
COUPLER_MODELS = KICAD10_COMPAT.coupler_model_uris
_LENGTH_RE = re.compile(
    r"\s*([+-]?(?:\d+(?:\.\d*)?|\.\d+)(?:[eE][+-]?\d+)?)"
    r"(?:\s*(mm|cm|in|mil|um|µm))?\s*",
    re.IGNORECASE,
)
_ANGLE_RE = re.compile(
    r"\s*([+-]?(?:\d+(?:\.\d*)?|\.\d+)(?:[eE][+-]?\d+)?)"
    r"(?:\s*(deg|°|rad))?\s*",
    re.IGNORECASE,
)



@dataclass
class EnableResult:
    scanned: int = 0
    couplers: int = 0
    added: int = 0
    updated: int = 0
    unchanged: int = 0
    invalid: int = 0
    details: list[str] = field(default_factory=list)


@dataclass
class HideResult:
    scanned: int = 0
    changed: int = 0
    hidden: int = 0


def parse_signed_length_mm(value, quantity_name: str) -> float:
    """Parse a signed length, treating a missing suffix as millimetres."""
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


def parse_tilt_degrees(value) -> float:
    """Parse a signed footprint-local X rotation in degrees."""
    if value is None or not str(value).strip():
        return 0.0
    match = _ANGLE_RE.fullmatch(str(value).strip())
    if match is None:
        raise ValueError(f"invalid Tilt value {value!r}; expected degrees")
    result = float(match.group(1))
    unit = (match.group(2) or "deg").lower()
    if unit == "rad":
        result = math.degrees(result)
    if not math.isfinite(result):
        raise ValueError("Tilt must be finite")
    return result


def footprint_coupler_type(footprint) -> Optional[str]:
    """Return the coupler type, preferring the library entry name."""
    candidates = []
    try:
        candidates.append(footprint.definition.id.name)
    except Exception:
        pass
    try:
        candidates.append(str(footprint.definition.id))
    except Exception:
        pass
    try:
        candidates.append(footprint.value_field.text.value)
    except Exception:
        pass

    for cand in candidates:
        if not cand:
            continue
        cand_str = str(cand).strip()
        # Direct exact match
        if cand_str in COUPLER_MODELS:
            return cand_str
        # Check normalized name
        norm = re.sub(r"[^a-zA-Z0-9]", "", cand_str).lower()
        if norm.endswith("couplerfixed"):
            return "CouplerFixed"
        if norm.endswith("couplermoving"):
            return "CouplerMoving"
    return None


def _normalise_model_filename(filename) -> str:
    return str(filename or "").strip().replace("\\", "/")


def _helper_model_type(filename, models=COUPLER_MODELS) -> Optional[str]:
    value = _normalise_model_filename(filename).lower()
    if not value:
        return None

    # Check exact match with any model URI
    for coupler_type, helper in models.items():
        normalised = _normalise_model_filename(helper).lower()
        if value == normalised:
            return coupler_type

    # Check filename ending or stem
    if value.endswith("coupler-fixed.step") or value.endswith("coupler-fixed.stp") or value.endswith("coupler_fixed.step") or value.endswith("coupler_fixed.stp"):
        return "CouplerFixed"
    if value.endswith("coupler-moving.step") or value.endswith("coupler-moving.stp") or value.endswith("coupler_moving.step") or value.endswith("coupler_moving.stp"):
        return "CouplerMoving"

    return None


def _new_helper_model(
    coupler_type: str,
    z: float,
    tilt: float,
    offset: float,
    compatibility=KICAD10_COMPAT,
    model_uri: Optional[str] = None,
):
    from kipy.board_types import Footprint3DModel
    from kipy.geometry import Vector3D

    model = Footprint3DModel()
    model.filename = model_uri or compatibility.coupler_model_uris[coupler_type]
    model.scale = Vector3D.from_xyz(1.0, 1.0, 1.0)
    # Positive Offset follows the footprint triangle. In KiCad's footprint
    # coordinates that is local -Y. Z remains along the board normal and is
    # therefore applied independently of the local-X tilt.
    model.offset = Vector3D.from_xyz(0.0, -offset, z)
    model.rotation = Vector3D.from_xyz(tilt, 0.0, 0.0)
    model.visible = True
    compatibility.set_model_opacity(model, 1.0)
    return model


def _vector_matches(vector, expected, tolerance=1e-5) -> bool:
    try:
        return all(
            math.isclose(actual, wanted, abs_tol=tolerance)
            for actual, wanted in zip(
                (vector.x, vector.y, vector.z), expected)
        )
    except Exception:
        return False


def _model_matches(model, expected) -> bool:
    opacity = getattr(model, "opacity", 1.0)
    if opacity is None:
        opacity = 1.0
    return (
        _normalise_model_filename(model.filename)
        == _normalise_model_filename(expected.filename)
        and _vector_matches(model.scale, (1.0, 1.0, 1.0))
        and _vector_matches(
            model.offset,
            (expected.offset.x, expected.offset.y, expected.offset.z),
        )
        and _vector_matches(
            model.rotation,
            (expected.rotation.x, expected.rotation.y, expected.rotation.z),
        )
        and bool(model.visible)
        and math.isclose(opacity, 1.0, abs_tol=1e-5)
    )


def _replace_helper_models(footprint, helper, models=COUPLER_MODELS) -> None:
    from kipy.board_types import Footprint3DModel

    footprint.definition.items = [
        item for item in footprint.definition.items
        if not (
            isinstance(item, Footprint3DModel)
            and _helper_model_type(item.filename, models) is not None
        )
    ]
    footprint.definition.add_item(helper)


def _hide_helper_models(footprint, models=COUPLER_MODELS) -> int:
    hidden = 0
    for model in footprint.definition.models:
        if _helper_model_type(model.filename, models) is None or not model.visible:
            continue
        model.visible = False
        hidden += 1
    return hidden


def _push_updates(board, updates, message: str) -> None:
    if not updates:
        return
    commit = board.begin_commit()
    try:
        updated = board.update_items(updates)
        if len(updated) != len(updates):
            raise RuntimeError(
                f"KiCad updated {len(updated)} of {len(updates)} footprints")
        board.push_commit(commit, message)
    except Exception:
        board.drop_commit(commit)
        raise


def enable_update_coupler_helpers(kicad, board) -> EnableResult:
    """Add or update helper models using each coupler's transform fields."""
    compatibility = get_kicad_compat(kicad.get_version())
    models = compatibility.coupler_model_uris
    footprints = list(board.get_footprints())
    result = EnableResult(scanned=len(footprints))
    couplers = []
    for footprint in footprints:
        coupler_type = footprint_coupler_type(footprint)
        if coupler_type is not None:
            couplers.append((footprint, coupler_type))
    result.couplers = len(couplers)

    variables = path_variables(kicad, board)
    resolved_uris = {}
    missing = []
    for coupler_type in sorted({item[1] for item in couplers}):
        filename = models[coupler_type]
        path = resolve_model_path(filename, board, variables)
        if path is None:
            target_name = f"{coupler_type.lower().replace('coupler', 'coupler-')}.step"
            path = find_kikakuka_3d_model(target_name, board=board, variables=variables)
        if path is None:
            missing.append(filename.rsplit("/", 1)[-1])
        else:
            resolved_uris[coupler_type] = to_kicad_model_uri(path, variables, compatibility)

    if missing:
        raise FileNotFoundError(
            f"Kikakuka {', '.join(missing)} was not found. Please ensure the "
            "Kikakuka 3D model library (com.github.buganini.kikakuka-footprints) is installed."
        )

    updates = []
    for footprint, coupler_type in couplers:
        properties = footprint_properties(footprint)
        val_z = get_normalized_property(properties, "Z", "OffsetZ")
        val_offset = get_normalized_property(properties, "Offset", "OffsetY")
        val_tilt = get_normalized_property(properties, "Tilt", "RotationX", "Angle")

        try:
            z = parse_signed_length_mm(val_z, "Z")
            offset = parse_signed_length_mm(val_offset, "Offset")
            tilt = parse_tilt_degrees(val_tilt)
        except ValueError as error:
            result.invalid += 1
            result.details.append(f"{footprint_label(footprint)}: {error}")
            continue

        model_uri = resolved_uris.get(coupler_type)
        expected = _new_helper_model(
            coupler_type,
            z,
            tilt,
            offset,
            compatibility=compatibility,
            model_uri=model_uri,
        )
        existing = [
            model for model in footprint.definition.models
            if _helper_model_type(model.filename, models) is not None
        ]
        if len(existing) == 1 and _model_matches(existing[0], expected):
            result.unchanged += 1
            continue

        if existing:
            result.updated += 1
        else:
            result.added += 1
        _replace_helper_models(footprint, expected, models)
        updates.append(footprint)

    _push_updates(board, updates, "Update Coupler 3D Viewer helpers")
    return result


def hide_coupler_helpers(kicad, board) -> HideResult:
    """Hide Kikakuka coupler helper models without removing them."""
    compatibility = get_kicad_compat(kicad.get_version())
    models = compatibility.coupler_model_uris
    footprints = list(board.get_footprints())
    result = HideResult(scanned=len(footprints))
    updates = []
    for footprint in footprints:
        hidden = _hide_helper_models(footprint, models)
        if hidden:
            result.changed += 1
            result.hidden += hidden
            updates.append(footprint)
    _push_updates(board, updates, "Hide coupler helper 3D models")
    return result

