FROM bioconductor/bioconductor_docker:RELEASE_3_18@sha256:8c131494af2c1a4a8ff16656ac486e72fd0d1b135d4c06e54f17bf63cd3b86a3

ARG MINIFORGE_VERSION=26.3.2-2
ARG MINIFORGE_SHA256=42260ffe3830fb953d5eee1bbb32229ff06aa7c3833c1ed7a9a0420a95685d94
ARG NEXTFLOW_VERSION=26.04.6
ARG NEXTFLOW_SHA256=182a63c74074e2dc7956ffa3c8cd59de952ed2c44394e21faf5e1736b945444c

USER root

ENV DEBIAN_FRONTEND=noninteractive \
    PATH=/opt/conda/envs/biomystery/bin:/opt/uv-python/bin:/opt/conda/bin:$PATH \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

RUN apt-get update && \
    apt-get install -y --no-install-recommends \
        bash \
        bcftools \
        bedtools \
        bowtie2 \
        build-essential \
        bwa \
        ca-certificates \
        curl \
        fastqc \
        git \
        jq \
        libcairo2 \
        libcairo2-dev \
        libgl1 \
        libglu1-mesa \
        ncbi-blast+ \
        nodejs \
        npm \
        procps \
        python3 \
        python3-pip \
        ripgrep \
        samtools \
        seqtk \
        tabix \
        unzip \
        vim \
        wget \
        zip && \
    apt-get clean && \
    rm -rf /var/lib/apt/lists/*

RUN curl -fsSL \
        "https://github.com/conda-forge/miniforge/releases/download/${MINIFORGE_VERSION}/Miniforge3-${MINIFORGE_VERSION}-Linux-x86_64.sh" \
        -o /tmp/miniforge.sh && \
    echo "${MINIFORGE_SHA256}  /tmp/miniforge.sh" | sha256sum --check --strict && \
    bash /tmp/miniforge.sh -b -p /opt/conda && \
    rm /tmp/miniforge.sh && \
    printf 'channels:\n  - conda-forge\n  - bioconda\nchannel_priority: strict\n' \
        > /opt/conda/.condarc && \
    mamba create --yes --name biomystery python=3.11 openjdk=21 pip && \
    conda clean --all --yes

RUN python -m pip install --no-cache-dir \
        biopython==1.87 \
        h5py==3.16.0 \
        jupyterlab==4.6.2 \
        matplotlib==3.11.1 \
        networkx==3.6.1 \
        numpy==2.4.6 \
        pandas==3.0.5 \
        plotly==6.9.0 \
        pysam==0.24.0 \
        pyvcf3==1.0.4 \
        scikit-learn==1.9.0 \
        scipy==1.17.1 \
        seaborn==0.13.2 \
        statsmodels==0.14.6 \
        uv==0.12.1

RUN uv python install 3.12 && \
    uv venv --python 3.12 /opt/uv-python && \
    uv pip install --python /opt/uv-python/bin/python \
        harbor-rewardkit==0.1.7 \
        matplotlib==3.11.1 \
        numpy==2.5.1 \
        pandas==3.0.5 \
        scikit-image==0.26.0 \
        scikit-learn==1.9.0 \
        scipy==1.18.0

ENV UV_PYTHON=/opt/uv-python/bin/python

RUN R --quiet -e 'options(Ncpus = 4); BiocManager::install(c("tidyverse", "DESeq2", "edgeR", "limma", "GenomicRanges", "clusterProfiler", "enrichplot", "GO.db", "tximport", "Biostrings", "org.Hs.eg.db", "org.Mm.eg.db", "lionessR"), ask = FALSE, update = FALSE)'

RUN curl -fsSL \
        "https://github.com/nextflow-io/nextflow/releases/download/v${NEXTFLOW_VERSION}/nextflow-${NEXTFLOW_VERSION}-dist" \
        -o /usr/local/bin/nextflow && \
    echo "${NEXTFLOW_SHA256}  /usr/local/bin/nextflow" | sha256sum --check --strict && \
    chmod 0755 /usr/local/bin/nextflow

RUN python -c "import Bio, h5py, jupyterlab, matplotlib, networkx, numpy, pandas, plotly, pysam, scipy, seaborn, sklearn, statsmodels, vcf" && \
    /opt/uv-python/bin/python -c "import matplotlib, numpy, pandas, scipy, skimage, sklearn" && \
    R --quiet -e 'stopifnot(getRversion() >= "4.3", getRversion() < "4.4"); invisible(lapply(c("tidyverse", "DESeq2", "edgeR", "limma", "GenomicRanges", "clusterProfiler", "enrichplot", "GO.db", "tximport", "Biostrings", "org.Hs.eg.db", "org.Mm.eg.db", "lionessR"), requireNamespace, quietly = FALSE))' && \
    blastn -version && \
    bcftools --version | head -n 1 && \
    bedtools --version && \
    bowtie2 --version | head -n 1 && \
    command -v bwa fastqc samtools seqtk && \
    java -version && \
    nextflow -version && \
    node --version && \
    uv --version

WORKDIR /app
COPY task_files/ /app/data_files/

RUN mkdir -p /logs/artifacts && chmod -R a+rX /app/data_files
