# PyInstaller recipe for the GPR Studio desktop app.
#   .venv/bin/python -m PyInstaller packaging/gpr_studio.spec --noconfirm
# Windows: .venv\Scripts\python -m PyInstaller packaging\gpr_studio.spec --noconfirm
# Output: dist/GPR Studio/ (a folder with the executable), plus GPR Studio.app on macOS.
# The Studio never imports the detector's training stack, so torch/ultralytics stay out:
# that is most of a gigabyte the app does not need.
from pathlib import Path

ROOT = Path(SPECPATH).parent  # noqa: F821 - SPECPATH is provided by PyInstaller

a = Analysis(  # noqa: F821
    [str(ROOT / "studio" / "launcher.py")],
    pathex=[str(ROOT)],
    datas=[
        (str(ROOT / "studio" / "static"), "studio/static"),
        (str(ROOT / "reason" / "prompts"), "reason/prompts"),
        (str(ROOT / "config.yaml"), "."),
    ],
    hiddenimports=[
        "groq",  # imported lazily when the reasoning assistant is first used
        "uvicorn.logging", "uvicorn.loops.auto", "uvicorn.protocols.http.auto",
        "uvicorn.protocols.websockets.auto", "uvicorn.lifespan.on",
    ],
    excludes=["torch", "torchvision", "ultralytics", "tensorflow", "matplotlib", "IPython",
              "scipy", "sklearn", "pandas", "h5py", "duckdb", "pytest"],
    noarchive=False,
)
pyz = PYZ(a.pure)  # noqa: F821
exe = EXE(  # noqa: F821
    pyz, a.scripts, [], exclude_binaries=True, name="GPR Studio", console=True,
    disable_windowed_traceback=False, upx=False,
)
coll = COLLECT(exe, a.binaries, a.datas, name="GPR Studio", upx=False)  # noqa: F821
app = BUNDLE(coll, name="GPR Studio.app", bundle_identifier="com.gprstudio.app")  # noqa: F821 - macOS only
