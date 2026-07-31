FROM python:3.13-slim

ARG PLINK2_VERSION=20260504
ARG PLINK2_SHA256=b8e68c8c06343a8b30c6a89a3488b05d6f2ea2f46749ad934a8966512da5d571

ENV DEBIAN_FRONTEND=noninteractive \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

RUN apt-get update && \
    apt-get install -y --no-install-recommends \
        bash \
        bedtools \
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
        "https://s3.amazonaws.com/plink2-assets/alpha7/plink2_linux_x86_64_${PLINK2_VERSION}.zip" \
        -o /tmp/plink2.zip && \
    echo "${PLINK2_SHA256}  /tmp/plink2.zip" | sha256sum --check --strict && \
    unzip -q /tmp/plink2.zip plink2 -d /usr/local/bin && \
    chmod 0755 /usr/local/bin/plink2 && \
    rm /tmp/plink2.zip

RUN python -m pip install --no-cache-dir \
        bed-reader==1.1.0 \
        lifelines==0.30.3 \
        matplotlib==3.11.1 \
        numpy==2.5.1 \
        pandas==2.3.3 \
        pyarrow==25.0.0 \
        pysam==0.24.0 \
        pysnptools==0.5.14 \
        scikit-learn==1.9.0 \
        seaborn==0.13.2 \
        scipy==1.18.0 \
        statsmodels==0.14.6

RUN python -c "import bed_reader, lifelines, matplotlib, numpy, pandas, pyarrow, pysam, pysnptools, scipy, seaborn, sklearn, statsmodels" && \
    bedtools --version && \
    plink2 --version

WORKDIR /app
COPY data_files/ /app/data_files/

RUN mkdir -p /logs/artifacts && chmod -R a+rX /app/data_files
