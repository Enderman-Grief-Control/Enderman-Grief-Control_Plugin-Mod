#!/usr/bin/env python3
from pathlib import Path
import sys
import xml.etree.ElementTree as ET


ROOT = Path(__file__).resolve().parents[1]
POM_NS = {"m": "http://maven.apache.org/POM/4.0.0"}


def pom_property(path: Path, property_name: str) -> str:
    tree = ET.parse(path)
    value = tree.findtext(f"m:properties/m:{property_name}", namespaces=POM_NS)
    if value is None or not value.strip():
        raise ValueError(f"{path.relative_to(ROOT)} is missing property {property_name}")
    return value.strip()


def gradle_property(path: Path, property_name: str) -> str:
    for line in path.read_text(encoding="utf-8").splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        key, separator, value = stripped.partition("=")
        if separator and key.strip() == property_name:
            value = value.strip()
            if value:
                return value
            break
    raise ValueError(f"{path.relative_to(ROOT)} is missing property {property_name}")


def main() -> int:
    pins = {
        "messaging module revision": pom_property(
            ROOT / "enderman-grief-control-messaging" / "pom.xml",
            "revision",
        ),
        "Paper messaging dependency": pom_property(
            ROOT / "paper-plugin" / "pom.xml",
            "enderman-grief-control-messaging.version",
        ),
        "Fabric messaging dependency": gradle_property(
            ROOT / "fabric-mod" / "gradle.properties",
            "messaging_version",
        ),
    }

    expected = pins["messaging module revision"]
    mismatches = {
        name: version
        for name, version in pins.items()
        if version != expected
    }

    for name, version in pins.items():
        print(f"{name}: {version}")

    if mismatches:
        print(
            "Messaging version pins must move together. "
            "Update enderman-grief-control-messaging/pom.xml, "
            "paper-plugin/pom.xml, and fabric-mod/gradle.properties together.",
            file=sys.stderr,
        )
        return 1

    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as error:
        print(f"Unable to check messaging version pins: {error}", file=sys.stderr)
        raise SystemExit(1)
