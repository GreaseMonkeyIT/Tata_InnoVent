# 2F.2 tag server. Same slim base and pymodbus pin as plant-sim.
# FLEET.md 8 adds the fleet registry (fleet.py) and the protocol drivers (drivers/).
FROM python:3.11-slim
WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY tags.py fleet.py tagserver.py ./
COPY drivers/ ./drivers/
# Register maps, one YAML per vendor/model (drivers/regmap.py). REGMAP_DIR overrides the folder.
COPY regmaps/ ./regmaps/
EXPOSE 9300
CMD ["python", "-u", "tagserver.py"]
