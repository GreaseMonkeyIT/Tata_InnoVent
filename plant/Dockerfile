# plant-sim: stdlib Python + pymodbus (the ONE dep — the 2F PLC field wiring; the sim runs
# open-loop without it, so a stdlib-only rebuild still works if the wheel is unavailable).
FROM python:3.11-slim
RUN pip install --no-cache-dir pymodbus==3.6.9
WORKDIR /app
COPY sim/main.py .
EXPOSE 9200
CMD ["python", "-u", "main.py"]
