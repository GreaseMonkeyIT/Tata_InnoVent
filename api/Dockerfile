FROM python:3.12-slim
WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
# main.py imports security.py (2E gate and audit ledger), fleet.py (2H), and integrity.py
# (SCENARIOS.md 5.2). Copy all four.
COPY main.py security.py fleet.py integrity.py ./
EXPOSE 8088
CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8088"]
