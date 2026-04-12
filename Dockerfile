FROM python:3.12-slim

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

RUN mkdir -p /app/data

VOLUME ["/app/data", "/app/config"]

ENTRYPOINT ["python", "main.py"]
CMD ["daemon"]
