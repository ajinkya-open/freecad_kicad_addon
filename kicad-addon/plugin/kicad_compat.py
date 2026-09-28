"""Version-specific behavior for the system KiCad integration.

Only code that talks to the user's KiCad installation belongs here.  The
bundled KiCad runtimes used by Panelizer and Differ deliberately do not use
this factory.
"""

from __future__ import annotations

import json
import os
import re
from typing import Callable, Mapping


class UnsupportedKiCadVersion(ValueError):
    """Raised when no compatibility implementation exists for a KiCad major."""


def _version_major(version) -> int:
    major = getattr(version, "major", None)
    if major is not None:
        return int(major)
    if isinstance(version, int):
        return version
    match = re.search(r"(?<!\d)(\d+)(?:\.\d+)?", str(version))
    if match is None:
        raise UnsupportedKiCadVersion(
            f"Cannot determine system KiCad version from {version!r}"
        )
    return int(match.group(1))


class KiCadCompatibility:
    """Base contract for one supported system KiCad major version."""

    major: int
    library_fields: tuple[str, ...]

    def __init__(self, system_version):
        self.system_version = system_version

    @property
    def third_party_variable(self) -> str:
        return f"KICAD{self.major}_3RD_PARTY"

    def third_party_uri(self, *parts: str) -> str:
        suffix = "/".join(str(part).strip("/\\") for part in parts)
        return f"${{{self.third_party_variable}}}/{suffix}"

    def kikakuka_model_uri(self, filename: str) -> str:
        return self.third_party_uri(
            "3dmodels",
            "com_github_buganini_kikakuka-footprints",
            "Kikakuka.3dshapes",
            filename,
        )

    @property
    def placeholder_model_uri(self) -> str:
        return self.kikakuka_model_uri("unit-cube.step")

    @property
    def coupler_model_uris(self) -> dict[str, str]:
        return {
            "CouplerFixed": self.kikakuka_model_uri("coupler-fixed.step"),
            "CouplerMoving": self.kikakuka_model_uri("coupler-moving.step"),
        }

    def footprint_library_uri(self, clean_identifier: str) -> str:
        return self.third_party_uri(
            "footprints", clean_identifier, "Kikakuka.pretty"
        )

    def empty_footprint_library_table(self) -> str:
        raise NotImplementedError

    def footprint_library_entry(
        self,
        clean_identifier: str,
        name: str = "PCM_Kikakuka",
    ) -> str:
        uri = self.footprint_library_uri(clean_identifier)
        return (
            f'  (lib (name "{name}") (type "KiCad") '
            f'(uri "{uri}") (options "") '
            f'(descr "Added by Kikakuka"))'
        )

    def read_library_node(self, node) -> dict[str, str]:
        return {
            field: node.get(field).value
            for field in self.library_fields
        }

    def render_library_table(self, name: str, table: Mapping) -> str:
        """Render the library-table schema supported by this KiCad major."""
        lines = [f"({name}", f"  (version {table['version']})"]
        for library in table["lib"]:
            fields = "".join(
                f"({field} {json.dumps(library[field])})"
                for field in self.library_fields
            )
            lines.append(f"  (lib {fields})")
        lines.append(")")
        return "\n".join(lines) + "\n"

    def convert_library_paths_to_project_relative(
        self,
        project: Mapping,
        project_dir: str,
        relpath: Callable[..., str],
    ) -> list[tuple[str, str]]:
        """Convert absolute library URIs using this version's table schema."""
        changes = []
        for table_name in ("sym_lib_table", "fp_lib_table"):
            for library in project[table_name]["lib"]:
                old_uri = library["uri"]
                if not os.path.isabs(old_uri) or not os.path.exists(old_uri):
                    continue
                relative = relpath(old_uri, project_dir, allow_outside=True)
                new_uri = "${KIPRJMOD}/" + relative
                library["uri"] = new_uri
                changes.append((old_uri, new_uri))
        return changes

    def set_model_opacity(self, model, opacity: float) -> None:
        raise NotImplementedError

    def set_footprint_pose(self, footprint, position, orientation) -> None:
        raise NotImplementedError

    def board_path(self, board, normalize: Callable[[str], str]):
        raise NotImplementedError


class GenericKiCadCompatibility(KiCadCompatibility):
    """Generic IPC compatibility working across KiCad 9, 10, 11+."""

    library_fields = ("name", "type", "uri", "options", "descr")

    def __init__(self, system_version, major: Optional[int] = None):
        super().__init__(system_version)
        if major is not None:
            self.major = major
        else:
            try:
                self.major = _version_major(system_version)
            except Exception:
                self.major = 10

    def empty_footprint_library_table(self) -> str:
        return "(fp_lib_table\n  (version 7)\n)\n"

    def set_model_opacity(self, model, opacity: float) -> None:
        try:
            model._proto.opacity = opacity
        except Exception:
            try:
                model.opacity = opacity
            except Exception:
                pass

    def set_footprint_pose(self, footprint, position, orientation) -> None:
        try:
            definition_items = list(footprint.definition.items)
            footprint.position = position
            footprint.orientation = orientation
            footprint.definition.items = definition_items
        except Exception:
            try:
                footprint.position = position
                footprint.orientation = orientation
            except Exception:
                pass

    def board_path(self, board, normalize: Callable[[str], str]):
        """Return the absolute board path using the KiCad IPC shape."""
        document = getattr(board, "document", None)
        name = (
            getattr(board, "name", "")
            or getattr(document, "board_filename", "")
        )
        if not name:
            return None
        if os.path.isabs(name):
            return normalize(name)
        project = board.get_project()
        project_path = getattr(project, "path", "")
        if not project_path:
            return None
        return normalize(os.path.join(project_path, name))


class KiCad9Compatibility(GenericKiCadCompatibility):
    major = 9


class KiCad10Compatibility(GenericKiCadCompatibility):
    major = 10
    kicad_python_requirement = "kicad-python>=0.8,<0.9"


class KiCad11Compatibility(GenericKiCadCompatibility):
    major = 11


_COMPATIBILITY_BY_MAJOR = {
    9: KiCad9Compatibility,
    10: KiCad10Compatibility,
    11: KiCad11Compatibility,
}


def get_kicad_compat(system_version) -> KiCadCompatibility:
    """Return the implementation selected by the system KiCad version."""
    try:
        major = _version_major(system_version)
    except Exception:
        major = 10

    if major in _COMPATIBILITY_BY_MAJOR:
        return _COMPATIBILITY_BY_MAJOR[major](system_version)
    return GenericKiCadCompatibility(system_version, major=major)


KICAD10_COMPAT = KiCad10Compatibility("10.0")

