"""Resolve KiCad path variables and 3D model references."""

from __future__ import annotations

import json
import os
import re
import sys
from pathlib import Path
from typing import Mapping, Optional


_VARIABLE_RE = re.compile(r"\$\{([A-Za-z_][A-Za-z0-9_]*)\}")
_KICAD_MAJORS = range(6, 12)


def _config_directories() -> list[Path]:
    home = Path.home()
    if sys.platform == "darwin":
        return [home / "Library" / "Preferences" / "kicad"]
    if os.name == "nt":
        appdata = os.environ.get("APPDATA")
        return [Path(appdata) / "kicad"] if appdata else []
    xdg_config = Path(os.environ.get("XDG_CONFIG_HOME", home / ".config"))
    return [xdg_config / "kicad"]


def _data_directories() -> list[Path]:
    home = Path.home()
    if sys.platform == "darwin":
        return [home / "Documents" / "KiCad"]
    if os.name == "nt":
        user_profile = Path(os.environ.get("USERPROFILE", home))
        return [user_profile / "Documents" / "KiCad"]
    xdg_data = Path(os.environ.get("XDG_DATA_HOME", home / ".local" / "share"))
    return [xdg_data / "kicad", xdg_data / "KiCad"]


def _load_user_path_variables() -> dict[str, str]:
    result = {}
    for config_root in _config_directories():
        try:
            configs = config_root.glob("*/kicad_common.json")
            for config in configs:
                try:
                    data = json.loads(config.read_text(encoding="utf-8"))
                    variables = (data.get("environment", {}) or {}).get(
                        "vars", {}) or {}
                    result.update({
                        str(key): str(value)
                        for key, value in variables.items()
                        if value is not None
                    })
                except (OSError, ValueError, TypeError):
                    continue
        except OSError:
            continue
    return result


def _third_party_roots(source_path=None) -> dict[int, Path]:
    """Find PCM roots, preferring the root containing *source_path*."""
    roots = {}
    if source_path is not None:
        source = Path(source_path)
        for candidate in (source.absolute(), source.resolve()):
            for parent in candidate.parents:
                if parent.name.lower() != "3rdparty":
                    continue
                version = parent.parent.name.split(".", 1)[0]
                if version.isdigit():
                    roots[int(version)] = parent
                break

    for data_root in _data_directories():
        for major in _KICAD_MAJORS:
            candidate = data_root / f"{major}.0" / "3rdparty"
            if candidate.is_dir() and major not in roots:
                roots[major] = candidate
    return roots


def _installed_3d_model_directory(kicad) -> Optional[Path]:
    try:
        binary = Path(kicad.get_kicad_binary_path("kicad-cli")).resolve()
    except Exception:
        return None

    candidates = [
        binary.parent.parent / "SharedSupport" / "3dmodels",
        binary.parent.parent / "share" / "kicad" / "3dmodels",
        binary.parent / "3dmodels",
    ]
    return next((path for path in candidates if path.is_dir()), None)


def _project_directory(board) -> str:
    if board is None:
        return ""
    try:
        return str(board.document.project.path or "")
    except Exception:
        try:
            return str(board.get_project().path or "")
        except Exception:
            return ""


def _find_library_shape_dir(source_path=None, board=None, variables=None) -> Optional[Path]:
    """Locate the Kikakuka.3dshapes directory across standard and adjacent locations."""
    # 1. Check relative to plugin folder (e.g. adjacent ../library/3dmodels/Kikakuka.3dshapes)
    search_origins = []
    if source_path:
        search_origins.append(Path(source_path).resolve())
    search_origins.append(Path(__file__).resolve())

    for origin in search_origins:
        dirs_to_check = [
            origin.parent.parent / "library" / "3dmodels" / "Kikakuka.3dshapes",
            origin.parent.parent.parent / "library" / "3dmodels" / "Kikakuka.3dshapes",
            origin.parent / "3dmodels" / "Kikakuka.3dshapes",
            origin.parent / "library" / "3dmodels" / "Kikakuka.3dshapes",
            origin.parent / "resources" / "3dmodels" / "Kikakuka.3dshapes",
        ]
        for d in dirs_to_check:
            if d.is_dir():
                return d

    # 2. Check board footprint model references
    if board is not None:
        try:
            for footprint in board.get_footprints():
                for model in getattr(footprint.definition, "models", []):
                    fn = str(getattr(model, "filename", "") or "")
                    if "Kikakuka.3dshapes" in fn:
                        p = Path(fn.split("Kikakuka.3dshapes", 1)[0] + "Kikakuka.3dshapes")
                        if p.is_dir():
                            return p
        except Exception:
            pass

    # 3. Check environment / path variables
    check_vars = ["KIKAKUKA_3DMODELS", "KIKAKUKA_DIR", "KIKAKUKA_LIBRARY"]
    for var in check_vars:
        val = os.environ.get(var) or (variables.get(var) if variables else None)
        if val:
            p = Path(val)
            if p.name == "Kikakuka.3dshapes" and p.is_dir():
                return p
            cand = p / "Kikakuka.3dshapes"
            if cand.is_dir():
                return cand
            cand = p / "3dmodels" / "Kikakuka.3dshapes"
            if cand.is_dir():
                return cand

    # 4. Search within 3rdparty roots
    for major, root in _third_party_roots(source_path).items():
        if not root.is_dir():
            continue
        # Check standard PCM package identifiers
        for pkg_name in (
            "com_github_buganini_kikakuka-footprints",
            "com.github.buganini.kikakuka-footprints",
            "com_github_buganini_kikakuka_footprints",
            "kikakuka-footprints",
        ):
            cand = root / "3dmodels" / pkg_name / "Kikakuka.3dshapes"
            if cand.is_dir():
                return cand
        # Walk 3rdparty plugins or 3dmodels
        for sub in ("plugins", "3dmodels"):
            sub_dir = root / sub
            if sub_dir.is_dir():
                for match in sub_dir.glob("**/Kikakuka.3dshapes"):
                    if match.is_dir():
                        return match

    return None


def find_kikakuka_3d_model(
    model_name: str,
    board=None,
    variables: Optional[Mapping[str, str]] = None,
    source_path=None,
) -> Optional[Path]:
    """Find a Kikakuka 3D model file, searching all known locations and extensions."""
    base_name = Path(model_name).name
    stem = Path(model_name).stem.replace("_", "-")
    names_to_try = [
        base_name,
        f"{stem}.step",
        f"{stem}.stp",
        f"{stem}.STEP",
        f"{stem}.STP",
    ]

    shape_dir = _find_library_shape_dir(source_path=source_path, board=board, variables=variables)
    if shape_dir and shape_dir.is_dir():
        for name in names_to_try:
            cand = shape_dir / name
            if cand.is_file():
                return cand

    # Check project directory
    if variables and "KIPRJMOD" in variables:
        prj = Path(variables["KIPRJMOD"])
        for sub in ("", "3dmodels", "library/3dmodels", "3dmodels/Kikakuka.3dshapes"):
            for name in names_to_try:
                cand = prj / sub / name
                if cand.is_file():
                    return cand

    return None


def to_kicad_model_uri(
    file_path: Path | str,
    variables: Mapping[str, str],
    compatibility=None,
) -> str:
    """Format an existing model file path as a portable KiCad URI using environment variables."""
    path = Path(file_path).resolve()
    path_str = str(path).replace("\\", "/")

    # Check third party roots
    for major in (10, 9, 11, 8, 7):
        var_name = f"KICAD{major}_3RD_PARTY"
        root_dir = variables.get(var_name)
        if root_dir:
            resolved_root = Path(root_dir).resolve()
            try:
                rel = path.relative_to(resolved_root)
                return f"${{{var_name}}}/" + str(rel).replace("\\", "/")
            except ValueError:
                pass

    # Check KIPRJMOD
    prj_dir = variables.get("KIPRJMOD")
    if prj_dir:
        try:
            rel = path.relative_to(Path(prj_dir).resolve())
            return "${KIPRJMOD}/" + str(rel).replace("\\", "/")
        except ValueError:
            pass

    return path_str


def path_variables(kicad, board=None, *, source_path=None) -> dict[str, str]:
    """Collect KiCad path variables needed to resolve model references."""
    variables = _load_user_path_variables()
    variables.update({key: value for key, value in os.environ.items()})

    project_path = _project_directory(board)
    if project_path:
        variables["KIPRJMOD"] = project_path

    roots = _third_party_roots(source_path)
    for major, root in roots.items():
        variables.setdefault(f"KICAD{major}_3RD_PARTY", str(root))

    # Provide alias KICAD_3RD_PARTY
    for major in (10, 9, 11, 8, 7):
        if f"KICAD{major}_3RD_PARTY" in variables:
            variables.setdefault("KICAD_3RD_PARTY", variables[f"KICAD{major}_3RD_PARTY"])
            break

    model_dir = _installed_3d_model_directory(kicad)
    if model_dir is not None:
        for major in _KICAD_MAJORS:
            variables.setdefault(f"KICAD{major}_3DMODEL_DIR", str(model_dir))
        variables.setdefault("KICAD_3DMODEL_DIR", str(model_dir))
    return variables


def expand_variables(value: str, variables: Mapping[str, str]) -> str:
    """Recursively expand ``${NAME}`` references from *variables*."""
    def replacement(match):
        replacement_value = variables.get(match.group(1))
        return (str(replacement_value)
                if replacement_value is not None else match.group(0))

    result = value
    for _ in range(10):
        expanded = _VARIABLE_RE.sub(replacement, result)
        if expanded == result:
            break
        result = expanded
    return result


def resolve_model_path(
    filename: str,
    board,
    variables: Mapping[str, str],
    *,
    prefer_step: bool = False,
) -> Optional[Path]:
    """Resolve a KiCad model reference and return it when the file exists."""
    value = str(filename or "").strip()
    if not value:
        return None

    try:
        value = board.expand_text_variables(value, expand_env_vars=True)
    except TypeError:
        try:
            value = board.expand_text_variables(value)
        except Exception:
            pass
    except Exception:
        pass

    value = expand_variables(str(value), variables)

    # If variables were not fully expanded or file does not exist, check Kikakuka models
    path = Path(os.path.expanduser(value))
    if not path.is_absolute():
        project_dir = variables.get("KIPRJMOD")
        if project_dir:
            path = Path(project_dir) / path

    if prefer_step and path.suffix.lower() == ".wrl":
        for suffix in (".step", ".stp", ".STEP", ".STP"):
            alternative = path.with_suffix(suffix)
            if alternative.is_file():
                return alternative

    if path.is_file():
        return path

    # Check case-insensitive extensions (.step vs .stp)
    if path.suffix.lower() in (".step", ".stp"):
        for suffix in (".step", ".stp", ".STEP", ".STP"):
            alternative = path.with_suffix(suffix)
            if alternative.is_file():
                return alternative

    # Fallback for Kikakuka models (unit-cube, coupler-fixed, coupler-moving)
    model_name = Path(value).name.lower()
    if any(k in model_name for k in ("unit-cube", "unit_cube", "coupler-fixed", "coupler_fixed", "coupler-moving", "coupler_moving")):
        found = find_kikakuka_3d_model(Path(value).name, board=board, variables=variables)
        if found and found.is_file():
            return found

    return None

