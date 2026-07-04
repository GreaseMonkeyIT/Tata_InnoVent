FROM python:3.12-slim
RUN apt-get update && apt-get install -y --no-install-recommends fio && rm -rf /var/lib/apt/lists/*
WORKDIR /app
COPY main.py .
ENTRYPOINT ["python", "-u", "main.py"]
