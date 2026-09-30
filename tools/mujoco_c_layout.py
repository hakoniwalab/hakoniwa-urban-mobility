"""Read mjModel / mjData fields of a MuJoCo shared library through ctypes.

The Drone Core runs its own MuJoCo shared library, and the fleet tools query
it through ctypes rather than mujoco-python. Reading a field of mjModel or
mjData needs the struct layout that library was built with; that layout
changes between MuJoCo releases. Every MuJoCo release ships its C headers
beside the library (include/mujoco, or Headers in the macOS framework), so the
layout is built from those headers: ctypes lays out the leading fields of the
struct, up to the last one asked for, with the platform's C alignment.

The header version must match the library's mj_version(), and every pointer
read must lie inside the struct's own buffer, so a layout that does not match
the library is an error rather than a wrong read.
"""

from __future__ import annotations

import ctypes
import re
from pathlib import Path


class MujocoLayoutError(RuntimeError):
    pass


_HEADERS = ("mjtype.h", "mjmodel.h", "mjdata.h", "mujoco.h")

_BASE_TYPES = {
    "double": ctypes.c_double,
    "float": ctypes.c_float,
    "int": ctypes.c_int,
    "unsigned int": ctypes.c_uint,
    "char": ctypes.c_char,
    "unsigned char": ctypes.c_ubyte,
    "_Bool": ctypes.c_bool,
    "int64_t": ctypes.c_int64,
    "uint64_t": ctypes.c_uint64,
    "size_t": ctypes.c_size_t,
    "uintptr_t": ctypes.c_size_t,
}


def header_directory(library_path: Path) -> Path:
    """The include directory shipped with the MuJoCo library."""
    library = Path(library_path).resolve()
    for candidate in (
        library.parent.parent / "include" / "mujoco",  # release tree: lib/ or bin/
        library.parent / "Headers",  # macOS framework
    ):
        if (candidate / "mjmodel.h").is_file():
            return candidate
    raise MujocoLayoutError(f"MuJoCo headers were not found beside {library}")


def _strip(text: str) -> str:
    text = re.sub(r"/\*.*?\*/", " ", text, flags=re.S)
    return re.sub(r"//[^\n]*", " ", text)


def _body(text: str, start: int) -> tuple[str, int]:
    """The text between the brace at ``start`` and its match, and the index after it."""
    depth = 0
    for index in range(start, len(text)):
        if text[index] == "{":
            depth += 1
        elif text[index] == "}":
            depth -= 1
            if depth == 0:
                return text[start + 1:index], index + 1
    raise MujocoLayoutError("unbalanced braces in MuJoCo header")


class _Headers:
    def __init__(self, directory: Path):
        text = "\n".join(
            _strip((directory / name).read_text(encoding="utf-8")) for name in _HEADERS
        )
        self.constants: dict[str, int] = {}
        for name, value in re.findall(r"^\s*#\s*define\s+(\w+)\s+(-?\d+)\s*$", text, flags=re.M):
            self.constants.setdefault(name, int(value))
        # Leave out the preprocessor lines: only the first branch of a
        # conditional typedef (mjtNum is double unless mjUSESINGLE) is used.
        code = re.sub(r"^\s*#[^\n]*", " ", text, flags=re.M)
        self.typedefs: dict[str, str] = {}
        for base, name in re.findall(r"\btypedef\s+([\w ]+?)\s+(\w+)\s*;", code):
            self.typedefs.setdefault(name, base)
        self.structs: dict[str, str] = {}
        for match in re.finditer(r"\btypedef\s+(struct|enum)\s+\w*\s*\{", code):
            body, end = _body(code, match.end() - 1)
            name = re.match(r"\s*(\w+)", code[end:])
            if not name:
                continue
            if match.group(1) == "struct":
                self.structs.setdefault(name.group(1), body)
            else:
                self._enum(body)
        self._types: dict[str, type] = {}

    def _enum(self, body: str) -> None:
        value = -1
        for entry in body.split(","):
            entry = entry.strip()
            if not entry:
                continue
            name, _, expression = entry.partition("=")
            try:
                value = self.value(expression) if expression.strip() else value + 1
            except MujocoLayoutError:
                return  # the rest of this enum is not an integer sequence
            self.constants.setdefault(name.strip(), value)

    def value(self, expression: str) -> int:
        def constant(match: re.Match) -> str:
            if match.group(0) not in self.constants:
                raise MujocoLayoutError(f"unknown constant in MuJoCo header: {match.group(0)}")
            return str(self.constants[match.group(0)])

        expression = " ".join(re.sub(r"(?<![\d.])[A-Za-z_]\w*", constant, expression).split())
        if not re.fullmatch(r"[\d\s+\-*()<>|&~]+", expression):
            raise MujocoLayoutError(f"unsupported constant expression in MuJoCo header: {expression}")
        try:
            return int(eval(expression, {"__builtins__": {}}))  # noqa: S307 - digits and operators only
        except SyntaxError as exc:
            raise MujocoLayoutError(f"unsupported constant expression in MuJoCo header: {expression}") from exc

    def type(self, name: str) -> type:
        name = " ".join(name.replace("const ", " ").split())
        if name in _BASE_TYPES:
            return _BASE_TYPES[name]
        if name in self._types:
            return self._types[name]
        if name in self.structs:
            self._types[name] = self.struct(name, self.structs[name])
            return self._types[name]
        if name in self.typedefs:
            return self.type(self.typedefs[name])
        raise MujocoLayoutError(f"unknown type in MuJoCo header: {name}")

    def struct(self, name: str, body: str, until: str | None = None) -> type:
        """A ctypes Structure of the fields in ``body``, ending with ``until``."""
        fields: list[tuple[str, type]] = []
        index = 0
        while (end := body.find(";", index)) >= 0:
            nested = re.compile(r"\s*struct\s*\{").match(body, index)
            if nested:  # an anonymous struct member: struct { ... } name;
                inner, index = _body(body, nested.end() - 1)
                member = re.compile(r"\s*(\w+)\s*;").match(body, index)
                if not member:
                    raise MujocoLayoutError(f"unsupported nested struct in MuJoCo {name}")
                fields.append((member.group(1), self.struct(f"{name}.{member.group(1)}", inner)))
                index = member.end()
                continue
            statement, index = body[index:end].strip(), end + 1
            match = re.fullmatch(r"([\w ]+?)\s*(\*?)\s*(\w+)\s*((?:\[[^\]]+\]\s*)*)", statement)
            if not match:
                raise MujocoLayoutError(f"unsupported field in MuJoCo {name}: {statement}")
            base, pointer, field, dimensions = match.groups()
            field_type = ctypes.c_void_p if pointer else self.type(base)
            for dimension in reversed(re.findall(r"\[([^\]]+)\]", dimensions)):
                field_type = field_type * self.value(dimension)
            fields.append((field, field_type))
            if field == until:
                break
        if until is not None and (not fields or fields[-1][0] != until):
            raise MujocoLayoutError(f"MuJoCo {name} has no field {until}")
        return type(name.replace(".", "_"), (ctypes.Structure,), {"_fields_": fields})


class Layout:
    """The leading fields of mjModel and mjData, as the headers declare them."""

    def __init__(self, library: ctypes.CDLL, library_path: Path, *, model_until: str, data_until: str):
        directory = header_directory(library_path)
        headers = _Headers(directory)
        library.mj_version.restype = ctypes.c_int
        header_version = headers.constants.get("mjVERSION_HEADER")
        if header_version != library.mj_version():
            raise MujocoLayoutError(
                f"MuJoCo headers in {directory} are version {header_version}, "
                f"but the library is {library.mj_version()}"
            )
        self.constants = headers.constants
        self.model = headers.struct("mjModel", headers.structs["mjModel"], until=model_until)
        self.data = headers.struct("mjData", headers.structs["mjData"], until=data_until)


def array(struct: ctypes.Structure, field: str, count: int, element=ctypes.c_double) -> ctypes.Array:
    """``count`` elements behind the pointer ``field``; it must lie in the struct's buffer."""
    address = getattr(struct, field)
    size = count * ctypes.sizeof(element)
    buffer = struct.buffer or 0
    if not address or address < buffer or address + size > buffer + struct.nbuffer:
        raise MujocoLayoutError(
            f"{type(struct).__name__}.{field} does not point into its buffer; "
            "the MuJoCo headers do not match the library"
        )
    return (element * count).from_address(address)
