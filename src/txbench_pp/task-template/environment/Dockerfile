FROM python:3.11-slim-bookworm

ENV DEBIAN_FRONTEND=noninteractive \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    PIP_DEFAULT_TIMEOUT=300

RUN apt-get update && \
    apt-get install -y --no-install-recommends \
        bash \
        build-essential \
        ca-certificates \
        curl \
        file \
        git \
        gzip \
        jq \
        libgl1 \
        libglib2.0-0 \
        libgomp1 \
        libsm6 \
        libxext6 \
        libxrender1 \
        nodejs \
        npm \
        procps \
        ripgrep \
        tar \
        unzip \
        wget && \
    rm -rf /var/lib/apt/lists/*

# The released tasks span tabular, image, single-cell, and chemistry analyses.
RUN python -m pip install --no-cache-dir --upgrade pip setuptools wheel && \
    python -m pip install --no-cache-dir \
        numpy \
        pandas \
        scipy \
        scikit-learn \
        statsmodels \
        lifelines \
        matplotlib \
        seaborn \
        plotly \
        pillow \
        scikit-image \
        opencv-python-headless \
        tifffile \
        imageio \
        pyarrow \
        fastparquet \
        h5py \
        tables \
        anndata \
        scanpy \
        rdkit \
        biopython \
        networkx \
        openpyxl \
        xlsxwriter \
        duckdb \
        polars \
        numba \
        umap-learn \
        sympy \
        requests \
        tqdm \
        pyyaml \
        latch-eval-tools==0.4.17 \
        adjustText==1.3.0 \
        gseapy==1.1.11 \
        upsetplot==0.9.0

RUN python -c "import Bio, PIL, anndata, cv2, duckdb, h5py, lifelines, matplotlib, networkx, numpy, pandas, polars, pyarrow, rdkit, scanpy, scipy, seaborn, skimage, sklearn, statsmodels" && \
    python -c "from latch_eval_tools.graders import get_grader; r = get_grader('numeric_tolerance').evaluate_answer({'x': 1}, {'ground_truth': {'x': 1}, 'tolerances': {'x': {'type': 'absolute', 'value': 0}}}); assert r.passed and r.score == 1.0" && \
    command -v jq node npm rg

WORKDIR /app
COPY data/* /app/data/

RUN mkdir -p /app/work /tmp/w /root/w /logs/artifacts && chmod -R a+rX /app/data

ENV TMPDIR=/tmp/w \
    JOBLIB_TEMP_FOLDER=/tmp/w \
    MPLCONFIGDIR=/root/w/matplotlib \
    NUMBA_CACHE_DIR=/root/w/numba

VOLUME ["/app/work", "/tmp/w", "/root/w"]
