import bz2
import hashlib
import lzma
import re
import shutil
import struct
import subprocess
import tempfile
import xml.etree.ElementTree as ET
import zlib
from pathlib import Path



def _decompress(data: bytes, encoding: str) -> bytes:
    if "bzip2" in encoding:
        return bz2.decompress(data)
    elif "gzip" in encoding:
        # xar "application/x-gzip" is zlib (deflate+header), NOT the gzip file format
        return zlib.decompress(data)
    elif "lzma" in encoding or "xz" in encoding:
        return lzma.decompress(data)
    return data


def _compress(data: bytes, encoding: str) -> bytes:
    if "bzip2" in encoding:
        return bz2.compress(data, compresslevel=9)
    elif "gzip" in encoding:
        return zlib.compress(data)  # match xar's zlib convention
    elif "lzma" in encoding or "xz" in encoding:
        return lzma.compress(data)
    return data


def _neutralize_installation_check(dist: str) -> str:
    """Replace InstallationCheck body using bracket counting to handle nested braces."""
    m = re.search(r"function\s+InstallationCheck\s*\(\s*prefix\s*\)\s*\{", dist)
    if not m:
        return dist
    depth = 0
    i = m.end() - 1  # index of the opening '{'
    while i < len(dist):
        if dist[i] == "{":
            depth += 1
        elif dist[i] == "}":
            depth -= 1
            if depth == 0:
                return (
                    dist[: m.start()]
                    + "function InstallationCheck(prefix) {\n\treturn true;\n}"
                    + dist[i + 1 :]
                )
        i += 1
    return dist  # no matching brace found — leave unchanged


class PackagePatcher:

    @staticmethod
    def patch_package(input_path: str, output_path: str, progress=None) -> None:
        def log(msg: str) -> None:
            if progress:
                progress(msg)

        if input_path.lower().endswith(".dmg"):
            PackagePatcher._patch_dmg(input_path, output_path, log)
        else:
            PackagePatcher._patch_pkg(input_path, output_path)

    @staticmethod
    def _patch_dmg(input_path: str, output_path: str, log=None) -> None:
        from .dmg_handler import mount_dmg, find_packages

        def _log(msg: str) -> None:
            if log:
                log(msg)

        with tempfile.TemporaryDirectory(prefix="driver_patcher_") as work_dir:
            work_path = Path(work_dir) / "vol"
            work_path.mkdir()

            _log("Mounting DMG\u2026")
            with mount_dmg(input_path) as mount_point:
                pkgs = find_packages(mount_point)
                if not pkgs:
                    raise ValueError("No .pkg files found inside the DMG.")

                _log(
                    f"Found {len(pkgs)} package(s): "
                    + ", ".join(p.name for p in pkgs)
                )
                _log("Copying volume contents\u2026")

                for item in mount_point.iterdir():
                    if item.name.startswith("."):
                        continue  # skip macOS metadata (.Trashes, .fseventsd …)
                    dst = work_path / item.name
                    try:
                        if item.is_dir():
                            shutil.copytree(str(item), str(dst), symlinks=True)
                        else:
                            shutil.copy2(str(item), str(dst))
                    except Exception:
                        pass

                pkg_rel_paths = [pkg.relative_to(mount_point) for pkg in pkgs]

            _log(f"Patching {len(pkg_rel_paths)} package(s)\u2026")
            for rel in pkg_rel_paths:
                _log(f"  \u2192 {rel.name}")
                pkg_src = work_path / rel
                pkg_tmp = Path(str(pkg_src) + ".tmp")
                PackagePatcher._patch_pkg(str(pkg_src), str(pkg_tmp))
                pkg_src.unlink()
                pkg_tmp.rename(pkg_src)

            _log("Building output DMG\u2026")
            out_no_ext = (
                output_path[:-4]
                if output_path.lower().endswith(".dmg")
                else output_path
            )
            vol_name = Path(input_path).stem + " Patched"
            proc = subprocess.run(
                [
                    "hdiutil", "create",
                    "-srcfolder", str(work_path),
                    "-volname", vol_name,
                    "-format", "UDZO",
                    "-ov",
                    "-o", out_no_ext,
                ],
                capture_output=True,
                text=True,
            )
            if proc.returncode != 0:
                raise RuntimeError(
                    f"hdiutil create failed: "
                    f"{proc.stderr.strip() or proc.stdout.strip()}"
                )

            size = Path(output_path).stat().st_size
            _log(f"Wrote {size:,} bytes  \u2192  {output_path}")

    @staticmethod
    def _patch_pkg(input_path: str, output_path: str) -> None:
        with open(input_path, "rb") as f:
            header_raw = f.read(28)
            magic, hdr_size, ver, toc_len_c, toc_len_u, cksum_alg = struct.unpack(
                ">IHHQQI", header_raw
            )

            if magic != 0x78617221:
                raise ValueError("Input file is not a valid xar archive (.pkg).")

            toc_c = f.read(toc_len_c)
            heap = bytearray(f.read())

        toc = zlib.decompress(toc_c).decode()
        toc_root = ET.fromstring(toc)

        # 1. Remove RSA (<signature>) and CMS (<x-signature>) blocks
        for parent in toc_root.iter():
            for child in list(parent):
                if child.tag in ("signature", "x-signature"):
                    parent.remove(child)

        # 2. Locate Distribution XML data block
        dist_file = next(
            (fe for fe in toc_root.iter("file") if fe.findtext("name") == "Distribution"),
            None,
        )
        if dist_file is None:
            raise ValueError("Could not find Distribution entry in package TOC.")

        data_elem = dist_file.find("data")
        orig_offset = int(data_elem.findtext("offset"))
        orig_clen = int(data_elem.findtext("length"))
        enc_elem = data_elem.find("encoding")
        encoding = (
            enc_elem.get("style", "application/x-bzip2")
            if enc_elem is not None
            else "application/x-bzip2"
        )

        old_dist = _decompress(
            bytes(heap[orig_offset : orig_offset + orig_clen]), encoding
        ).decode("utf-8", errors="replace")

        new_dist = old_dist

        # 3a. Add arm64 to hostArchitectures
        new_dist = re.sub(
            r'hostArchitectures="([^"]+)"',
            lambda m: 'hostArchitectures="'
            + ",".join(sorted(set(m.group(1).split(",") + ["arm64"])))
            + '"',
            new_dist,
        )

        # 3b. Neutralize InstallationCheck — bracket-aware replacement
        new_dist = _neutralize_installation_check(new_dist)

        # 4. Compress patched Distribution and splice into heap
        new_bytes = new_dist.encode()
        new_compressed = _compress(new_bytes, encoding)
        new_clen = len(new_compressed)
        new_ulen = len(new_bytes)
        delta = new_clen - orig_clen

        new_heap = bytearray(
            heap[:orig_offset] + new_compressed + heap[orig_offset + orig_clen :]
        )

        # 5. Update TOC: distribution entry lengths, sizes, and checksums
        new_arch_sha1 = hashlib.sha1(new_compressed).hexdigest()
        new_extr_sha1 = hashlib.sha1(new_bytes).hexdigest()

        data_elem.find("length").text = str(new_clen)
        data_elem.find("size").text = str(new_ulen)
        data_elem.find("archived-checksum").text = new_arch_sha1
        data_elem.find("extracted-checksum").text = new_extr_sha1

        if delta != 0:
            for offset_elem in toc_root.iter("offset"):
                val = int(offset_elem.text)
                if val > orig_offset:
                    offset_elem.text = str(val + delta)

        # 6. Recompress TOC and fix heap checksum (SHA1 at heap[0:20])
        new_toc_bytes = ET.tostring(toc_root, encoding="unicode").encode()
        new_toc_c = zlib.compress(new_toc_bytes)

        if cksum_alg == 1:
            new_heap[0:20] = hashlib.sha1(new_toc_c).digest()

        # 7. Write output atomically; delete partial file on failure
        new_hdr = struct.pack(
            ">IHHQQI",
            magic,
            hdr_size,
            ver,
            len(new_toc_c),
            len(new_toc_bytes),
            cksum_alg,
        )

        out_path = Path(output_path)
        try:
            with open(output_path, "wb") as f:
                f.write(new_hdr)
                f.write(new_toc_c)
                f.write(new_heap)
        except BaseException:
            out_path.unlink(missing_ok=True)
            raise
