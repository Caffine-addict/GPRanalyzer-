# Prior art: Caffine-addict/GPRanalyzer-

A prototype existed on GitHub before this project started. It is not used as
a starting point — reviewed and found to be three flat scripts (`gpr_app.py`,
`gpr_cli.py`, `gpr_streamlit.py`) each independently reimplementing the same
denoise -> YOLO -> LLM pipeline, no shared module, no package structure, and
a `tools/generate_report.py` + `smoke_tests.py` that imported a `backend`
package which did not exist anywhere in the repo (so the most architecturally
interesting file in that codebase — versioned JSON/MD/PDF reports with a
certain/uncertain confidence split — was dead on arrival). Hardcoded absolute
paths (`/Users/pritamwani/...`) meant nothing there ran on a second machine.

Kept from it, ported deliberately rather than copied wholesale:

- The 9-class taxonomy (`config.yaml` under `detection.classes`).
- The denoise parameters (median blur + CLAHE) as a starting point for
  `preprocess/enhance.py`.
- The certain/uncertain confidence framing — this became the
  `"calibrated"/"estimated"/"unavailable"` confidence discipline in
  `core/contracts.py`'s `Evidence`.
- VOC-to-YOLO conversion logic and the ResNet18 + PCA + K-Means
  pseudo-labelling approach, as reference for any future dataset tooling.

Everything else (the flat script structure, the vision-LLM report call, the
hardcoded paths, the untested `backend` imports) was deliberately not
carried forward.
