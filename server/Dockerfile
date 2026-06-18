FROM python:3.12-slim

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY server.py .

ENV HOST=0.0.0.0
ENV PORT=8080
ENV CO2_DATA_DIR=/data/latest

EXPOSE 8080

CMD ["python", "server.py"]
