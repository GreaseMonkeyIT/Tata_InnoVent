# L3 correlation engine service. The code is the package correlation (pyproject.toml at the repo
# root): /app/correlation/ holds service.py and engine/, and the service runs as a module.
FROM python:3.12-slim
WORKDIR /app
COPY requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt
COPY __init__.py service.py ./correlation/
COPY engine/ ./correlation/engine/
EXPOSE 9100
USER 65534:65534
ENTRYPOINT ["python", "-m", "correlation.service"]
