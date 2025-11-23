FROM python:3.9-slim

WORKDIR /app

COPY subscribers/autoscaler/requirements.txt ./requirements.txt
RUN pip install --no-cache-dir -r requirements.txt

COPY subscribers/autoscaler/ ./

COPY utils/ ./utils/

ENV PYTHONPATH=/app

CMD ["python", "autoscaler.py"]
