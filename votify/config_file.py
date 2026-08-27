from __future__ import annotations

import configparser
from enum import Enum
from pathlib import Path

import click
import typing


def _is_serializable(value: typing.Any) -> bool:
    """Can this default survive a round trip through config.ini?

    None is fine (it writes as "null"). Scalars are fine. An Enum is fine only
    if its *value* is a scalar — click's UNSET sentinel is an Enum member
    wrapping a bare object(), and that is exactly what must not be written.
    """
    if value is None or isinstance(value, (str, bool, int, float)):
        return True
    if isinstance(value, Enum):
        return isinstance(value.value, (str, bool, int, float))
    return False


class ConfigFile:
    def __init__(
        self,
        config_path: Path,
        section_name: str = "votify",
    ) -> None:
        self.config_path = config_path
        self.section_name = section_name
        # Set when a stored value had to be rewritten (see
        # _parse_param_from_config), so the heal is flushed exactly once.
        self._repaired = False

        self._read_config_file()

    def _read_config_file(self) -> None:
        self.config = configparser.ConfigParser(interpolation=None)

        if self.config_path.exists():
            self.config.read(self.config_path, encoding="utf-8")
        else:
            self.config_path.parent.mkdir(parents=True, exist_ok=True)

        if not self.config.has_section(self.section_name):
            self.config.add_section(self.section_name)

    def _write_config_file(self) -> None:
        with self.config_path.open("w", encoding="utf-8") as config_file:
            self.config.write(config_file)

    def _serialize_param_default(self, param: click.Parameter) -> str:
        if not isinstance(param.default, (list, tuple)):
            param_default = [param.default]
        else:
            param_default = param.default

        if not param_default:
            return ""

        first = param_default[0]

        # click >= 8.5 reports an UNSET sentinel — not False, not None — as the
        # default of any param declared without an explicit one, which here is
        # every is_flag option. Stringifying it writes a literal
        # "<object object at 0x...>" into config.ini, and the next run dies
        # parsing that back before it so much as looks at the URL.
        #
        # The sentinel is itself an Enum member whose value is a bare object(),
        # so it cannot simply be excluded by type — the real test is whether
        # the thing can round-trip through an ini file at all. A flag with no
        # explicit default is False; anything else genuinely has no value.
        if not _is_serializable(first):
            return "false" if getattr(param, "is_flag", False) else "null"

        if isinstance(first, Enum):
            return ",".join(str(item.value) for item in param_default)
        if isinstance(first, bool):
            return ",".join(str(item).lower() for item in param_default)
        if first is None:
            return "null"

        return ",".join(str(item) for item in param_default)

    def _add_param_default_to_config(
        self,
        param: click.Parameter,
    ) -> bool:
        if self.config[self.section_name].get(param.name):
            return False

        value = self._serialize_param_default(param)
        self.config[self.section_name][param.name] = value

        return True

    def _parse_param_from_config(
        self,
        param: click.Parameter,
    ) -> typing.Any:
        value = self.config[self.section_name].get(param.name)

        if value == "null":
            return None

        try:
            return param.type_cast_value(None, value)
        except Exception:
            # An entry left behind by an older or incompatible run must never
            # be fatal — a single bad value used to take the entire CLI down
            # for every URL, and config.ini is not something a user thinks to
            # look at. Repair it from the current default instead, so the file
            # heals itself rather than needing to be deleted by hand.
            repaired = self._serialize_param_default(param)
            self.config[self.section_name][param.name] = repaired
            self._repaired = True

            if repaired in ("null", ""):
                return None
            try:
                return param.type_cast_value(None, repaired)
            except Exception:
                return None

    def add_params_default_to_config(
        self,
        params: list[click.Parameter],
    ) -> None:
        has_changes = False

        for param in params:
            has_changes = self._add_param_default_to_config(param) or has_changes

        if has_changes:
            self._write_config_file()

    def parse_params_from_config(
        self,
        params: list[click.Parameter],
    ) -> dict[str, typing.Any]:
        parsed_params = {}

        for param in params:
            parsed_params[param.name] = self._parse_param_from_config(param)

        if self._repaired:
            self._repaired = False
            try:
                self._write_config_file()
            except Exception:
                # A read-only config dir is no reason to fail the download;
                # the in-memory repair already stands for this run.
                pass

        return parsed_params
