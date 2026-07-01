FROM python:3.11-slim

# wkhtmltopdf — pdfkit'in PDF üretmesi için gerekli
# NOT: Debian bookworm'un apt deposundaki "wkhtmltopdf" paketi X11 sunucusu
# gerektiren eski/yamasız sürümdür ve --no-install-recommends ile bağımlılık
# çözümü başarısız olur (exit code 100). Bunun yerine kendi Qt'sini içeren
# resmi statik paketi (wkhtmltopdf/packaging) kuruyoruz — X11 gerektirmez.
RUN apt-get update && apt-get install -y --no-install-recommends \
    wget \
    ca-certificates \
    fontconfig \
    fonts-liberation \
    libjpeg62-turbo \
    libxrender1 \
    libxext6 \
    xfonts-75dpi \
    xfonts-base \
    && wget -q -O /tmp/wkhtmltox.deb \
       https://github.com/wkhtmltopdf/packaging/releases/download/0.12.6.1-3/wkhtmltox_0.12.6.1-3.bookworm_amd64.deb \
    && apt-get install -y --no-install-recommends /tmp/wkhtmltox.deb \
    && rm -f /tmp/wkhtmltox.deb \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

ENV PORT=8080
EXPOSE 8080

CMD ["gunicorn", "-b", "0.0.0.0:8080", "--workers", "1", "--threads", "4", "--timeout", "120", "app:app"]
