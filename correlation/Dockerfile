# L3 correlation engine service (P4).
FROM python:3.12-slim
WORKDIR /app
COPY requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt
COPY engine/ ./engine/
COPY service.py ./
EXPOSE 9100
USER 65534:65534
ENTRYPOINT ["python", "service.py"]
