import bz2
import lzma
import struct
import xml.etree.ElementTree as ET
import zlib
from pathlib import Path

from .models import PackageReport



def _decompress(data: bytes, encoding: str) -> bytes:
    if "bzip2" in encoding:
        return bz2.decompress(data)
    elif "gzip" in encoding:
        # xar "application/x-gzip" is zlib (deflate+header), NOT the gzip file format
        return zlib.decompress(data)
    elif "lzma" in encoding or "xz" in encoding:
        return lzma.decompress(data)
    return data  # application/octet-stream — already raw


class PackageAnalyzer:

    def analyze(self, filename: str) -> PackageReport:
        if filename.lower().endswith(".dmg"):
            return self._analyze_dmg(filename)
        return self._analyze_pkg(filename)

    def _analyze_dmg(self, filename: str) -> PackageReport:
        from .dmg_handler import mount_dmg, find_packages

        report = PackageReport()
        report.filename = Path(filename).name
        report.package_type = "DMG"

        try:
            with mount_dmg(filename) as mount_point:
                pkgs = find_packages(mount_point)
                if not pkgs:
                    report.warnings.append("No .pkg files found inside this DMG.")
                    return report

                pkg_names = ", ".join(p.name for p in pkgs)
                report.recommendations.append(
                    f"Contains {len(pkgs)} package(s): {pkg_names}"
                )

                # Analyse the primary package for metadata
                sub = self._analyze_pkg(str(pkgs[0]))
                report.architecture = sub.architecture
                report.signature = sub.signature
                report.compatible = sub.compatible
                report.warnings.extend(sub.warnings)
                if len(pkgs) > 1:
                    report.recommendations.append(
                        f"All {len(pkgs)} packages will be patched on export."
                    )
                else:
                    report.recommendations.extend(sub.recommendations)

        except (OSError, RuntimeError) as exc:
            report.warnings.append(f"DMG error: {exc}")

        return report

    def _analyze_pkg(self, filename: str) -> PackageReport:
        report = PackageReport()
        path = Path(filename)
        report.filename = path.name
        report.package_type = path.suffix.upper().replace(".", "") or "PKG"

        try:
            with open(filename, "rb") as f:
                header_raw = f.read(28)
                if len(header_raw) < 28:
                    report.warnings.append("File is too small to be a valid xar archive.")
                    return report

                magic, hdr_size, ver, toc_len_c, toc_len_u, cksum_alg = struct.unpack(
                    ">IHHQQI", header_raw
                )

                if magic != 0x78617221:
                    report.warnings.append(
                        "Invalid magic number: not a standard xar/pkg archive."
                    )
                    return report

                toc_c = f.read(toc_len_c)
                heap = f.read()

            toc = zlib.decompress(toc_c).decode("utf-8", errors="ignore")

            if "<signature" in toc:
                report.signature = "Signed (will be stripped during patch)"
            else:
                report.signature = "Unsigned"

            toc_root = ET.fromstring(toc)
            dist_file = next(
                (
                    fe
                    for fe in toc_root.iter("file")
                    if fe.findtext("name") == "Distribution"
                ),
                None,
            )

            if dist_file is not None:
                data_elem = dist_file.find("data")
                offset = int(data_elem.findtext("offset"))
                length = int(data_elem.findtext("length"))
                enc_elem = data_elem.find("encoding")
                encoding = (
                    enc_elem.get("style", "application/x-bzip2")
                    if enc_elem is not None
                    else "application/x-bzip2"
                )

                dist_xml = _decompress(
                    heap[offset : offset + length], encoding
                ).decode("utf-8", errors="ignore")

                try:
                    dist_root = ET.fromstring(dist_xml)
                    arch = dist_root.get("hostArchitectures")
                except ET.ParseError:
                    arch = None

                if arch:
                    report.architecture = arch
                else:
                    report.architecture = "Not Specified (implicit x86_64)"

                if "InstallationCheck" in dist_xml or "osVersion" in dist_xml:
                    report.warnings.append(
                        "Contains OS version locks or installation check scripts."
                    )
                    report.recommendations.append(
                        "Patching required to bypass macOS version restrictions."
                    )
                else:
                    report.recommendations.append(
                        "Package appears compatible or lacks explicit version gates."
                    )

                report.compatible = True
            else:
                report.warnings.append("Could not locate Distribution XML block.")

                report.recommendations.append(
                    "Deep inspection limited; verify this is a standard flat .pkg."
                )

        except (OSError, struct.error, zlib.error, lzma.LZMAError, ET.ParseError, ValueError, TypeError) as exc:
            report.warnings.append(f"Inspection error: {exc}")

        return report
