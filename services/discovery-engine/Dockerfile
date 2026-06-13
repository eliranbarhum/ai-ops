FROM python:3.12-slim

RUN apt-get update && apt-get install -y --no-install-recommends \
    nmap \
    iproute2 \
    dnsutils \
    openssh-client \
    sshpass \
    curl \
    unzip \
    && rm -rf /var/lib/apt/lists/*

# Install nuclei and pre-download templates into the image
ARG NUCLEI_VERSION=3.3.9
RUN curl -sL "https://github.com/projectdiscovery/nuclei/releases/download/v${NUCLEI_VERSION}/nuclei_${NUCLEI_VERSION}_linux_amd64.zip" \
    -o /tmp/nuclei.zip \
    && unzip -q /tmp/nuclei.zip nuclei -d /usr/local/bin/ \
    && chmod +x /usr/local/bin/nuclei \
    && rm /tmp/nuclei.zip \
    && nuclei -update-templates -silent 2>/dev/null || true

WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY *.py .

RUN mkdir -p /data

CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8010"]
