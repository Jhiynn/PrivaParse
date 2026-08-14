# Install

## Install with pipx

Need pipx first? `python -m pip install --user pipx` fails outright on Debian
and Ubuntu 23.04+ (`externally-managed-environment`, PEP 668) unless you add
`--break-system-packages`; the package manager's own pipx avoids that:
`sudo apt install pipx` on Debian/Ubuntu, `brew install pipx` on macOS. See
[pipx's own install instructions](https://pypa.github.io/pipx/installation/)
for other platforms.

```bash
pipx install "privaparse[gateway]"
```

If `privaparse` isn't found afterwards, run `pipx ensurepath` and open a new
shell.

That gives you the CLI and the local gateway. Person detection needs the model
backend, which pulls in PyTorch — roughly 2 GB:

```bash
pipx install "privaparse[gateway,model]"
```

## Install from source

```bash
python -m venv .venv
```

Activate it — `.venv\Scripts\activate` on Windows, `source .venv/bin/activate`
on macOS or Linux — then:

```bash
pip install -e ".[dev]"
```

That gets you everything except the model backend — enough to run the full test
suite and the regex-only pipeline. For person detection you also need GLiNER2:

```bash
pip install -e ".[model]"
```

That pulls the CPU build of PyTorch from PyPI. On a CUDA machine, swap it
afterwards — pick the index whose newest wheel matches the torch version you
already have, so the swap is CPU-for-CUDA and not also a version jump:

```bash
pip install torch --index-url https://download.pytorch.org/whl/cu130 --force-reinstall
```

Swapping afterwards rather than installing from the CUDA index first is
deliberate: the PyTorch CDN is separate from PyPI and less reliable, so this
order means a bad connection costs you GPU speed rather than a working install.

If that download stalls — pip cannot resume a partial transfer, so a dropped
connection restarts the whole ~2 GB — fetch the wheel yourself and install the
file. `curl -C -` resumes:

```bash
curl -L -C - --retry 5 --retry-all-errors -o torch-cu130.whl "https://download-r2.pytorch.org/whl/cu130/torch-2.13.0%2Bcu130-cp312-cp312-win_amd64.whl"
```

Verify what you ended up with:

```bash
privaparse doctor
```

`doctor` prints the resolved device, dtype and model. If it says `device=cpu`
when you expected CUDA, the torch swap did not take.

## Switching between CPU and GPU

Nothing is compiled in. The device is read at engine construction:

```bash
privaparse --device cuda demo brief.md
```

```bash
privaparse --device cpu demo brief.md
```

`auto` picks CUDA when it is usable and CPU otherwise. An *explicit* `cuda` on a
machine without CUDA is an error, never a quiet downgrade.

Switching device also switches dtype: `quantize` and `compile` default to on for
CUDA and off for CPU, because fp16 and `torch.compile` pay off on a GPU and do
not on a CPU. Both can be pinned by hand (`PRIVAPARSE_QUANTIZE=false`) when you
want to compare like for like. `pytest -m model` includes a test asserting that
a CPU/GPU swap returns identical detections — being swappable is worth nothing
if the swap changes the answers.

**On Windows, `torch.compile` is unavailable** and is downgraded automatically.
The inductor backend needs Triton, which the PyTorch wheel does not ship on this
platform; without the check you get a `TritonMissing` crash on the first forward
pass, after the model has already loaded. Device and compile are treated
differently on purpose:

| | Unavailable → |
| --- | --- |
| **Device** | Error. Changes speed by ~20x and hides it. A contract. |
| **`torch.compile`** | Warn and continue in eager mode. Changes speed only, never the result. A hint. |

`privaparse doctor` shows `compile=off (triton is not installed …)` so the
downgrade is visible rather than silent. If you want compilation on Windows,
install a `triton-windows` build; the check picks it up automatically.

## Docker

```bash
docker build --target full -t privaparse:full .
```

```bash
docker run --rm --network host -v privaparse-vault:/data privaparse:full
```

`full` bakes the model weights in and sets `PRIVAPARSE_OFFLINE=1`, so the
container never contacts the Hugging Face Hub. `--target slim` leaves the
weights out and downloads them on first use.

`--network host` is what makes the container's loopback bind reachable from
your host, and it is the only documented way in: publishing a port would mean
binding `0.0.0.0` inside the container, and the image has no way to ask for
that. Mount `/data` — the vault must outlive the container, or every past
answer becomes unrestorable.

Both targets are built and run under podman as part of testing; `full` was
verified to detect with `--network none`, which is the whole point of baking
the weights. One podman quirk: its default OCI image format silently drops
`HEALTHCHECK`, so `podman build --format docker` is needed if you want the
container healthcheck. Docker's builder keeps it either way. The image is
large — 5.3 GB slim, 6.6 GB full — and that is torch, not PrivaParse.

## Verified on

Ubuntu 24.04.4 LTS, Python 3.12.3, in a sandbox that had never seen this
project before — no editable install, no resolved dependencies, no model
weights on disk. The pipx install of the built wheel (`[gateway]` extra),
`privaparse doctor` without model weights, the `demo` round trip with
`--detector regex`, and the contributor path (`python -m venv`,
`pip install -e ".[dev,gateway]"`, `pytest`, `ruff check .`) were all run
verbatim from this page, `README.md`, and `CONTRIBUTING.md`. `pytest` passed
with 7 deselected on both platforms — matching a Windows checkout exactly —
and `ruff check .` was clean.
