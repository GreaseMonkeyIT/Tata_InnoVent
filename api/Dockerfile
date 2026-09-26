FROM python:3.12-slim
WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
# main.py imports security.py (2E gate and audit ledger), fleet.py (2H), integrity.py (SCENARIOS.md 5.2),
# metrics.py (LOG-088), incident.py (LOG-092), and narrator.py, advice.py, agent.py (LOG-093). A module
# missing here crashes the pod at import (forge, 2026-09-26: "No module named 'advice'").
COPY main.py security.py fleet.py integrity.py metrics.py incident.py narrator.py advice.py agent.py drift.py ./
EXPOSE 8088
CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8088"]
