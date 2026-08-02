FROM debian:trixie-slim

ARG MINIFORGE_VERSION=26.3.2-3
ARG MINIFORGE_SHA256=848194851a98903134187fbb4ab50efe87b003e0c0f808f97644b7524a62bf2c

ENV DEBIAN_FRONTEND=noninteractive \
    PATH=/opt/conda/bin:$PATH \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

RUN apt-get update && \
    apt-get install -y --no-install-recommends \
        bash \
        build-essential \
        ca-certificates \
        curl \
        git \
        gzip \
        jq \
        nodejs \
        npm \
        pigz \
        procps \
        ripgrep \
        tar \
        unzip \
        wget && \
    rm -rf /var/lib/apt/lists/*

RUN curl -fsSL \
        "https://github.com/conda-forge/miniforge/releases/download/${MINIFORGE_VERSION}/Miniforge3-${MINIFORGE_VERSION}-Linux-x86_64.sh" \
        -o /tmp/miniforge.sh && \
    echo "${MINIFORGE_SHA256}  /tmp/miniforge.sh" | sha256sum --check --strict && \
    bash /tmp/miniforge.sh -b -p /opt/conda && \
    rm /tmp/miniforge.sh && \
    printf 'channels:\n  - conda-forge\n  - bioconda\nchannel_priority: strict\n' \
        > /opt/conda/.condarc

# Scientific Python plus the command-line epigenomics tools used by the public tasks.
RUN conda install --yes --name base \
        --override-channels \
        --channel conda-forge \
        --channel bioconda \
        python=3.11 \
        pip \
        numpy \
        pandas \
        scipy \
        scikit-learn \
        statsmodels \
        lifelines \
        matplotlib \
        seaborn \
        h5py \
        pyarrow \
        biopython \
        pysam \
        pybedtools \
        pybigwig \
        pyfaidx \
        anndata \
        scanpy \
        samtools \
        bcftools \
        bedtools \
        htslib \
        tabix \
        homer \
        macs2=2.2.9.1 \
        macs3=3.0.4 \
        ucsc-bedgraphtobigwig \
        ucsc-bigwigtobedgraph \
        requests \
        tqdm && \
    conda clean --all --yes

RUN python -m pip install --no-cache-dir \
        MOODS-python==1.9.4.1 \
        gseapy==1.1.11 \
        ncls==0.0.70 \
        pyliftover==0.4.1 \
        pyranges==0.1.4 \
        sorted-nearest==0.0.41

RUN python -c "import Bio, MOODS, anndata, gseapy, h5py, lifelines, matplotlib, numpy, pandas, pyarrow, pybedtools, pyBigWig, pyfaidx, pyranges, pysam, scanpy, scipy, seaborn, sklearn, statsmodels" && \
    command -v bcftools bedGraphToBigWig bedtools bigWigToBedGraph findMotifsGenome.pl homer2 macs2 macs3 samtools tabix && \
    find /opt/conda -type f -path '*/knownTFs/known.motifs' -size +0c -print -quit | grep -q . && \
    macs2 --version && \
    macs3 --version && \
    samtools --version | head -n 1

RUN printf '%s\n' 'export PATH=/opt/conda/bin:$PATH' > /etc/profile.d/conda.sh

WORKDIR /app
COPY data/* /app/data/

RUN mkdir -p /logs/artifacts && chmod -R a+rX /app/data
