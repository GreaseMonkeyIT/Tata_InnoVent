# 2H virtual PLC runtime (FLEET.md section 6). Same slim base and pymodbus pin as plant-sim.
FROM python:3.11-slim
WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY *.py ./
COPY st/ ./st/
COPY tasks/ ./tasks/
# 102 = S7comm (siemens-s7-1200), 502 = Modbus TCP SCADA port, 5020 = field port, 8080 = control HTTP
EXPOSE 102 502 5020 8080
CMD ["python", "-u", "main.py"]
