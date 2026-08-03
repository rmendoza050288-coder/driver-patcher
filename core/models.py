from dataclasses import dataclass, field


@dataclass
class PackageReport:
    filename: str = ""
    package_type: str = "Unknown"
    architecture: str = "Unknown"
    installer_version: str = "Unknown"
    signature: str = "Unknown"
    compatible: bool = False
    warnings: list[str] = field(default_factory=list)
    recommendations: list[str] = field(default_factory=list)
