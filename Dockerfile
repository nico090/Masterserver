FROM python:3.12-slim

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

EXPOSE 8000
EXPOSE 7770-7870/tcp
EXPOSE 7770-7870/udp

CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8000"]
