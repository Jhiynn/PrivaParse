"""Runtime configuration for PrivaParse.

Every setting can be overridden by an environment variable with the
``PRIVAPARSE_`` prefix, e.g. ``PRIVAPARSE_DEVICE=cuda``.
"""

from __future__ import annotations

from pathlib import Path
from typing import Literal

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

DeviceSpec = str  # "auto" | "cpu" | "cuda" | "cuda:0" | ...

# Entity types supported in Phase 1. The schema descriptions are handed to
# GLiNER2 verbatim; per the GLiNER2 docs, described labels beat bare labels.
DEFAULT_ENTITY_SCHEMA: dict[str, str] = {
    "person": "Vor- und Nachnamen von Menschen, auch mit Titeln wie Dr. oder Prof.",
    "email": "E-Mail-Adressen",
    "phone number": "Telefon- und Mobilnummern, auch mit Landesvorwahl",
}

# Maps the GLiNER2 schema keys above onto our internal EntityType values.
SCHEMA_KEY_TO_TYPE: dict[str, str] = {
    "person": "PERSON",
    "email": "EMAIL",
    "phone number": "PHONE",
}


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_prefix="PRIVAPARSE_",
        env_file=".env",
        extra="ignore",
        # `model_id` would otherwise collide with pydantic's protected namespace.
        protected_namespaces=(),
    )

    # --- storage -----------------------------------------------------------
    db_path: Path = Field(
        default=Path("privaparse.db"),
        description="SQLite file holding the global vault. Contains plaintext PII.",
    )

    # --- detection ---------------------------------------------------------
    detector: Literal["hybrid", "gliner", "regex"] = Field(
        default="hybrid",
        description="hybrid = GLiNER2 for names + regex for email/phone (production). "
        "gliner = model only, used by the eval to measure the model in isolation. "
        "regex = no model at all; email and phone only, but needs no weights.",
    )
    model_id: str = Field(
        default="fastino/gliner2-privacy-filter-PII-multi",
        description="HuggingFace model id for the GLiNER2 detector.",
    )
    model_dir: Path = Field(
        default=Path("models/gliner"),
        description="Local cache directory for downloaded weights.",
    )
    offline: bool = Field(
        default=False,
        description="Never contact the Hugging Face Hub. Once the weights are "
        "cached this makes startup fully local — which is the point of a tool "
        "whose promise is that nothing leaves the machine. Off by default only "
        "because the first run has to download the model.",
    )
    threshold: float = Field(default=0.5, ge=0.0, le=1.0)
    scan_code: bool = Field(
        default=False,
        description="Scan fenced/inline code and URLs too. Off by default to avoid "
        "pseudonymising variable names and documentation snippets.",
    )
    coreference_sweep: bool = Field(
        default=True,
        description="After detection, re-find every accepted surface form elsewhere "
        "in the document so a missed repeat still gets its placeholder.",
    )

    # --- performance -------------------------------------------------------
    device: DeviceSpec = Field(default="auto")
    quantize: bool | None = Field(
        default=None,
        description="fp16 weights. None means: on when running on CUDA, off on CPU.",
    )
    compile: bool | None = Field(
        default=None,
        description="torch.compile. None means: on when running on CUDA. Costs warmup "
        "time once, which a long-running service pays back immediately.",
    )
    batch_size: int = Field(default=8, ge=1)
    flash_attention: bool = Field(
        default=False,
        description="Use FlashDeberta kernels if the package is installed.",
    )
    warmup: bool = Field(
        default=True,
        description="Push a dummy text through the model at engine init so the first "
        "real request does not pay compile/CUDA-init cost.",
    )
    chunk_chars: int = Field(
        default=512,
        ge=200,
        description="Window used when splitting a document for the model. Shorter "
        "is better for recall, not just for speed: GLiNER scores candidate spans "
        "against the whole chunk, so a long chunk dilutes them and names slip "
        "under the threshold. Measured on a 7.2 KB document, 1500 scored PERSON "
        "recall 0.900 and 512 scored 0.950 at identical precision — and 512 was "
        "also faster on GPU. Below ~384 precision starts to suffer.",
    )

    # --- logging -----------------------------------------------------------
    log_level: Literal["DEBUG", "INFO", "WARNING", "ERROR"] = "INFO"

    @field_validator("device")
    @classmethod
    def _validate_device(cls, v: str) -> str:
        v = v.strip().lower()
        if v in {"auto", "cpu", "cuda", "mps"}:
            return v
        if v.startswith("cuda:") and v.split(":", 1)[1].isdigit():
            return v
        raise ValueError(
            f"Invalid device {v!r}. Expected 'auto', 'cpu', 'cuda', 'cuda:<N>' or 'mps'."
        )

    @property
    def entity_schema(self) -> dict[str, str]:
        return dict(DEFAULT_ENTITY_SCHEMA)

    @property
    def db_url(self) -> str:
        return f"sqlite:///{self.db_path.resolve()}"


def load_settings(**overrides: object) -> Settings:
    """Build settings from env/.env, then apply explicit overrides.

    ``None`` overrides are dropped so CLI flags that were not passed do not
    clobber configured values.
    """
    clean = {k: v for k, v in overrides.items() if v is not None}
    return Settings(**clean)  # type: ignore[arg-type]
