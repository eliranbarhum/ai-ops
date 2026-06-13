FROM python:3.12-slim-bookworm

# Install PowerShell (Microsoft packages target Debian 12 / Bookworm)
RUN apt-get update && apt-get install -y --no-install-recommends \
    wget apt-transport-https ca-certificates gnupg \
    && wget -q "https://packages.microsoft.com/config/debian/12/packages-microsoft-prod.deb" \
    && dpkg -i packages-microsoft-prod.deb && rm packages-microsoft-prod.deb \
    && apt-get update && apt-get install -y --no-install-recommends powershell \
    && apt-get clean && rm -rf /var/lib/apt/lists/*

# Install VMware.PowerCLI
RUN pwsh -NonInteractive -NoProfile -Command \
    "Set-PSRepository -Name PSGallery -InstallationPolicy Trusted; \
     Install-Module -Name VMware.PowerCLI -RequiredVersion 13.3.0.24145081 -Force -AllowClobber -Scope AllUsers"

# Configure CEIP in a fresh session (module must be loaded first)
RUN pwsh -NonInteractive -NoProfile -Command \
    "Import-Module VMware.VimAutomation.Core; \
     Set-PowerCLIConfiguration -Scope AllUsers -ParticipateInCeip:\$false -InvalidCertificateAction Ignore -Confirm:\$false | Out-Null"

WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY . .

EXPOSE 8010
CMD ["uvicorn", "app:app", "--host", "0.0.0.0", "--port", "8010"]
