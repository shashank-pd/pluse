FROM python:3.9-slim
WORKDIR /app
COPY docker/fake_node.py .
RUN pip install --no-cache-dir google-cloud-pubsub==2.18.4
CMD ["python", "fake_node.py"]
