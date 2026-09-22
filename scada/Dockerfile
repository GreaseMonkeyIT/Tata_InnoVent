# 2F.2 tag server. Same slim base and pymodbus pin as plant-sim.
# FLEET.md 8 adds the fleet registry (fleet.py) and the protocol drivers (drivers/).
FROM python:3.11-slim
WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY tags.py fleet.py tagserver.py ./
COPY drivers/ ./drivers/
EXPOSE 9300
CMD ["python", "-u", "tagserver.py"]
