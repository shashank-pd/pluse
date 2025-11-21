FROM python:3.9-slim
WORKDIR /app
COPY aggregator/requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY aggregator/aggregator.py .
CMD ["python", "aggregator.py"]
