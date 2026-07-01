FROM python:3.11-slim

# wkhtmltopdf — pdfkit'in PDF üretmesi için gerekli
RUN apt-get update && apt-get install -y --no-install-recommends \
    wkhtmltopdf \
    fonts-liberation \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

ENV PORT=8080
EXPOSE 8080

CMD ["gunicorn", "-b", "0.0.0.0:8080", "--workers", "1", "--threads", "4", "--timeout", "120", "app:app"]
