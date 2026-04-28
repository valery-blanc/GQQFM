FROM python:3.11-slim

RUN apt-get update && apt-get install -y git && rm -rf /var/lib/apt/lists/*

WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY collector.py api.py main.py ./

# Le repo GQQFM est cloné dans /repo (volume ou clone au build)
# Les données collectées vont dans /data (volume Docker)
VOLUME ["/data"]

ENV REPO_DIR=/repo
ENV DATA_DIR=/data
ENV PORT=8502

CMD ["python", "main.py"]
