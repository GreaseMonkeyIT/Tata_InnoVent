# 2F.2 tag server — same slim pattern as plant-sim.
FROM python:3.11-alpine
WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY tags.py tagserver.py .
EXPOSE 9300
CMD ["python", "tagserver.py"]
