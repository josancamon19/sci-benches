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
        jq \
        procps \
        ripgrep \
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

# Match the bare-minimum environment published with CompBioBench.
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
        matplotlib \
        seaborn \
        biopython \
        pysam \
        pybedtools \
        samtools \
        bcftools \
        bedtools \
        tabix \
        blast \
        minimap2 \
        bwa \
        cyvcf2 \
        vcftools \
        gffutils \
        htseq \
        subread \
        cooler \
        cooltools \
        htslib \
        requests \
        tqdm \
        ipython \
        nodejs && \
    conda clean --all --yes

RUN python -m pip install --no-cache-dir \
        pyBigWig==0.3.25 \
        pyliftover==0.4.1

RUN python -c "import Bio, cooler, cooltools, cyvcf2, gffutils, HTSeq, matplotlib, numpy, pandas, pybedtools, pyBigWig, pyliftover, pysam, requests, scipy, seaborn, sklearn, tqdm" && \
    command -v bcftools bedtools blastn bwa cooler featureCounts minimap2 node npm samtools tabix vcftools && \
    python --version && \
    samtools --version | head -n 1 && \
    node --version

RUN printf '%s\n' 'export PATH=/opt/conda/bin:$PATH' > /etc/profile.d/conda.sh

WORKDIR /app
COPY task_files/ /app/

RUN mkdir -p /logs/artifacts && chmod -R a+rX /app
