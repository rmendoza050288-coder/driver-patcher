from __future__ import annotations

import shlex
import subprocess
from pathlib import Path

from PySide6.QtCore import QThread, Signal
from PySide6.QtGui import QFont
from PySide6.QtWidgets import (
    QFileDialog,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMainWindow,
    QMessageBox,
    QPlainTextEdit,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from core.analyzer import PackageAnalyzer
from core.models import PackageReport
from core.patcher import PackagePatcher


class _InstallWorker(QThread):
    message: Signal = Signal(str)
    finished: Signal = Signal(bool, str)

    def __init__(self, pkg_path: str) -> None:
        super().__init__()
        self.pkg_path = pkg_path

    def run(self) -> None:
        try:
            script = (
                f'do shell script "installer -allowUntrusted '
                f"-pkg {shlex.quote(self.pkg_path)} -target /\""
                f" with administrator privileges"
            )
            self.message.emit("Requesting administrator privileges\u2026")
            proc = subprocess.run(
                ["osascript", "-e", script],
                capture_output=True,
                text=True,
            )
            if proc.returncode != 0:
                err = proc.stderr.strip() or proc.stdout.strip()
                raise RuntimeError(err or "Install was cancelled or failed.")
            self.message.emit("Driver installed successfully.")
            self.finished.emit(True, "Install complete.")
        except Exception as exc:
            self.message.emit(f"Install error: {exc}")
            self.finished.emit(False, str(exc))


class _PatchWorker(QThread):
    message: Signal = Signal(str)
    finished: Signal = Signal(bool, str)

    def __init__(self, input_path: str, output_path: str) -> None:
        super().__init__()
        self.input_path = input_path
        self.output_path = output_path

    def run(self) -> None:
        try:
            is_dmg = self.input_path.lower().endswith(".dmg")
            kind = "DMG" if is_dmg else "package"
            self.message.emit(f"Patching {kind} '{Path(self.input_path).name}' \u2026")
            PackagePatcher.patch_package(
                self.input_path, self.output_path, progress=self.message.emit
            )
            if not is_dmg:
                size = Path(self.output_path).stat().st_size
                self.message.emit(f"Wrote {size:,} bytes  \u2192  {self.output_path}")
                self.message.emit("")
                self.message.emit("To install, run in Terminal:")
                self.message.emit(
                    f"  sudo installer -allowUntrusted -pkg '{self.output_path}' -target /"
                )
            else:
                self.message.emit("")
                self.message.emit("To install: mount the DMG and run the .pkg inside it.")
            self.finished.emit(True, "Patch complete.")
        except Exception as exc:
            self.message.emit(f"Error: {exc}")
            self.finished.emit(False, str(exc))


class MainWindow(QMainWindow):

    def __init__(self) -> None:
        super().__init__()
        self.setWindowTitle("Driver Patcher")
        self.setMinimumSize(740, 640)
        self._report: PackageReport | None = None
        self._worker: _PatchWorker | None = None
        self._install_worker: _InstallWorker | None = None
        self._patched_pkg_path: str | None = None
        self._build_ui()

    # ── UI construction ───────────────────────────────────────────────────────

    def _build_ui(self) -> None:
        central = QWidget()
        self.setCentralWidget(central)
        root = QVBoxLayout(central)
        root.setSpacing(12)
        root.setContentsMargins(20, 20, 20, 16)

        # Title
        title = QLabel("Driver Patcher")
        title_font = QFont()
        title_font.setPointSize(20)
        title_font.setBold(True)
        title.setFont(title_font)

        sub = QLabel(
            "Patch legacy macOS installer packages for modern systems and Apple Silicon"
        )
        sub.setStyleSheet("color: gray;")

        root.addWidget(title)
        root.addWidget(sub)

        # ── File I/O ──────────────────────────────────────────────────────────
        io_group = QGroupBox("Package Files")
        io_layout = QVBoxLayout(io_group)

        # Input row
        in_row = QHBoxLayout()
        in_lbl = QLabel("Input .pkg:")
        in_lbl.setFixedWidth(90)
        self.input_field = QLineEdit()
        self.input_field.setPlaceholderText("Choose a .pkg file to patch\u2026")
        self.input_field.textChanged.connect(self._on_input_changed)
        in_btn = QPushButton("Browse\u2026")
        in_btn.setFixedWidth(80)
        in_btn.clicked.connect(self._browse_input)
        in_row.addWidget(in_lbl)
        in_row.addWidget(self.input_field)
        in_row.addWidget(in_btn)
        io_layout.addLayout(in_row)

        # Output row
        out_row = QHBoxLayout()
        out_lbl = QLabel("Output .pkg:")
        out_lbl.setFixedWidth(90)
        self.output_field = QLineEdit()
        self.output_field.setPlaceholderText("Choose where to save the patched package\u2026")
        self.output_field.textChanged.connect(self._update_patch_btn)
        out_btn = QPushButton("Browse\u2026")
        out_btn.setFixedWidth(80)
        out_btn.clicked.connect(self._browse_output)
        out_row.addWidget(out_lbl)
        out_row.addWidget(self.output_field)
        out_row.addWidget(out_btn)
        io_layout.addLayout(out_row)

        root.addWidget(io_group)

        # ── Analyze button ────────────────────────────────────────────────────
        self.analyze_btn = QPushButton("Analyze Package")
        self.analyze_btn.setEnabled(False)
        self.analyze_btn.setFixedHeight(34)
        self.analyze_btn.clicked.connect(self._analyze)
        root.addWidget(self.analyze_btn)

        # ── Report panel ──────────────────────────────────────────────────────
        self.report_group = QGroupBox("Package Report")
        self.report_group.setVisible(False)
        report_layout = QVBoxLayout(self.report_group)
        self.report_text = QPlainTextEdit()
        self.report_text.setReadOnly(True)
        self.report_text.setFixedHeight(130)
        self.report_text.setStyleSheet(
            "font-family: 'Menlo', 'Courier New', monospace; font-size: 12px;"
        )
        report_layout.addWidget(self.report_text)
        root.addWidget(self.report_group)

        # ── Patch button ──────────────────────────────────────────────────────
        self.patch_btn = QPushButton("Patch Package")
        self.patch_btn.setEnabled(False)
        self.patch_btn.setFixedHeight(42)
        patch_font = QFont()
        patch_font.setBold(True)
        patch_font.setPointSize(13)
        self.patch_btn.setFont(patch_font)
        self.patch_btn.clicked.connect(self._patch)
        root.addWidget(self.patch_btn)

        # Install button (hidden until a PKG patch succeeds)
        self.install_btn = QPushButton("\u2b07  Install Patched Package\u2026")
        self.install_btn.setVisible(False)
        self.install_btn.setFixedHeight(36)
        self.install_btn.setStyleSheet(
            "QPushButton { color: white; background-color: #0a84ff; border-radius: 6px; }"
            "QPushButton:hover { background-color: #0070d8; }"
            "QPushButton:disabled { background-color: #888; }"
        )
        self.install_btn.clicked.connect(self._install)
        root.addWidget(self.install_btn)

        # ── Log area ──────────────────────────────────────────────────────────
        log_group = QGroupBox("Log")
        log_layout = QVBoxLayout(log_group)
        self.log_area = QPlainTextEdit()
        self.log_area.setReadOnly(True)
        self.log_area.setStyleSheet(
            "font-family: 'Menlo', 'Courier New', monospace; font-size: 11px;"
        )
        self.log_area.setMinimumHeight(120)
        log_layout.addWidget(self.log_area)
        root.addWidget(log_group, stretch=1)

        self.statusBar().showMessage("Ready \u2014 select a .pkg or .dmg file to begin.")

    # ── Slots ─────────────────────────────────────────────────────────────────

    def _on_input_changed(self, text: str) -> None:
        path = text.strip()
        file_exists = bool(path) and Path(path).is_file()
        self.analyze_btn.setEnabled(file_exists)
        if path and not file_exists:
            self.statusBar().showMessage("File not found.")
        elif not path:
            self.statusBar().showMessage("Ready \u2014 select a .pkg or .dmg file to begin.")
        if not path:
            self._report = None
            self.report_group.setVisible(False)
            self._update_patch_btn()
        self.install_btn.setVisible(False)
        self._patched_pkg_path = None

    def _update_patch_btn(self) -> None:
        has_report = self._report is not None
        has_output = bool(self.output_field.text().strip())
        self.patch_btn.setEnabled(has_report and has_output)

    def _browse_input(self) -> None:
        path, _ = QFileDialog.getOpenFileName(
            self,
            "Select Package or Disk Image",
            str(Path.home()),
            "macOS Drivers (*.pkg *.dmg);;PKG Packages (*.pkg);;DMG Images (*.dmg);;All Files (*)",
        )
        if path:
            self.input_field.setText(path)
            p = Path(path)
            ext = "_patched.dmg" if path.lower().endswith(".dmg") else "_patched.pkg"
            self.output_field.setText(str(p.parent / (p.stem + ext)))

    def _browse_output(self) -> None:
        default = self.output_field.text() or str(Path.home())
        is_dmg = self.input_field.text().lower().endswith(".dmg")
        if is_dmg:
            path, _ = QFileDialog.getSaveFileName(
                self, "Save Patched DMG As", default,
                "DMG Images (*.dmg);;All Files (*)"
            )
            if path:
                if not path.lower().endswith(".dmg"):
                    path += ".dmg"
                self.output_field.setText(path)
        else:
            path, _ = QFileDialog.getSaveFileName(
                self, "Save Patched Package As", default,
                "macOS Packages (*.pkg);;All Files (*)"
            )
            if path:
                if not path.lower().endswith(".pkg"):
                    path += ".pkg"
                self.output_field.setText(path)

    def _analyze(self) -> None:
        path = self.input_field.text().strip()
        if not path:
            return
        self._log(f"Analyzing '{Path(path).name}' \u2026")
        self.statusBar().showMessage("Analyzing\u2026")
        try:
            report = PackageAnalyzer().analyze(path)
            self._report = report
            self._show_report(report)
            self.report_group.setVisible(True)
            self._update_patch_btn()
            self.statusBar().showMessage("Analysis complete.")
        except Exception as exc:
            self._log(f"Error during analysis: {exc}")
            self.statusBar().showMessage(f"Analysis error: {exc}")

    def _show_report(self, report: PackageReport) -> None:
        lines = [
            f"File         : {report.filename}",
            f"Type         : {report.package_type}",
            f"Architecture : {report.architecture}",
            f"Signature    : {report.signature}",
            f"Compatible   : {'Yes' if report.compatible else 'No (may still be patchable)'}",
        ]
        if report.warnings:
            lines += ["", "Warnings:"] + [f"  \u26a0  {w}" for w in report.warnings]
        if report.recommendations:
            lines += ["", "Recommendations:"] + [f"  \u2192  {r}" for r in report.recommendations]
        text = "\n".join(lines)
        self.report_text.setPlainText(text)
        self._log(text)

    def _patch(self) -> None:
        input_path = self.input_field.text().strip()
        output_path = self.output_field.text().strip()

        if not input_path or not output_path:
            QMessageBox.warning(
                self, "Missing Paths", "Both input and output paths are required."
            )
            return
        if not Path(input_path).is_file():
            QMessageBox.warning(
                self, "File Not Found", f"Input file does not exist:\n{input_path}"
            )
            return
        if input_path == output_path:
            QMessageBox.warning(
                self, "Same Path", "Input and output paths must be different."
            )
            return
        output_dir = Path(output_path).parent
        if not output_dir.exists():
            QMessageBox.warning(
                self,
                "Invalid Output Directory",
                f"Output directory does not exist:\n{output_dir}",
            )
            return

        self.patch_btn.setEnabled(False)
        self.analyze_btn.setEnabled(False)
        self.statusBar().showMessage("Patching\u2026")
        self._log(f"\n{'\u2500' * 52}")

        self._worker = _PatchWorker(input_path, output_path)
        self._worker.message.connect(self._log)
        self._worker.finished.connect(self._on_finished)
        self._worker.start()

    def _on_finished(self, success: bool, message: str) -> None:
        self.patch_btn.setEnabled(True)
        self.analyze_btn.setEnabled(bool(self.input_field.text().strip()))
        if success:
            self.statusBar().showMessage("Done.")
            QMessageBox.information(self, "Patch Complete", message)
            output_path = self.output_field.text().strip()
            if output_path.lower().endswith(".pkg"):
                self._patched_pkg_path = output_path
                self.install_btn.setVisible(True)
        else:
            self.statusBar().showMessage(f"Failed: {message}")
            QMessageBox.critical(self, "Patch Failed", message)

    def _install(self) -> None:
        if not self._patched_pkg_path:
            return
        self.install_btn.setEnabled(False)
        self.statusBar().showMessage("Installing\u2026")
        self._log(f"\n{'\u2500' * 52}")
        self._install_worker = _InstallWorker(self._patched_pkg_path)
        self._install_worker.message.connect(self._log)
        self._install_worker.finished.connect(self._on_install_finished)
        self._install_worker.start()

    def _on_install_finished(self, success: bool, message: str) -> None:
        self.install_btn.setEnabled(True)
        if success:
            self.statusBar().showMessage("Installed.")
            QMessageBox.information(self, "Install Complete", "Driver installed successfully.")
        else:
            self.statusBar().showMessage(f"Install failed: {message}")
            QMessageBox.critical(self, "Install Failed", message)

    def _log(self, text: str) -> None:
        self.log_area.appendPlainText(text)
        sb = self.log_area.verticalScrollBar()
        sb.setValue(sb.maximum())
