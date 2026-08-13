"""Runtime configuration for PrivaParse.

Every setting can be overridden by an environment variable with the
``PRIVAPARSE_`` prefix, e.g. ``PRIVAPARSE_DEVICE=cuda``.
"""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING, Literal

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

if TYPE_CHECKING:  # pragma: no cover
    from privaparse.app.catalogue import Catalogue

DeviceSpec = str  # "auto" | "cpu" | "cuda" | "cuda:0" | ...


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
    catalogue_path: Path | None = Field(
        default=None,
        description="Entity catalogue YAML. None means: discover it (PRIVAPARSE_ENTITIES, "
        "./privaparse.entities.yaml, ~/.config/privaparse/entities.yaml), then fall back "
        "to the built-in.",
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

    # --- gateway -------------------------------------------------------------
    gateway_upstream: str = Field(
        default="https://api.openai.com",
        description="Where the gateway forwards to. Point it at Azure, a local "
        "vLLM server, or any other OpenAI-compatible endpoint. The path is added "
        "by the gateway, so this is an origin: https://host, no /v1.",
    )
    gateway_fuzzy: bool = Field(
        default=False,
        description="Also restore placeholders the model handed back slightly wrong "
        "-- a bracket pair dropped, quotes injected, the underscore spaced out. Off "
        "by default because exact matching is the stricter contract; a model that "
        "mangles placeholders is measured in docs/gateway-model-fidelity-report.md. "
        "Widens only how a placeholder may be spelled, never which mapping may "
        "resolve it.",
    )
    gateway_hint: bool = Field(
        default=False,
        description="Prepend a system message asking the model to reproduce "
        "[[TYPE_A1]] tokens verbatim. Off by default: it rewrites the caller's "
        "request, costs tokens on every call that carries an entity, and can only "
        "ask -- it cannot guarantee.",
    )
    gateway_allow_images: bool = Field(
        default=False,
        description="Forward image and file parts the detector cannot read, instead "
        "of refusing the request. Off by default, and the reason is not squeamishness: "
        "a coding agent screenshots its own work, and a screenshot can show every "
        "value that was just pseudonymised out of the text. Turning this on means "
        "images leave the machine unexamined. Codex cannot verify its output visually "
        "without it.",
    )
    gateway_cache: int = Field(
        default=2048,
        ge=0,
        description="How many text blocks the gateway keeps detection results for. "
        "A chat client resends its whole history every turn, so most blocks of a "
        "request were already detected on the previous one. 0 turns the cache off; "
        "the entries hold entity values in memory, and an operator may prefer that "
        "they not outlive the request that produced them.",
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
    def catalogue(self) -> Catalogue:
        """The resolved catalogue. Cached — loading parses YAML and validates."""
        cached = self.__dict__.get("_catalogue")
        if cached is None:
            from privaparse.app.catalogue import load_catalogue

            cached = load_catalogue(self.catalogue_path)
            object.__setattr__(self, "_catalogue", cached)
        return cached

    @property
    def entity_schema(self) -> dict[str, str]:
        return self.catalogue.schema()

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
