# gprMax FDTD simulator, built from a pinned commit and kept outside gpr-analyzer.
#
# gprMax is GPL-3.0. gpr-analyzer never imports it: simulate/ writes plain-text gprMax
# input files, runs this container as a separate program, and reads the HDF5 output
# files back. The simulator stays out of the project's code and dependency tree, and the
# same image builds unchanged on the company's machine (Docker Desktop on Windows or Mac).
#
#   docker build -t gpr-analyzer-gprmax simulate/docker
FROM python:3.12-slim-bookworm

# master as of 2026-09-03: v3 solver, NumPy 2 and Cython 3 support.
ARG GPRMAX_COMMIT=950d0e1976d344e4509251b7ebe385e5cd0836e9

# gcc brings OpenMP (libgomp), which the CPU solver needs; libc6-dev for the C headers.
RUN apt-get update \
 && apt-get install -y --no-install-recommends gcc libc6-dev git \
 && rm -rf /var/lib/apt/lists/*

# Floors from gprMax's own requirements.txt at the pinned commit (jupyter omitted: notebooks only).
# setuptools is listed explicitly: Python 3.12 images ship neither it nor distutils, and
# gprMax's setup.py needs one of the two.
RUN pip install --no-cache-dir setuptools \
      "numpy>=1.26.4" "Cython>=3.0.11" "h5py>=3.12.1" "scipy>=1.15.1" "matplotlib>=3.8.0" \
      "colorama>=0.4.6" "psutil>=5.9.0" "terminaltables>=3.1.10" "tqdm>=4.67.1"

# `setup.py build` compiles the Cython solver in place (-O3 -fopenmp -march=native).
# -march=native targets the CPU the image is built on, so build it where it will run.
RUN git clone https://github.com/gprMax/gprMax.git /opt/gprMax \
 && cd /opt/gprMax && git checkout "${GPRMAX_COMMIT}" \
 && python setup.py build

# gprMax's host probe cannot parse lscpu's output on ARM Linux; see simulate/docker/lscpu.
COPY lscpu /usr/local/bin/lscpu
RUN chmod +x /usr/local/bin/lscpu

ENV PYTHONPATH=/opt/gprMax
WORKDIR /work
ENTRYPOINT ["python", "-m"]
CMD ["gprMax", "--help"]
