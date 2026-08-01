FROM debian:trixie-slim

ARG MINIFORGE_VERSION=26.3.2-3
ARG MINIFORGE_SHA256=848194851a98903134187fbb4ab50efe87b003e0c0f808f97644b7524a62bf2c
ARG PLINK2_VERSION=20260504
ARG PLINK2_SHA256=b8e68c8c06343a8b30c6a89a3488b05d6f2ea2f46749ad934a8966512da5d571

ENV DEBIAN_FRONTEND=noninteractive \
    PATH=/opt/conda/bin:$PATH \
    BASILISK_EXTERNAL_DIR=/opt/basilisk \
    BASILISK_CUSTOM_PYTHON_3_14_0=/opt/basilisk-python/bin/python \
    BASILISK_NO_PYENV=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

RUN apt-get update && \
    apt-get install -y --no-install-recommends \
        bash \
        ca-certificates \
        curl \
        git \
        jq \
        nodejs \
        npm \
        procps \
        ripgrep \
        unzip && \
    rm -rf /var/lib/apt/lists/*

RUN curl -fsSL \
        "https://github.com/conda-forge/miniforge/releases/download/${MINIFORGE_VERSION}/Miniforge3-${MINIFORGE_VERSION}-Linux-x86_64.sh" \
        -o /tmp/miniforge.sh && \
    echo "${MINIFORGE_SHA256}  /tmp/miniforge.sh" | sha256sum --check --strict && \
    bash /tmp/miniforge.sh -b -p /opt/conda && \
    rm /tmp/miniforge.sh && \
    printf 'channels:\n  - conda-forge\n  - bioconda\nchannel_priority: strict\n' \
        > /opt/conda/.condarc

RUN curl -fsSL \
        "https://s3.amazonaws.com/plink2-assets/alpha7/plink2_linux_x86_64_${PLINK2_VERSION}.zip" \
        -o /tmp/plink2.zip && \
    echo "${PLINK2_SHA256}  /tmp/plink2.zip" | sha256sum --check --strict && \
    unzip -q /tmp/plink2.zip plink2 -d /usr/local/bin && \
    chmod 0755 /usr/local/bin/plink2 && \
    rm /tmp/plink2.zip

# Complete Python, genomics, single-cell, R, and Bioconductor stack from the paper.
RUN conda install --yes --name base \
        --override-channels \
        --channel conda-forge \
        --channel bioconda \
        python=3.13.14 \
        numpy=2.4.6 \
        pandas=2.3.3 \
        scipy=1.18.0 \
        scikit-learn=1.9.0 \
        statsmodels=0.14.6 \
        lifelines=0.30.3 \
        matplotlib=3.11.1 \
        seaborn=0.13.2 \
        openpyxl=3.1.5 \
        pyarrow=25.0.0 \
        pysam=0.24.0 \
        pybedtools=0.12.0 \
        pybigwig=0.3.25 \
        pyfaidx=0.9.0.4 \
        scanpy=1.12.3 \
        anndata=0.13.2 \
        scrublet=0.2.3 \
        pydeseq2=0.5.4 \
        bedtools=2.31.1 \
        htslib=1.24 \
        r-base=4.5.3 \
        r-seurat=5.5.1 \
        bioconductor-deseq2=1.50.2 \
        bioconductor-edger=4.8.2 \
        bioconductor-limma=3.66.0 \
        bioconductor-tximport=1.38.2 \
        bioconductor-singlecellexperiment=1.32.0 \
        bioconductor-zellkonverter=1.20.1 \
        bioconductor-scdblfinder=1.24.0 && \
    conda clean --all --yes

# zellkonverter's pinned basilisk environment requires Python 3.14.0.
RUN conda create --yes --prefix /opt/basilisk-python \
        --override-channels \
        --channel conda-forge \
        python=3.14.0 \
        pip && \
    conda clean --all --yes

RUN CC=x86_64-conda-linux-gnu-cc \
    CXX=x86_64-conda-linux-gnu-c++ \
    python -m pip install --no-cache-dir \
        bed-reader==1.1.0 \
        cyvcf2==0.34.0 \
        ncls==0.0.70 \
        pyranges==0.1.4 \
        pysnptools==0.5.14 \
        sorted-nearest==0.0.41

RUN python -c "import anndata, bed_reader, cyvcf2, lifelines, matplotlib, numpy, openpyxl, pandas, pyarrow, pybedtools, pyBigWig, pydeseq2, pyfaidx, pyranges, pysam, pysnptools, scanpy, scipy, scrublet, seaborn, sklearn, statsmodels" && \
    Rscript -e 'packages <- c("Seurat", "DESeq2", "edgeR", "limma", "tximport", "SingleCellExperiment", "zellkonverter", "scDblFinder"); stopifnot(all(vapply(packages, requireNamespace, logical(1), quietly = TRUE)))' && \
    bedtools --version && \
    command -v tabix && \
    tabix --version | head -n 1 && \
    plink2 --version

RUN Rscript -e 'client <- basilisk::basiliskStart(zellkonverter::zellkonverterAnnDataEnv()); basilisk::basiliskStop(client)' && \
    chmod -R a+rwX /opt/basilisk

RUN printf '%s\n' 'export PATH=/opt/conda/bin:$PATH' > /etc/profile.d/conda.sh

WORKDIR /app
COPY data_files/ /app/data_files/

RUN mkdir -p /logs/artifacts && chmod -R a+rX /app/data_files
